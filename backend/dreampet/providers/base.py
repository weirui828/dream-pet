"""Provider protocols. Every AI capability is a role; each role wraps one adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

import numpy as np
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    """A provider failed (network, API error, bad output)."""


class BudgetExceeded(RuntimeError):
    """The budget guard refused a call."""


class ReplayMiss(ProviderError):
    """Replay mode had no fixture for this request."""


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0
    calls: int = 1
    usd: float | None = None  # provider-reported cost when available


@dataclass
class ChatRequest:
    task: str  # e.g. "plan_queries"; lets fakes and fixtures tell calls apart
    system: str
    user: str
    temperature: float | None = None
    max_tokens: int | None = None
    # Structured inputs the prompt was rendered from. Live adapters ignore it; the fake uses it
    # to produce sensible deterministic output. Never part of fixture keys.
    context: dict[str, Any] = field(default_factory=dict)

    def key_dict(self) -> dict[str, Any]:
        return {"task": self.task, "system": self.system, "user": self.user,
                "temperature": self.temperature, "max_tokens": self.max_tokens}


@dataclass
class ChatResult:
    text: str
    usage: Usage


@dataclass
class StructResult:
    data: BaseModel
    usage: Usage


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ShotRequest:
    prompt: str
    negative: str
    seconds: float
    width: int
    height: int
    seed: int = 0


@dataclass
class VideoLimits:
    max_shot_seconds: float = 5.0
    min_shot_seconds: float = 2.0
    max_total_seconds: float = 30.0


class ChatProvider(Protocol):
    model_name: str

    def generate(self, req: ChatRequest) -> ChatResult: ...

    def structured(self, req: ChatRequest, schema: type[T]) -> StructResult: ...


class EmbeddingsProvider(Protocol):
    model_name: str
    dim: int

    def embed(self, texts: list[str]) -> tuple[list[np.ndarray], Usage]: ...


class SearchProvider(Protocol):
    def search(self, query: str, k: int = 5, language: str = "en") -> list[SearchResult]: ...


class FetchProvider(Protocol):
    def fetch(self, url: str) -> FetchedPage: ...


class VideoProvider(Protocol):
    model_name: str
    limits: VideoLimits

    def submit(self, shot: ShotRequest) -> str: ...

    def poll(self, job_id: str) -> str:  # "queued" | "running" | "done" | "failed"
        ...

    def download(self, job_id: str, dest: Path) -> Path: ...


class ImageProvider(Protocol):
    model_name: str

    def generate(self, prompt: str, width: int, height: int, n: int = 1, seed: int = 0) -> list[bytes]: ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
