from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class ScenarioEvent(BaseModel):
    day: int = Field(ge=1)
    time: str = "12:00"  # local HH:MM, or "all" for the whole day
    chat: str | None = None
    inject_articles: str | None = None
    conversation: str = "sim"


class Scenario(BaseModel):
    name: str
    seed: int = 0
    persona: str | dict[str, Any] = "presets/physicist.yaml"
    days: float = Field(7, gt=0, le=365)
    speed: str | float = "max"  # "max" or sim-seconds per real second
    start: str | None = None  # local ISO date or datetime; default 2026-10-01 at the pet's wake time
    providers: dict[str, str] = Field(default_factory=lambda: {"default": "fake"})
    drives: dict[str, Any] = Field(default_factory=dict)  # owner overrides, e.g. {boredom.theta_high: 0.8}
    persona_overrides: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)  # app-config overrides (video, scheduler, ...)
    events: list[ScenarioEvent] = Field(default_factory=list)
    assert_: list[dict[str, Any]] = Field(default_factory=list, alias="assert")

    model_config = {"populate_by_name": True}

    @field_validator("speed")
    @classmethod
    def _speed(cls, v):
        if isinstance(v, str) and v != "max":
            return float(v)
        return v

    @property
    def speed_factor(self) -> float | None:
        return None if self.speed == "max" else float(self.speed)

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


def load_scenario(path: str | Path) -> tuple[Scenario, Path]:
    p = Path(path)
    return Scenario.model_validate(yaml.safe_load(p.read_text())), p.resolve().parent
