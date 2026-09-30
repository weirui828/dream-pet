"""Persona drift: at most ±3 points per night per field, must cite memories, respects locks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dreampet.persona.model import TRAIT_NAMES, Interest, Persona
from dreampet.schemas import DriftProposal

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext

MAX_STEP = 3.0
MAX_INTERESTS = 12


def apply_drift(ctx: PetContext, proposal: DriftProposal, valid_ids: set[str]) -> list[dict[str, Any]]:
    persona = Persona.model_validate(ctx.persona.model_dump())
    applied: list[dict[str, Any]] = []
    touched: set[str] = set()
    for ch in proposal.changes:
        field = ch.field.strip()
        if field in touched:
            continue
        cited = [m for m in ch.memory_ids if m in valid_ids]
        if not cited:
            ctx.emit("drift_rejected", {"field": field, "why": "no valid memory citations"})
            continue
        if persona.is_locked(field):
            ctx.emit("drift_rejected", {"field": field, "why": "locked"})
            continue
        delta = max(-MAX_STEP, min(MAX_STEP, float(ch.delta)))
        if delta == 0:
            continue
        group, _, name = field.partition(".")
        if group == "traits" and name in TRAIT_NAMES:
            old = getattr(persona.traits, name)
            new = int(max(0, min(100, round(old + delta))))
            if new == old:
                continue
            setattr(persona.traits, name, new)
        elif group == "interests" and name:
            existing = next((i for i in persona.interests if i.topic.lower() == name.lower()), None)
            if existing is None:
                if delta < 0 or len(persona.interests) >= MAX_INTERESTS:
                    continue
                old = 0.0
                new = round(delta, 2)
                persona.interests.append(Interest(topic=name, weight=new))
            else:
                old = existing.weight
                new = round(max(0.0, min(100.0, old + delta)), 2)
                if new == old:
                    continue
                existing.weight = new
        else:
            ctx.emit("drift_rejected", {"field": field, "why": "unknown field"})
            continue
        touched.add(field)
        ctx.repo.add_persona_change(ctx.pet_id, ctx.now(), field, old, new, ch.reason, cited)
        applied.append({"field": field, "old": old, "new": new, "reason": ch.reason, "memory_ids": cited})
    if applied:
        ctx.save_persona(persona)
    return applied
