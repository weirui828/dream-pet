from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

TRAIT_NAMES = ("restlessness", "openness", "depth", "sociability", "stamina", "dreaminess")


class Traits(BaseModel):
    """0–100 sliders. depth: 0 = breadth-first, 100 = depth-first."""

    restlessness: int = Field(50, ge=0, le=100)
    openness: int = Field(50, ge=0, le=100)
    depth: int = Field(50, ge=0, le=100)
    sociability: int = Field(50, ge=0, le=100)
    stamina: int = Field(50, ge=0, le=100)
    dreaminess: int = Field(50, ge=0, le=100)


class Interest(BaseModel):
    topic: str
    weight: float = Field(50, ge=0, le=100)


class ProactiveSettings(BaseModel):
    enabled: bool = True
    quiet_hours: tuple[str, str] = ("22:00", "08:00")


class Persona(BaseModel):
    name: str = "Mochi"
    language: str = "en"  # BCP 47
    timezone: str = "UTC"
    bedtime: str = "23:00"
    wake_time: str = "07:00"
    temperament: str = "playful"
    traits: Traits = Traits()
    interests: list[Interest] = Field(default_factory=list)
    aversions: list[str] = Field(default_factory=list)
    self_description: str = "I'm a small curious creature who likes to learn things and tell you about them."
    visual_style: str = "soft watercolor, dusk light"
    palette: list[str] = Field(default_factory=lambda: ["#1d3557", "#f1c27d", "#e76f51"])
    sleep_chat: Literal["sleeptalk", "wake"] = "sleeptalk"
    proactive: ProactiveSettings = ProactiveSettings()
    locks: list[str] = Field(default_factory=list)  # e.g. ["traits.depth", "interests.haiku"]
    avatar_uri: str | None = None

    def interest_weight(self, topic: str) -> float:
        for i in self.interests:
            if i.topic.lower() == topic.lower():
                return i.weight
        return 0.0

    def is_locked(self, field: str) -> bool:
        return field in self.locks or field.split(".")[0] in self.locks


def load_persona_file(path: str | Path) -> Persona:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return Persona.model_validate(raw.get("persona", raw))


def dump_persona_yaml(p: Persona) -> str:
    return yaml.safe_dump({"persona": p.model_dump(mode="json")}, sort_keys=False, allow_unicode=True)
