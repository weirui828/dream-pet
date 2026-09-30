from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel

from dreampet.drives.params import DriveParams


class Lifecycle:
    IDLE = "idle"
    EXPLORING = "exploring"
    NAPPING = "napping"
    ASLEEP = "asleep"
    DREAMING = "dreaming"
    ALL = (IDLE, EXPLORING, NAPPING, ASLEEP, DREAMING)
    AWAKE = (IDLE, EXPLORING)
    RESTING = (NAPPING, ASLEEP, DREAMING)


class DriveState(BaseModel):
    boredom: float = 0.3
    energy: float = 100.0
    curiosity: float = 0.0  # display only: best learning progress available right now
    lifecycle: str = Lifecycle.IDLE
    satisfaction_today: float = 0.0
    updated_at: datetime | None = None


@dataclass
class Event:
    """Something that happened to the pet. `satisfaction` inputs are precomputed by the node
    that produced the event (it has memory access); the drive model just combines them."""

    type: str
    t: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def energy_cost(self) -> float:
        return float(self.payload.get("energy_cost", 0.0))


class DriveModel(Protocol):
    name: str

    def update(self, state: DriveState, events: list[Event], dt: timedelta) -> DriveState: ...

    def explain(self, state: DriveState) -> dict: ...


def make_drive_model(name: str, params: DriveParams, **kwargs) -> DriveModel:
    """Look up a DriveModel by entry-point name so contributors can plug their own."""
    from importlib.metadata import entry_points

    for ep in entry_points(group="dreampet.drive_models"):
        if ep.name == name:
            return ep.load()(params, **kwargs)
    # fall back to built-ins when the package isn't installed with entry points
    from dreampet.drives.alt import CountBasedBoredom
    from dreampet.drives.hybrid import HybridLPBoredom

    builtins = {"hybrid_lp": HybridLPBoredom, "count_based": CountBasedBoredom}
    if name not in builtins:
        raise KeyError(f"unknown drive model {name!r}")
    return builtins[name](params, **kwargs)
