"""Traits -> parameters. Sliders modulate the base drive params (from drives.yaml), then the
owner's advanced overrides win. Everything is clamped to bounds."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from dreampet.drives.params import DriveParams, apply_overrides, clamp_to_bounds
from dreampet.persona.model import Persona


class Compiled(BaseModel):
    drives: DriveParams
    dreamer_temperature: float
    walk_hops: int
    proactive_per_day: int
    silence_hours: float
    trace: dict[str, str]  # param -> which trait moved it (for the dashboard)


def _x(v: int) -> float:
    """Map 0..100 to -1..1 centred on 50."""
    return (v - 50) / 50.0


def compile_persona(persona: Persona, base: DriveParams, overrides: dict[str, Any] | None = None) -> Compiled:
    t = persona.traits
    f = base.flat()
    trace: dict[str, str] = {}

    r = _x(t.restlessness)
    f["boredom.growth_rate"] *= 2 ** (1.2 * r)
    f["boredom.theta_high"] -= 0.1 * r
    trace["boredom.growth_rate"] = trace["boredom.theta_high"] = "restlessness"

    o = _x(t.openness)
    f["curiosity.epsilon"] += 0.08 * o
    f["curiosity.novelty_k"] = round(f["curiosity.novelty_k"] * (1 + 0.5 * o))
    trace["curiosity.epsilon"] = trace["curiosity.novelty_k"] = "openness"

    d = _x(t.depth)
    f["curiosity.tau"] *= 2 ** (-d)
    f["curiosity.habituation"] += 0.1 * d
    trace["curiosity.tau"] = trace["curiosity.habituation"] = "depth"

    s = _x(t.sociability)
    f["boredom.chat_weight"] *= 1 + 0.8 * s
    trace["boredom.chat_weight"] = "sociability"

    st = _x(t.stamina)
    f["energy.max"] *= 1 + 0.5 * st
    f["energy.recovery_per_sleep_hour"] *= 1 + 0.4 * st
    trace["energy.max"] = trace["energy.recovery_per_sleep_hour"] = "stamina"

    f = clamp_to_bounds(f)
    f["curiosity.novelty_k"] = int(f["curiosity.novelty_k"])
    # keep the hysteresis band valid after modulation
    if f["boredom.theta_high"] <= f["boredom.theta_low"] + 0.1:
        f["boredom.theta_high"] = min(0.95, f["boredom.theta_low"] + 0.15)

    params = apply_overrides(DriveParams(), f)
    if overrides:
        params = apply_overrides(params, overrides)
        for k in _flatten_keys(overrides):
            trace[k] = "override"

    return Compiled(
        drives=params,
        dreamer_temperature=round(0.6 + 0.5 * t.dreaminess / 100, 3),
        walk_hops=1 + round(3 * t.dreaminess / 100),
        proactive_per_day=max(0, min(8, round(3 + 2.5 * s))),
        silence_hours=round(24 - 16 * t.sociability / 100, 1),
        trace=trace,
    )


def _flatten_keys(d: dict[str, Any]) -> list[str]:
    out = []
    for k, v in d.items():
        if isinstance(v, dict):
            out.extend(f"{k}.{kk}" for kk in v)
        else:
            out.append(k)
    return out
