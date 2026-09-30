"""Dream weaving: seed selection, associative walk, and the grounding critic's checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dreampet.memory.clustering import cos_dist
from dreampet.memory.repo import Memory
from dreampet.text import key_phrases, longest_shared_run, truncate_words

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext

DREAMABLE = ("episodic", "chat", "insight", "semantic")
BAND = (0.3, 0.6)  # prefer moderately distant neighbours: surprising but related
MAX_VERBATIM_WORDS = 12


def seed_weight(m: Memory) -> float:
    """importance × surprise × emotional salience (with floors so nothing is impossible)."""
    return max(1e-3, m.importance) * (0.2 + m.surprise) * (0.2 + m.salience)


def select_seeds(ctx: PetContext, pool: list[Memory], n_min: int = 3, n_max: int = 6) -> list[Memory]:
    pool = [m for m in pool if m.kind in DREAMABLE]
    if not pool:
        return []
    n = min(len(pool), ctx.rng.randint(n_min, n_max))
    chosen: list[Memory] = []
    candidates = list(pool)
    for _ in range(n):
        weights = [seed_weight(m) for m in candidates]
        u = ctx.rng.random() * sum(weights)
        for i, w in enumerate(weights):
            u -= w
            if u <= 0:
                chosen.append(candidates.pop(i))
                break
        else:
            chosen.append(candidates.pop())
    return chosen


def associative_walk(ctx: PetContext, seeds: list[Memory], hops: int, cap: int = 9) -> list[Memory]:
    """From each seed take `hops` steps along memory_links and embedding neighbours, preferring
    nodes at cosine distance 0.3–0.6 from the current node."""
    picked: dict[str, Memory] = {m.id: m for m in seeds}
    model = ctx.providers.embeddings.model_name
    for seed in seeds:
        cur = seed
        for _ in range(hops):
            if len(picked) >= cap or cur.embedding is None:
                break
            linked_ids = []
            for lk in ctx.repo.links([cur.id]):
                other = lk["dst_id"] if lk["src_id"] == cur.id else lk["src_id"]
                if other not in picked:
                    linked_ids.append(other)
            cands = [m for m in ctx.repo.get_memories(linked_ids) if m.kind in DREAMABLE and not m.archived]
            for m, _ in ctx.repo.knn(ctx.pet_id, cur.embedding, 12, kinds=list(DREAMABLE), embed_model=model,
                                     exclude_ids=list(picked)):
                if m.id not in {c.id for c in cands}:
                    cands.append(m)
            cands = [c for c in cands if c.id not in picked and c.embedding is not None]
            if not cands:
                break
            in_band = [c for c in cands if BAND[0] <= cos_dist(cur.embedding, c.embedding) <= BAND[1]]
            pool = in_band or cands
            nxt = pool[ctx.rng.randrange(len(pool))]
            picked[nxt.id] = nxt
            cur = nxt
    return list(picked.values())


def fragment(m: Memory) -> dict[str, Any]:
    return {"id": m.id, "title": m.title, "kind": m.kind, "content": truncate_words(m.content, 80),
            "phrases": key_phrases(m.content if m.kind in ("insight", "chat") else f"{m.title or ''}. {m.content}", 4)}


def critique(draft: dict[str, Any], valid_ids: set[str], sources: dict[str, str]) -> dict[str, Any]:
    """Grounding and shape checks. Returns {ok, issues, grounded_ratio, max_verbatim}."""
    issues = []
    els = draft.get("elements") or []
    if not (3 <= len(els) <= 6):
        issues.append(f"needs 3-6 scenes, got {len(els)}")
    if not (draft.get("mood") or "").strip():
        issues.append("no mood")
    grounded = 0
    for i, el in enumerate(els):
        ids = el.get("memory_ids") or []
        bad = [x for x in ids if x not in valid_ids]
        if not ids:
            issues.append(f"scene {i} cites no memory")
        elif bad:
            issues.append(f"scene {i} cites unknown ids {bad}")
        else:
            grounded += 1
    words = len((draft.get("narrative") or "").split())
    if words < 120 or words > 450:
        issues.append(f"narrative is {words} words (want 150-400)")
    texts = [draft.get("narrative") or ""] + [el.get("text", "") for el in els]
    max_run = 0
    for src in sources.values():
        for t in texts:
            max_run = max(max_run, longest_shared_run(t, src, max_n=MAX_VERBATIM_WORDS + 5))
    if max_run > MAX_VERBATIM_WORDS:
        issues.append(f"copies {max_run} words verbatim from a source")
    return {"ok": not issues, "issues": issues, "grounded_ratio": grounded / max(1, len(els)),
            "max_verbatim": max_run}


def repair(draft: dict[str, Any], valid_ids: set[str]) -> dict[str, Any]:
    """For a weak dream: keep only valid citations and drop scenes left with none."""
    els = []
    for el in draft.get("elements") or []:
        ids = [x for x in el.get("memory_ids") or [] if x in valid_ids]
        if ids:
            els.append({**el, "memory_ids": ids})
    return {**draft, "elements": els}
