"""Adapter registry. Built-ins are listed here; third-party adapters register through the
`dreampet.providers` entry-point group with names like `search.myengine` or `video.myhost`."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

from dreampet.config import LLM_ROLES, RoleConfig
from dreampet.providers.base import ProviderError


def role_kind(role: str) -> str:
    return "chat" if role in LLM_ROLES else role


def _builtin(kind: str, provider: str) -> Callable[..., Any] | None:
    if kind == "chat":
        from dreampet.providers.adapters.llm import LangChainChat

        return LangChainChat  # any provider init_chat_model understands
    if kind == "embeddings":
        from dreampet.providers.adapters.llm import LangChainEmbeddings

        return LangChainEmbeddings
    if kind in ("search", "fetch"):
        from dreampet.providers.adapters import web

        return {
            ("search", "searxng"): web.SearxngSearch, ("search", "tavily"): web.TavilySearch,
            ("search", "brave"): web.BraveSearch, ("search", "exa"): web.ExaSearch,
            ("fetch", "trafilatura"): web.TrafilaturaFetch, ("fetch", "jina"): web.JinaFetch,
            ("fetch", "firecrawl"): web.FirecrawlFetch,
        }.get((kind, provider))
    if kind in ("video", "image"):
        from dreampet.providers.adapters import media

        return {
            ("video", "fal"): media.FalVideo, ("video", "replicate"): media.ReplicateVideo,
            ("video", "comfyui"): media.ComfyUIVideo,
            ("image", "fal"): media.FalImage, ("image", "replicate"): media.ReplicateImage,
        }.get((kind, provider))
    return None


def make_live_adapter(role: str, rc: RoleConfig, *, user_agent: str, callbacks: list | None = None):
    kind = role_kind(role)
    for ep in entry_points(group="dreampet.providers"):
        if ep.name == f"{kind}.{rc.provider}":
            return ep.load()(rc)
    cls = _builtin(kind, rc.provider)
    if cls is None:
        raise ProviderError(f"no {kind} adapter named {rc.provider!r}")
    if kind == "chat":
        return cls(rc, callbacks=callbacks)
    if kind in ("search", "fetch"):
        return cls(rc, user_agent)
    return cls(rc)


def available() -> dict[str, list[str]]:
    out = {
        "chat": ["anthropic", "openai", "google_genai", "ollama", "(any init_chat_model provider)"],
        "embeddings": ["openai", "voyageai", "ollama", "huggingface", "(any init_embeddings provider)"],
        "search": ["searxng", "tavily", "brave", "exa"],
        "fetch": ["trafilatura", "jina", "firecrawl"],
        "video": ["fal", "replicate", "comfyui"],
        "image": ["fal", "replicate"],
    }
    for ep in entry_points(group="dreampet.providers"):
        kind, _, name = ep.name.partition(".")
        out.setdefault(kind, []).append(name)
    for k in out:
        out[k].insert(0, "fake")
    return out
