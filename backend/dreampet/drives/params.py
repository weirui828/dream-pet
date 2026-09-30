"""Drive tunables with bounds. Loaded from drives.yaml, modulated by persona traits, then
overridden by the owner's advanced settings. Every value is bounds-checked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class BoredomParams(BaseModel):
    growth_rate: float = Field(0.08, ge=0.01, le=0.5, description="per sim-hour")
    theta_high: float = Field(0.7, ge=0.3, le=0.95)
    theta_low: float = Field(0.3, ge=0.05, le=0.8)
    chat_weight: float = Field(0.5, ge=0.0, le=2.0)
    read_weight: float = Field(0.3, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def _band(self):
        if self.theta_high <= self.theta_low + 0.1:
            raise ValueError("boredom.theta_high must be > theta_low + 0.1")
        return self


class CuriosityParams(BaseModel):
    novelty_k: int = Field(10, ge=3, le=50)
    episodic_alpha: float = Field(0.6, ge=0.0, le=1.0)
    habituation: float = Field(0.8, ge=0.3, le=1.0)
    lp_window: int = Field(6, ge=3, le=20)
    epsilon: float = Field(0.1, ge=0.0, le=0.5)
    tau: float = Field(0.5, ge=0.05, le=2.0)


class EnergyParams(BaseModel):
    max: float = Field(100, ge=10, le=1000)
    recovery_per_sleep_hour: float = Field(15, ge=1, le=100)
    nap_below: float = Field(10, ge=0, le=50)


class DriveParams(BaseModel):
    boredom: BoredomParams = BoredomParams()
    curiosity: CuriosityParams = CuriosityParams()
    energy: EnergyParams = EnergyParams()

    def flat(self) -> dict[str, Any]:
        out = {}
        for group, model in (("boredom", self.boredom), ("curiosity", self.curiosity), ("energy", self.energy)):
            for k, v in model.model_dump().items():
                out[f"{group}.{k}"] = v
        return out

    @staticmethod
    def bounds() -> dict[str, dict[str, Any]]:
        out = {}
        for group, cls in (("boredom", BoredomParams), ("curiosity", CuriosityParams), ("energy", EnergyParams)):
            for name, field in cls.model_fields.items():
                lo = hi = None
                for m in field.metadata:
                    lo = getattr(m, "ge", lo) if getattr(m, "ge", None) is not None else lo
                    hi = getattr(m, "le", hi) if getattr(m, "le", None) is not None else hi
                out[f"{group}.{name}"] = {"default": field.default, "min": lo, "max": hi}
        return out


def apply_overrides(base: DriveParams, overrides: dict[str, Any] | None) -> DriveParams:
    """Apply nested ({"boredom": {...}}) or dotted ({"boredom.theta_high": 0.8}) overrides."""
    if not overrides:
        return base
    data = base.model_dump()
    for key, value in overrides.items():
        if "." in key:
            group, name = key.split(".", 1)
            data.setdefault(group, {})[name] = value
        elif isinstance(value, dict):
            data.setdefault(key, {}).update(value)
    return DriveParams.model_validate(data)


def clamp_to_bounds(flat: dict[str, float]) -> dict[str, float]:
    b = DriveParams.bounds()
    out = {}
    for k, v in flat.items():
        lo, hi = b[k]["min"], b[k]["max"]
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        out[k] = v
    return out


class DrivesFile:
    """drives.yaml with hot reload: `get()` re-reads the file when its mtime changes."""

    def __init__(self, path: Path | None):
        self.path = path
        self._mtime: float | None = None
        self._params = DriveParams()
        self.last_error: str | None = None
        self.get()

    def get(self) -> DriveParams:
        if self.path is None or not self.path.exists():
            return self._params
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                raw = yaml.safe_load(self.path.read_text()) or {}
                self._params = DriveParams.model_validate(raw)
                self.last_error = None
            except Exception as exc:  # keep the last good params on a bad edit
                self.last_error = str(exc)
        return self._params
