"""Scenario runner: the same graphs, a SimClock, a throwaway SQLite DB and fake/replayed
providers. A week of pet life runs in minutes, at zero cost, reproducibly."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dreampet.clock import RealClock, SimClock, at_local
from dreampet.config import AppConfig, deep_merge, repo_root
from dreampet.persona.model import Persona, load_persona_file
from dreampet.runtime.bootstrap import open_pet, open_repo
from dreampet.scheduler.runtime import PetRuntime
from dreampet.sim.metrics import check_assertions, summarize
from dreampet.sim.scenario import Scenario

SIM_PET = "sim-pet"


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    summary: dict[str, Any]
    assertions: list[dict[str, Any]]
    chats: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(a["ok"] for a in self.assertions)

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "passed": self.passed, "summary": self.summary, "assertions": self.assertions,
                "chats": self.chats}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "run"


def resolve_persona(scn: Scenario, base_dir: Path | None) -> Persona:
    if isinstance(scn.persona, dict):
        persona = Persona.model_validate(scn.persona.get("persona", scn.persona))
    else:
        cands = [Path(scn.persona)]
        if base_dir:
            cands.insert(0, base_dir / scn.persona)
        cands.append(repo_root() / scn.persona)
        path = next((c for c in cands if c.exists()), None)
        if path is None:
            raise FileNotFoundError(f"persona file {scn.persona!r} not found")
        persona = load_persona_file(path)
    if scn.persona_overrides:
        persona = Persona.model_validate(deep_merge(persona.model_dump(), scn.persona_overrides))
    return persona


def run_scenario(
    scn: Scenario,
    cfg: AppConfig,
    *,
    base_dir: Path | None = None,
    run_id: str | None = None,
    runs_dir: Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    register: bool = True,
    checkpoints: str = "memory",
) -> RunResult:
    run_id = run_id or f"{_slug(scn.name)}-{uuid.uuid4().hex[:6]}"
    runs_dir = runs_dir or (cfg.data_path / "sim")
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    main_repo = open_repo(cfg) if register else None
    if main_repo:
        main_repo.add_sim_run(id=run_id, name=scn.name, scenario=scn.dump(), status="running",
                              db_path=str(run_dir / "sim.sqlite"), created_at=RealClock().now())
    try:
        result = _run(scn, cfg, base_dir, run_id, run_dir, progress, checkpoints)
    except Exception as exc:
        if main_repo:
            main_repo.update_sim_run(run_id, status="failed", error=str(exc)[:2000])
        raise
    if main_repo:
        main_repo.update_sim_run(run_id, status="passed" if result.passed else "failed_assertions",
                                 summary=result.to_dict())
        main_repo.close()
    return result


def _run(scn: Scenario, cfg: AppConfig, base_dir: Path | None, run_id: str, run_dir: Path,
         progress, checkpoints: str) -> RunResult:
    persona = resolve_persona(scn, base_dir)
    tz = ZoneInfo(persona.timezone)
    overrides = deep_merge(scn.config, {"database_url": f"sqlite:///{run_dir / 'sim.sqlite'}"})
    run_cfg = AppConfig.model_validate(deep_merge(cfg.model_dump(), overrides))

    if scn.start:
        s = datetime.fromisoformat(scn.start)
        start_local = s if (len(scn.start) > 10) else datetime.combine(s.date(), datetime.min.time())
        start = start_local.replace(tzinfo=tz) if start_local.tzinfo is None else start_local
        if len(scn.start) <= 10:
            start = at_local(start, persona.wake_time, tz)
    else:
        start = at_local(datetime(2026, 10, 1, 12, tzinfo=tz), persona.wake_time, tz)
    end = start + timedelta(days=scn.days)
    clock = SimClock(start, speed=scn.speed_factor)

    opened = open_pet(run_cfg, pet_id=SIM_PET, persona=persona, clock=clock, run_id=run_id, seed=scn.seed,
                      mode_overrides=scn.providers, checkpointer_kind=checkpoints,
                      checkpoint_path=run_dir / "checkpoints.sqlite", media_root=run_dir / "media")
    ctx = opened.ctx
    if scn.drives:
        ctx.overrides = dict(scn.drives)
        ctx.refresh_params()
    rt = PetRuntime(ctx, run_video_inline=True, auto_approve_video=True)

    # schedule scenario events
    pending: list[tuple[datetime, int, dict[str, Any]]] = []
    boosts: list[tuple[datetime, datetime, str]] = []
    for i, ev in enumerate(scn.events):
        day0 = start + timedelta(days=ev.day - 1)
        if ev.inject_articles:
            if ev.time == "all":
                t0 = at_local(day0, "00:00", tz)
                boosts.append((t0, t0 + timedelta(days=1), ev.inject_articles))
            else:
                t0 = at_local(day0, ev.time, tz)
                boosts.append((t0, t0 + timedelta(hours=4), ev.inject_articles))
        if ev.chat:
            t = at_local(day0, ev.time if ev.time != "all" else "12:00", tz)
            pending.append((t, i, ev.model_dump()))
    pending.sort(key=lambda x: (x[0], x[1]))
    chats: list[dict[str, Any]] = []
    last_day = [None]

    search_fake = ctx.providers.search.fake

    def deliver() -> None:
        now = ctx.now()
        # article injections: flood fake search results with the matching corpus articles
        ids: list[str] = []
        for t0, t1, pattern in boosts:
            if t0 <= now < t1:
                ids += ctx.providers.corpus.match(pattern)
        _set_boost(search_fake, ids)
        while pending and pending[0][0] <= now:
            t, _, ev = pending.pop(0)
            out = rt.chat(ev["chat"], conversation_id=ev["conversation"])
            chats.append({"t": now.isoformat(), "day": ev["day"], "message": ev["chat"], "reply": out["reply"],
                          "mode": out["mode"], "recalled": [r["id"] for r in out["recalled"]]})
        day = ctx.local_day()
        if progress and day != last_day[0]:
            last_day[0] = day
            progress({"run_id": run_id, "day": day, "t": now.isoformat(),
                      "fraction": min(1.0, (now - start) / (end - start)), "drives": ctx.drives_snapshot()})

    rt.between_steps = deliver
    rt.run(until=end)
    ctx.clock.sleep_until(end)
    ctx.step_drives()

    summary = summarize(ctx.repo, SIM_PET, run_id, tz)
    summary.update({"run_id": run_id, "scenario": scn.name, "seed": scn.seed, "days": scn.days,
                    "start": start.isoformat(), "end": end.isoformat(), "persona": persona.name,
                    "providers": {k: v.mode for k, v in ctx.providers.roles.items()}})
    assertions = check_assertions(summary, scn.assert_)
    result = RunResult(run_id=run_id, run_dir=run_dir, summary=summary, assertions=assertions, chats=chats)
    (run_dir / "summary.json").write_text(json.dumps(result.to_dict(), indent=1, default=str))
    (run_dir / "scenario.json").write_text(json.dumps(scn.dump(), indent=1))
    ctx.repo.close()
    if progress:
        progress({"run_id": run_id, "fraction": 1.0, "done": True, "passed": result.passed})
    return result


def _set_boost(search_fake, ids: list[str]) -> None:
    search_fake.boost_ids = ids
