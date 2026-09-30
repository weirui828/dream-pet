"""PetContext: everything a graph node needs, handed to graphs when they are built.

It also owns the pet's live drive state and the one place drives are stepped forward, so the
dashboard, replay and the scheduler all see the same numbers."""

from __future__ import annotations

import random
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dreampet.clock import Clock, at_local
from dreampet.config import AppConfig
from dreampet.drives.base import DriveState, Event, Lifecycle, make_drive_model
from dreampet.drives.params import DrivesFile
from dreampet.memory.repo import MemoryRepo
from dreampet.persona.compiler import Compiled, compile_persona
from dreampet.persona.model import Persona
from dreampet.providers.roles import Providers
from dreampet.runtime.bus import Bus
from dreampet.runtime.media import MediaStore


@dataclass
class PetContext:
    cfg: AppConfig
    pet_id: str
    run_id: str
    clock: Clock
    repo: MemoryRepo
    providers: Providers
    persona: Persona
    drives_file: DrivesFile
    bus: Bus
    media: MediaStore
    seed: int | None = None
    checkpointer: Any = None
    state: DriveState = field(default_factory=DriveState)
    lock: threading.RLock = field(default_factory=threading.RLock)
    hooks: dict[str, Any] = field(default_factory=dict)  # runtime callbacks, e.g. "finding", "drift"

    def __post_init__(self):
        self.rng = random.Random(self.seed) if self.seed is not None else random.Random()
        self._id_lock = threading.Lock()
        self.overrides: dict[str, Any] = {}
        self.refresh_params()

    # ---- ids & time --------------------------------------------------------------------------

    def new_id(self, prefix: str) -> str:
        with self._id_lock:
            if self.seed is not None:
                return f"{prefix}_{uuid.UUID(int=self.rng.getrandbits(128)).hex[:16]}"
            return f"{prefix}_{uuid.uuid4().hex[:16]}"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.persona.timezone)

    def now(self) -> datetime:
        return self.clock.now()

    def local_day(self, t: datetime | None = None) -> str:
        return (t or self.now()).astimezone(self.tz).date().isoformat()

    def today_start(self) -> datetime:
        """The most recent wake time: 'today' for episodic novelty and habituation."""
        now = self.now()
        w = at_local(now, self.persona.wake_time, self.tz)
        return w if w <= now else w - timedelta(days=1)

    def spend(self, action: str, factor: float = 1.0) -> None:
        minutes = getattr(self.cfg.durations, action, 0) * factor
        if minutes:
            self.clock.spend(timedelta(minutes=minutes))

    def energy_cost(self, action: str) -> float:
        return float(getattr(self.cfg.energy_costs, action, 0.0))

    # ---- params ------------------------------------------------------------------------------

    def refresh_params(self) -> Compiled:
        """Recompile persona + drives.yaml (hot-reloaded) + owner overrides."""
        self.compiled = compile_persona(self.persona, self.drives_file.get(), self.overrides)
        self.drive_model = make_drive_model(self.cfg.drive_model, self.compiled.drives,
                                            nap_recovery_factor=self.cfg.scheduler.nap_recovery_factor)
        return self.compiled

    @property
    def params(self):
        return self.compiled.drives

    # ---- events & drives ---------------------------------------------------------------------

    def emit(self, type_: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        t = self.now()
        self.repo.add_event(self.pet_id, self.run_id, t, type_, payload)
        self.bus.publish("event", {"type": type_, "t": t.isoformat(), "payload": payload})

    def hook(self, name: str, payload: dict[str, Any]) -> None:
        fn = self.hooks.get(name)
        if fn:
            fn(payload)

    def activity(self, line: str) -> None:
        """The dashboard's current-activity line ("reading about tardigrades")."""
        self.emit("activity", {"line": line})

    def step_drives(self, events: list[Event] | None = None, sample: bool = True) -> DriveState:
        with self.lock:
            now = self.now()
            prev = self.state.updated_at or now
            dt = max(timedelta(0), now - prev)
            s = self.drive_model.update(self.state, events or [], dt)
            if prev and self.local_day(prev) != self.local_day(now):
                s.satisfaction_today = 0.0
            s.updated_at = now
            s.curiosity = self._best_lp()
            self.state = s
        if sample:
            self.record_sample()
        return s

    def record_sample(self) -> None:
        s = self.state
        self.repo.add_sample(self.pet_id, self.run_id, self.now(), s.boredom, s.energy, s.curiosity, s.lifecycle)
        self.bus.publish("drives", {"t": self.now().isoformat(), "boredom": s.boredom, "energy": s.energy,
                                    "curiosity": s.curiosity, "state": s.lifecycle})

    def set_lifecycle(self, new: str, reason: str) -> None:
        with self.lock:
            old = self.state.lifecycle
            if old == new:
                return
            # settle drives under the old state before switching
            self.step_drives(sample=False)
            self.state.lifecycle = new
        self.emit("state_change", {"from": old, "to": new, "reason": reason, "drives": self.drives_snapshot()})
        self.record_sample()
        self.bus.publish("state", {"from": old, "to": new, "reason": reason})

    def drives_snapshot(self) -> dict[str, Any]:
        s = self.state
        return {"boredom": round(s.boredom, 4), "energy": round(s.energy, 3), "curiosity": round(s.curiosity, 4),
                "lifecycle": s.lifecycle}

    def _best_lp(self) -> float:
        try:
            cl = self.repo.list_clusters(self.pet_id)
        except Exception:
            return 0.0
        return max((c.lp for c in cl), default=0.0)

    def restore_state(self) -> None:
        """Rebuild drive state from the last sample and state_change event after a restart."""
        last = self.repo.last_sample(self.pet_id, self.run_id)
        if last:
            self.state = DriveState(boredom=last["boredom"], energy=last["energy"], curiosity=last["curiosity"],
                                    lifecycle=last["state"], updated_at=last["t"])
        else:
            self.state = DriveState(energy=self.params.energy.max, lifecycle=Lifecycle.IDLE, updated_at=self.now())

    def base_payload(self) -> dict[str, Any]:
        """Shared state fields every graph carries."""
        return {"pet_id": self.pet_id, "sim_time": self.now().isoformat(), "drives": self.drives_snapshot(),
                "budget": {"spent_today": round(self.providers.meter.spent_today(), 4),
                           "cap": self.cfg.budgets.daily_usd_hard_cap}}

    def save_persona(self, persona: Persona) -> None:
        self.persona = persona
        self.repo.update_pet(self.pet_id, persona=persona.model_dump(mode="json"), name=persona.name,
                             language=persona.language)
        self.refresh_params()
