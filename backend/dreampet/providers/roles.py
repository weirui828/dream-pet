"""Role wrappers. Each role = one adapter + mode (fake/record/replay/live) + budget + usage.

Graph nodes only ever talk to these wrappers, so every AI call is metered, capped and
recordable, and modes can mix per role (e.g. live dreamer_llm with replayed search)."""

from __future__ import annotations

import threading
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np

from dreampet.clock import real_sleep, wall_monotonic
from dreampet.config import ROLE_NAMES, AppConfig, RoleConfig
from dreampet.providers.base import (
    ChatRequest,
    ChatResult,
    FetchedPage,
    ProviderError,
    ReplayMiss,
    SearchResult,
    ShotRequest,
    StructResult,
    Usage,
    VideoLimits,
    estimate_tokens,
)
from dreampet.providers.budget import Meter, estimate_usd, pricing_for
from dreampet.providers.corpus import Corpus, load_corpus
from dreampet.providers.fake import FakeChat, FakeEmbeddings, FakeFetch, FakeImage, FakeSearch, FakeVideo
from dreampet.providers.fixtures import FixtureStore, request_key
from dreampet.providers.registry import make_live_adapter, role_kind


@dataclass
class _Outcome:
    value: Any
    usage: Usage
    live: bool


class Role:
    def __init__(self, name: str, rc: RoleConfig, mode: str, fake: Any, meter: Meter,
                 fixtures: FixtureStore | None, replay_fallback: str, live_factory):
        self.name = name
        self.rc = rc
        self.mode = mode
        self.fake = fake
        self.meter = meter
        self.fixtures = fixtures
        self.replay_fallback = replay_fallback
        self._live_factory = live_factory
        self._live = None
        self.pricing = pricing_for(name, rc)
        self._lock = threading.Lock()

    @property
    def live(self):
        with self._lock:
            if self._live is None:
                self._live = self._live_factory()
            return self._live

    @property
    def adapter(self):
        return self.fake if self.mode == "fake" else self.live

    @property
    def model_name(self) -> str:
        if self.mode == "fake":
            return getattr(self.fake, "model_name", "fake")
        return f"{self.rc.provider}:{self.rc.model}" if self.rc.model else self.rc.provider

    def describe(self) -> dict[str, Any]:
        return {"role": self.name, "mode": self.mode, "provider": self.rc.provider if self.mode != "fake" else "fake",
                "model": self.model_name}

    def _run(self, op: str, payload: dict[str, Any], est: Usage, live_fn, fake_fn, encode, decode) -> _Outcome:
        if self.mode == "fake":
            return _Outcome(*fake_fn(), live=False)
        key = request_key(self.name, op, self.rc.provider, self.rc.model, payload)
        if self.mode == "replay":
            hit = self.fixtures.load(self.name, key) if self.fixtures else None
            if hit is not None:
                value, usage = decode(hit)
                return _Outcome(value, usage, live=False)
            if self.replay_fallback == "fake":
                return _Outcome(*fake_fn(), live=False)
            raise ReplayMiss(f"{self.name}: no fixture for {op} ({key[:12]})")
        self.meter.check(self.name, estimate_usd(self.pricing, est))
        value, usage = live_fn()
        if self.mode == "record" and self.fixtures:
            self.fixtures.save(self.name, key, op, payload, encode(value, usage))
        return _Outcome(value, usage, live=True)

    def _account(self, out: _Outcome) -> None:
        est = estimate_usd(self.pricing, out.usage)
        usd = (out.usage.usd if out.usage.usd is not None else est) if out.live else 0.0
        self.meter.record(self.name, out.usage, usd=usd, est_usd=est)


def _usage_dict(u: Usage) -> dict[str, Any]:
    return {"tokens_in": u.tokens_in, "tokens_out": u.tokens_out, "seconds": u.seconds, "calls": u.calls}


def _usage_from(d: dict[str, Any]) -> Usage:
    return Usage(tokens_in=d.get("tokens_in", 0), tokens_out=d.get("tokens_out", 0),
                 seconds=d.get("seconds", 0.0), calls=d.get("calls", 1))


class ChatRole(Role):
    def generate(self, req: ChatRequest) -> str:
        if req.temperature is None:
            req.temperature = self.rc.temperature
        est = Usage(tokens_in=estimate_tokens(req.system + req.user), tokens_out=req.max_tokens or 400)

        def live():
            r: ChatResult = self.live.generate(req)
            return r.text, r.usage

        def fake():
            r = self.fake.generate(req)
            return r.text, r.usage

        out = self._run("generate", req.key_dict(), est, live, fake,
                        encode=lambda v, u: {"text": v, "usage": _usage_dict(u)},
                        decode=lambda d: (d["text"], _usage_from(d["usage"])))
        self._account(out)
        return out.value

    def structured(self, req: ChatRequest, schema):
        if req.temperature is None:
            req.temperature = self.rc.temperature
        est = Usage(tokens_in=estimate_tokens(req.system + req.user), tokens_out=req.max_tokens or 800)

        def live():
            r: StructResult = self.live.structured(req, schema)
            return r.data, r.usage

        def fake():
            r = self.fake.structured(req, schema)
            return r.data, r.usage

        payload = {**req.key_dict(), "schema": schema.__name__}
        out = self._run("structured", payload, est, live, fake,
                        encode=lambda v, u: {"data": v.model_dump(mode="json"), "usage": _usage_dict(u)},
                        decode=lambda d: (schema.model_validate(d["data"]), _usage_from(d["usage"])))
        self._account(out)
        return out.value


class EmbeddingsRole(Role):
    @property
    def dim(self) -> int:
        return int(self.rc.dim or getattr(self.fake, "dim", 256))

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        est = Usage(tokens_in=sum(estimate_tokens(t) for t in texts))

        def live():
            return self.live.embed(texts)

        def fake():
            return self.fake.embed(texts)

        out = self._run("embed", {"texts": texts}, est, live, fake,
                        encode=lambda v, u: {"vectors": [x.tolist() for x in v], "usage": _usage_dict(u)},
                        decode=lambda d: ([np.asarray(x, np.float32) for x in d["vectors"]], _usage_from(d["usage"])))
        self._account(out)
        return out.value

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


class SearchRole(Role):
    def search(self, query: str, k: int = 5, language: str = "en") -> list[SearchResult]:
        def live():
            return self.live.search(query, k, language), Usage()

        def fake():
            return self.fake.search(query, k, language), Usage()

        out = self._run("search", {"q": query, "k": k, "lang": language}, Usage(), live, fake,
                        encode=lambda v, u: [r.to_dict() for r in v],
                        decode=lambda d: ([SearchResult(**r) for r in d], Usage()))
        self._account(out)
        return out.value


class WebGuard:
    """Crawling etiquette for live fetches: blocklist, robots.txt, per-domain rate limit."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg.safety
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def blocked(self, url: str) -> str | None:
        host = (urlparse(url).hostname or "").lower()
        if urlparse(url).scheme not in ("http", "https"):
            return "scheme"
        for d in self.cfg.domain_blocklist:
            if host == d or host.endswith("." + d):
                return "blocklist"
        return None

    def allowed_by_robots(self, url: str) -> bool:
        if not self.cfg.respect_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        with self._lock:
            if base not in self._robots:
                rp = urllib.robotparser.RobotFileParser()
                rp.set_url(base + "/robots.txt")
                try:
                    import httpx

                    r = httpx.get(base + "/robots.txt", timeout=10, headers={"User-Agent": self.cfg.user_agent},
                                  follow_redirects=True)
                    rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                except Exception:
                    rp.parse([])
                self._robots[base] = rp
            rp = self._robots[base]
        return rp.can_fetch(self.cfg.user_agent, url) if rp else True

    def wait_turn(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        with self._lock:
            last = self._last.get(host)
            now = wall_monotonic()
            wait = 0.0 if last is None else max(0.0, self.cfg.per_domain_min_seconds - (now - last))
            self._last[host] = now + wait
        if wait:
            real_sleep(wait)


class FetchRole(Role):
    guard: WebGuard

    def fetch(self, url: str) -> FetchedPage:
        why = self.guard.blocked(url)
        if why:
            raise ProviderError(f"refused to fetch {url}: {why}")

        def live():
            if not self.guard.allowed_by_robots(url):
                raise ProviderError(f"robots.txt disallows {url}")
            self.guard.wait_turn(url)
            return self.live.fetch(url), Usage()

        def fake():
            return self.fake.fetch(url), Usage()

        out = self._run("fetch", {"url": url}, Usage(), live, fake,
                        encode=lambda v, u: v.to_dict(), decode=lambda d: (FetchedPage(**d), Usage()))
        self._account(out)
        return out.value


class VideoRole(Role):
    """Video calls are long-running and binary, so replay mode serves fakes (fixtures would be
    huge); record mode behaves like live."""

    @property
    def limits(self) -> VideoLimits:
        a = self.fake if self.mode in ("fake", "replay") else self.live
        return a.limits

    @property
    def usd_per_second(self) -> float:
        return self.pricing.get("usd_per_second", 0.0)

    def estimate(self, seconds: float) -> float:
        return seconds * self.usd_per_second

    def _adapter(self):
        return self.fake if self.mode in ("fake", "replay") else self.live

    def submit(self, shot: ShotRequest) -> str:
        if self.mode not in ("fake", "replay"):
            self.meter.check(self.name, self.estimate(shot.seconds))
        return self._adapter().submit(shot)

    def poll(self, job_id: str) -> str:
        return self._adapter().poll(job_id)

    def download(self, job_id: str, dest: Path, seconds: float) -> Path:
        path = self._adapter().download(job_id, dest)
        live = self.mode not in ("fake", "replay")
        u = Usage(seconds=seconds)
        est = self.estimate(seconds)
        self.meter.record(self.name, u, usd=est if live else 0.0, est_usd=est)
        return path


class ImageRole(Role):
    def _adapter(self):
        return self.fake if self.mode in ("fake", "replay") else self.live

    def generate(self, prompt: str, width: int, height: int, n: int = 1, seed: int = 0) -> list[bytes]:
        live = self.mode not in ("fake", "replay")
        est = self.pricing.get("usd_per_call", 0.0) * n
        if live:
            self.meter.check(self.name, est)
        out = self._adapter().generate(prompt, width, height, n=n, seed=seed)
        self.meter.record(self.name, Usage(calls=n), usd=est if live else 0.0, est_usd=est)
        return out


ROLE_CLASSES = {
    "chat": ChatRole, "embeddings": EmbeddingsRole, "search": SearchRole, "fetch": FetchRole,
    "video": VideoRole, "image": ImageRole,
}


class Providers:
    def __init__(self, cfg: AppConfig, meter: Meter, *, seed: int = 0, mode_overrides: dict[str, str] | None = None,
                 palette: list[str] | None = None, callbacks: list | None = None):
        self.cfg = cfg
        self.meter = meter
        self._corpus: Corpus | None = None
        self.fixtures = FixtureStore(cfg.path(cfg.fixtures_dir))
        self.guard = WebGuard(cfg)
        mode_overrides = mode_overrides or {}
        self.roles: dict[str, Role] = {}
        for name in ROLE_NAMES:
            rc = cfg.role(name)
            mode = mode_overrides.get(name) or mode_overrides.get("default") or rc.effective_mode()
            if mode != "fake" and rc.provider == "fake":
                mode = "fake"  # nothing live to call
            kind = role_kind(name)
            fake = self._make_fake(kind, rc, seed, palette)
            cls = ROLE_CLASSES[kind]
            role = cls(name, rc, mode, fake, meter, self.fixtures, cfg.replay_fallback,
                       live_factory=(lambda n=name, r=rc: make_live_adapter(
                           n, r, user_agent=cfg.safety.user_agent, callbacks=callbacks)))
            if isinstance(role, FetchRole):
                role.guard = self.guard
            self.roles[name] = role

    @property
    def corpus(self) -> Corpus:
        if self._corpus is None:
            self._corpus = load_corpus(self.cfg.resolved_corpus_path())
        return self._corpus

    def _make_fake(self, kind: str, rc: RoleConfig, seed: int, palette):
        if kind == "chat":
            return FakeChat(seed=seed)
        if kind == "embeddings":
            return FakeEmbeddings(dim=int(rc.dim or 256))
        if kind == "search":
            return FakeSearch(lambda: self.corpus)
        if kind == "fetch":
            return FakeFetch(lambda: self.corpus)
        if kind == "video":
            return FakeVideo(palette=palette)
        if kind == "image":
            return FakeImage(palette=palette)
        raise KeyError(kind)

    def validate_live(self) -> dict[str, str]:
        """Instantiate each non-fake adapter so misconfiguration surfaces at startup."""
        problems = {}
        for name, role in self.roles.items():
            if role.mode in ("live", "record"):
                try:
                    _ = role.live
                except Exception as exc:
                    problems[name] = str(exc)
        return problems

    # typed accessors
    @property
    def explorer(self) -> ChatRole:
        return self.roles["explorer_llm"]  # type: ignore[return-value]

    @property
    def chat(self) -> ChatRole:
        return self.roles["chat_llm"]  # type: ignore[return-value]

    @property
    def dreamer(self) -> ChatRole:
        return self.roles["dreamer_llm"]  # type: ignore[return-value]

    @property
    def screenwriter(self) -> ChatRole:
        return self.roles["screenwriter_llm"]  # type: ignore[return-value]

    @property
    def embeddings(self) -> EmbeddingsRole:
        return self.roles["embeddings"]  # type: ignore[return-value]

    @property
    def search(self) -> SearchRole:
        return self.roles["search"]  # type: ignore[return-value]

    @property
    def fetch(self) -> FetchRole:
        return self.roles["fetch"]  # type: ignore[return-value]

    @property
    def video(self) -> VideoRole:
        return self.roles["video"]  # type: ignore[return-value]

    @property
    def image(self) -> ImageRole:
        return self.roles["image"]  # type: ignore[return-value]
