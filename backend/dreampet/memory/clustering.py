"""Topic clusters: incremental assignment on write, a re-run every night, and learning progress."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from dreampet.drives.signals import lp_with_prior
from dreampet.memory.repo import Cluster

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext

CLUSTERED_KINDS = ("episodic", "semantic")


def _norm(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n else v


def cos_dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(1.0 - np.dot(_norm(a), _norm(b)))


def nearest_cluster(clusters: list[Cluster], vec: np.ndarray) -> tuple[Cluster | None, float]:
    best, best_d = None, 2.0
    for c in clusters:
        if c.centroid is None or c.centroid.shape != vec.shape:
            continue
        d = cos_dist(c.centroid, vec)
        if d < best_d:
            best, best_d = c, d
    return best, best_d


def seed_clusters(ctx: PetContext) -> None:
    """Initial clusters from the persona's interests (they also act as wildcard sources)."""
    existing = {c.label.lower() for c in ctx.repo.list_clusters(ctx.pet_id)}
    topics = [i.topic for i in ctx.persona.interests if i.topic.lower() not in existing]
    if not topics:
        return
    vecs = ctx.providers.embeddings.embed(topics)
    for topic, v in zip(topics, vecs, strict=True):
        ctx.repo.upsert_cluster(Cluster(
            id=ctx.new_id("cl"), pet_id=ctx.pet_id, label=topic, centroid=v, error_history=[],
            lp=lp_with_prior([], ctx.params.curiosity.lp_window), visits=0, size=0, seed=True, created_at=ctx.now(),
        ))


def assign(ctx: PetContext, vec: np.ndarray, label_hint: str) -> Cluster:
    """Join the nearest cluster within the join distance, or start a new one."""
    clusters = ctx.repo.list_clusters(ctx.pet_id)
    best, d = nearest_cluster(clusters, vec)
    if best is not None and d <= ctx.cfg.memory.cluster_join_distance:
        n = best.size
        best.centroid = _norm((best.centroid * max(n, 1) + vec) / (max(n, 1) + 1)).astype(np.float32)
        best.size = n + 1
        ctx.repo.upsert_cluster(best)
        return best
    c = Cluster(id=ctx.new_id("cl"), pet_id=ctx.pet_id, label=label_hint, centroid=vec, error_history=[],
                lp=lp_with_prior([], ctx.params.curiosity.lp_window), visits=0, size=1, seed=False,
                created_at=ctx.now())
    ctx.repo.upsert_cluster(c)
    ctx.emit("cluster_new", {"cluster_id": c.id, "label": c.label})
    return c


def record_error(ctx: PetContext, cluster: Cluster, error: float) -> Cluster:
    cluster.error_history = (list(cluster.error_history) + [round(float(error), 4)])[-50:]
    cluster.lp = lp_with_prior(cluster.error_history, ctx.params.curiosity.lp_window)
    cluster.visits += 1
    ctx.repo.upsert_cluster(cluster)
    return cluster


def recluster(ctx: PetContext) -> dict:
    """Nightly re-run: reassign every memory to its nearest centroid (or a new cluster),
    recompute centroids, merge near-identical clusters, drop empty non-seed clusters and
    recompute learning progress. Cluster ids stay stable so error histories survive."""
    repo, cfg = ctx.repo, ctx.cfg.memory
    embed_model = ctx.providers.embeddings.model_name
    mems = repo.list_memories(ctx.pet_id, kinds=CLUSTERED_KINDS, limit=100000, newest_first=False)
    mems = [m for m in mems if m.embedding is not None and m.embed_model == embed_model]
    clusters = {c.id: c for c in repo.list_clusters(ctx.pet_id)}
    moved = created = merged = removed = 0

    for _ in range(2):
        members: dict[str, list] = {cid: [] for cid in clusters}
        for m in mems:
            best, d = nearest_cluster(list(clusters.values()), m.embedding)
            if best is None or d > cfg.cluster_join_distance:
                c = Cluster(id=ctx.new_id("cl"), pet_id=ctx.pet_id, label=m.title or m.content[:40], centroid=m.embedding,
                            error_history=[], lp=0.0, visits=0, size=0, seed=False, created_at=ctx.now())
                clusters[c.id] = c
                members[c.id] = []
                best = c
                created += 1
            members[best.id].append(m)
            if m.cluster_id != best.id:
                moved += 1
                m.cluster_id = best.id
        for cid, ms in members.items():
            c = clusters[cid]
            if ms:
                mean = np.mean([x.embedding for x in ms], axis=0)
                if c.seed and c.centroid is not None and c.centroid.shape == mean.shape:
                    mean = 0.8 * mean + 0.2 * c.centroid  # seeds keep a pull toward the stated interest
                c.centroid = _norm(mean).astype(np.float32)
            c.size = len(ms)

    # merge clusters whose centroids nearly coincide (keep the older id)
    ids = sorted(clusters, key=lambda i: (clusters[i].created_at, i))
    alive = set(ids)
    for i, a in enumerate(ids):
        if a not in alive:
            continue
        for b in ids[i + 1:]:
            if b not in alive:
                continue
            ca, cb = clusters[a], clusters[b]
            if ca.centroid is None or cb.centroid is None or ca.centroid.shape != cb.centroid.shape:
                continue
            if cos_dist(ca.centroid, cb.centroid) < cfg.cluster_merge_distance:
                ca.error_history = (ca.error_history + cb.error_history)[-50:]
                ca.visits += cb.visits
                ca.size += cb.size
                ca.seed = ca.seed or cb.seed
                for m in mems:
                    if m.cluster_id == b:
                        m.cluster_id = a
                alive.discard(b)
                merged += 1

    for m in mems:
        repo.update_memory(m.id, cluster_id=m.cluster_id)
    for cid in list(clusters):
        c = clusters[cid]
        if cid not in alive or (c.size == 0 and not c.seed):
            if repo.get_cluster(cid):
                repo.delete_cluster(cid)
            removed += cid in alive
            continue
        # relabel non-seed clusters by their most central member
        if not c.seed and c.size:
            ms = [m for m in mems if m.cluster_id == cid]
            central = min(ms, key=lambda m: cos_dist(m.embedding, c.centroid))
            c.label = central.title or c.label
        c.lp = lp_with_prior(c.error_history, ctx.params.curiosity.lp_window)
        repo.upsert_cluster(c)
    return {"moved": moved, "created": created, "merged": merged, "removed": removed,
            "clusters": len(alive)}


def topic_entropy(ctx_or_repo, pet_id: str) -> float:
    """Diversity of what the pet has read: Shannon entropy (bits) over cluster sizes."""
    repo = getattr(ctx_or_repo, "repo", ctx_or_repo)
    sizes = [n for n in repo.cluster_sizes(pet_id).values() if n > 0]
    total = sum(sizes)
    if not total:
        return 0.0
    ps = [n / total for n in sizes]
    return float(-sum(p * np.log2(p) for p in ps))
