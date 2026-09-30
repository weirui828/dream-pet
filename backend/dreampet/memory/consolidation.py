"""Sleep-time consolidation: reflect, merge, decay (archive, never delete)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from dreampet import prompts
from dreampet.memory.clustering import cos_dist
from dreampet.memory.repo import Memory
from dreampet.providers.base import ChatRequest
from dreampet.schemas import Insights

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext


def reflect(ctx: PetContext, day: list[Memory]) -> list[str]:
    """Summarize the day's episodic memories into insights, linked with derived_from."""
    episodic = [m for m in day if m.kind in ("episodic", "chat")]
    if len(episodic) < 2:
        return []
    labels = {c.id: c.label for c in ctx.repo.list_clusters(ctx.pet_id)}
    groups: dict[str, list[Memory]] = {}
    for m in episodic:
        groups.setdefault(m.cluster_id or "chat", []).append(m)
    group_list = [
        {"label": labels.get(cid, "conversations with my owner" if cid == "chat" else "things"),
         "memories": [{"id": m.id, "title": m.title, "content": m.content} for m in ms]}
        for cid, ms in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    req = ChatRequest(
        task="reflect",
        system=prompts.render(prompts.REFLECT_SYSTEM, ctx),
        user=prompts.reflect_user(group_list),
        context={"groups": group_list},
    )
    out: Insights = ctx.providers.dreamer.structured(req, Insights)
    valid = {m.id for m in episodic}
    ids = []
    for ins in out.insights:
        cited = [i for i in ins.memory_ids if i in valid]
        if not cited:
            continue
        vec = ctx.providers.embeddings.embed_one(ins.text)
        src = ctx.repo.get_memories(cited)
        mid = ctx.new_id("m")
        ctx.repo.add_memory(Memory(
            id=mid, pet_id=ctx.pet_id, kind="insight", content=ins.text, created_at=ctx.now(),
            title="Insight", embedding=vec, embed_model=ctx.providers.embeddings.model_name,
            importance=min(1.0, max(m.importance for m in src) + 0.1), strength=1.0,
            cluster_id=src[0].cluster_id if src else None, meta={"derived_from": cited},
        ))
        for c in cited:
            ctx.repo.add_link(mid, c, "derived_from")
        ids.append(mid)
    return ids


def merge_duplicates(ctx: PetContext, day: list[Memory]) -> list[tuple[str, str]]:
    """Near-duplicates (cosine > 0.95) are merged into the older memory; strength is summed."""
    merged = []
    gone: set[str] = set()
    model = ctx.providers.embeddings.model_name
    for m in day:
        if m.id in gone or m.embedding is None or m.kind not in ("episodic", "semantic", "insight"):
            continue
        for other, d in ctx.repo.knn(ctx.pet_id, m.embedding, 4, embed_model=model, exclude_ids=[m.id],
                                     kinds=[m.kind]):
            if other.id in gone or d > ctx.cfg.memory.duplicate_distance:
                continue
            keep, drop = (other, m) if (other.created_at, other.id) <= (m.created_at, m.id) else (m, other)
            ctx.repo.update_memory(keep.id, strength=keep.strength + drop.strength,
                                   importance=max(keep.importance, drop.importance),
                                   access_count=keep.access_count + drop.access_count,
                                   meta={**keep.meta, "merged": keep.meta.get("merged", []) + [drop.id]})
            ctx.repo.relink(drop.id, keep.id)
            ctx.repo.update_memory(drop.id, archived=True, meta={**drop.meta, "merged_into": keep.id})
            gone.add(drop.id)
            merged.append((keep.id, drop.id))
            if drop.id == m.id:
                break
    return merged


def decay(ctx: PetContext, since: datetime) -> list[str]:
    """strength ×= decay unless accessed since `since` or linked to a dream; archive below floor."""
    cfg = ctx.cfg.memory
    archived = []
    for m in ctx.repo.list_memories(ctx.pet_id, kinds=["episodic", "semantic", "insight", "chat"], limit=100000):
        if m.last_accessed_at and m.last_accessed_at >= since:
            continue
        if ctx.repo.has_link_kind(m.id, "dream_used"):
            continue
        new = round(m.strength * cfg.decay, 5)
        if new < cfg.archive_floor:
            ctx.repo.update_memory(m.id, strength=new, archived=True)
            archived.append(m.id)
        else:
            ctx.repo.update_memory(m.id, strength=new)
    return archived


def near(a, b) -> float:
    return cos_dist(a, b)
