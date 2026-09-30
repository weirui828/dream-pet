"""HybridLPBoredom: the default drive model.

Boredom is a leaky integrator  dB/dt = g (1 − B) − Σ s(e).  The growth term is integrated
exactly over dt; each event subtracts its satisfaction. Energy pays for actions and recovers
while resting.
"""

from __future__ import annotations

import math
from datetime import timedelta

from dreampet.drives.base import DriveState, Event, Lifecycle
from dreampet.drives.params import DriveParams

KIND_WEIGHT_KEYS = {"read": "read_weight", "chat": "chat_weight"}


class HybridLPBoredom:
    name = "hybrid_lp"

    def __init__(self, params: DriveParams, nap_recovery_factor: float = 0.6):
        self.params = params
        self.nap_recovery_factor = nap_recovery_factor

    def kind_weight(self, kind: str) -> float:
        key = KIND_WEIGHT_KEYS.get(kind)
        return getattr(self.params.boredom, key) if key else 0.0

    def satisfaction(self, e: Event) -> float:
        p = e.payload
        if "novelty" not in p:
            return 0.0
        return (
            self.kind_weight(e.type)
            * float(p["novelty"])
            * float(p.get("learnability", 1.0))
            * float(p.get("habituation", 1.0))
        )

    def update(self, state: DriveState, events: list[Event], dt: timedelta) -> DriveState:
        p = self.params
        hours = max(0.0, dt.total_seconds() / 3600)
        s = state.model_copy()

        # boredom only grows while awake; sleep resets the pressure a little each hour
        if s.lifecycle in Lifecycle.AWAKE:
            s.boredom = 1 - (1 - s.boredom) * math.exp(-p.boredom.growth_rate * hours)
        elif s.lifecycle == Lifecycle.NAPPING:
            s.boredom = 1 - (1 - s.boredom) * math.exp(-0.5 * p.boredom.growth_rate * hours)
        else:
            s.boredom = s.boredom * math.exp(-0.05 * hours)

        for e in events:
            sat = self.satisfaction(e)
            s.boredom -= sat
            s.satisfaction_today += sat
            s.energy -= e.energy_cost

        if s.lifecycle in (Lifecycle.ASLEEP, Lifecycle.DREAMING):
            s.energy += p.energy.recovery_per_sleep_hour * hours
        elif s.lifecycle == Lifecycle.NAPPING:
            s.energy += p.energy.recovery_per_sleep_hour * self.nap_recovery_factor * hours

        s.boredom = min(1.0, max(0.0, s.boredom))
        s.energy = min(p.energy.max, max(0.0, s.energy))
        return s

    def wants_to_explore(self, s: DriveState) -> bool:
        return s.boredom > self.params.boredom.theta_high and s.energy > self.params.energy.nap_below

    def should_stop_exploring(self, s: DriveState) -> bool:
        return s.boredom < self.params.boredom.theta_low or s.energy <= self.params.energy.nap_below

    def explain(self, state: DriveState) -> dict:
        b = self.params.boredom
        return {
            "model": self.name,
            "boredom": round(state.boredom, 4),
            "energy": round(state.energy, 2),
            "energy_max": self.params.energy.max,
            "band": {"theta_low": b.theta_low, "theta_high": b.theta_high},
            "wants_to_explore": self.wants_to_explore(state),
            "growth_rate_per_hour": b.growth_rate,
            "formula": "dB/dt = g(1-B) - Σ w_kind·novelty·learnability·habituation",
            "hours_until_bored": self._hours_until(state.boredom, b.theta_high, b.growth_rate),
        }

    @staticmethod
    def _hours_until(b: float, target: float, g: float) -> float | None:
        if b >= target:
            return 0.0
        return round(math.log((1 - b) / (1 - target)) / g, 2)
