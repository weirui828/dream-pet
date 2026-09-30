from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dreampet.clock import SimClock
from dreampet.config import AppConfig, repo_root
from dreampet.persona.model import load_persona_file
from dreampet.runtime.bootstrap import open_pet
from dreampet.scheduler.runtime import PetRuntime

ROOT = repo_root()


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate({
        "data_dir": str(tmp_path / "data"),
        "fixtures_dir": str(tmp_path / "fixtures"),
        "drives_file": str(tmp_path / "drives.yaml"),
        "base_dir": str(tmp_path),
    })


@pytest.fixture
def clock() -> SimClock:
    return SimClock(datetime(2026, 10, 1, 7, 0, tzinfo=UTC))


@pytest.fixture
def ctx(cfg, clock):
    persona = load_persona_file(ROOT / "presets" / "physicist.yaml")
    opened = open_pet(cfg, pet_id="t", persona=persona, clock=clock, run_id="test", seed=7,
                      checkpointer_kind="memory", media_root=Path(cfg.data_dir) / "media")
    yield opened.ctx
    opened.ctx.repo.close()


@pytest.fixture
def runtime(ctx) -> PetRuntime:
    return PetRuntime(ctx, run_video_inline=True, auto_approve_video=True)
