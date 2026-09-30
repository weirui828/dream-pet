"""Generative-Agents-style retrieval: score = w_r·recency + w_i·importance + w_s·similarity."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np

from dreampet.memory.repo import Memory

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext


def retrieve(ctx: PetContext, query: str | np.ndarray, k: int = 5, kinds: Sequence[str] | None = None,
             candidates: int = 40, touch: bool = True) -> list[tuple[Memory, float]]:
    vec = ctx.providers.embeddings.embed_one(query) if isinstance(query, str) else query
    cands = ctx.repo.knn(ctx.pet_id, vec, candidates, kinds=kinds,
                         embed_model=ctx.providers.embeddings.model_name)
    if not cands:
        return []
    mc = ctx.cfg.memory
    now = ctx.now()
    scored = []
    for m, dist in cands:
        last = m.last_accessed_at or m.created_at
        hours = max(0.0, (now - last).total_seconds() / 3600)
        recency = mc.recency_base ** hours
        similarity = max(0.0, 1.0 - dist)
        score = mc.w_recency * recency + mc.w_importance * m.importance + mc.w_similarity * similarity
        scored.append((m, round(score, 5)))
    scored.sort(key=lambda x: (-x[1], x[0].id))
    top = scored[:k]
    if touch:
        ctx.repo.touch([m.id for m, _ in top], now)
    return top
