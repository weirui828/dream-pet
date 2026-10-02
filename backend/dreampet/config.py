"""App configuration (dreampet.yaml). Keys come from env vars or a local secrets file, never the DB."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from dreampet.redact import register_secrets, url_password

ROLE_NAMES = (
    "explorer_llm", "chat_llm", "dreamer_llm", "screenwriter_llm",
    "embeddings", "search", "fetch", "video", "image",
)
LLM_ROLES = ("explorer_llm", "chat_llm", "dreamer_llm", "screenwriter_llm")

Mode = Literal["fake", "record", "replay", "live"]

# Rough list prices used for estimates when a role doesn't set its own. They only feed
# estimates and budget checks; adapters report real usage where the provider gives it.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "llm": {"usd_per_mtok_in": 3.0, "usd_per_mtok_out": 15.0},
    "embeddings": {"usd_per_mtok_in": 0.02},
    "search": {"usd_per_call": 0.005},
    "fetch": {"usd_per_call": 0.0},
    "video": {"usd_per_second": 0.08},
    "image": {"usd_per_call": 0.02},
}


class RoleConfig(BaseModel, extra="allow"):
    provider: str = "fake"
    model: str | None = None
    mode: Mode | None = None  # default: fake if provider == fake else live
    api_key: str | None = Field(default=None, repr=False)
    url: str | None = None
    temperature: float | None = None
    dim: int | None = None
    max_seconds: float | None = None
    usd_per_mtok_in: float | None = None
    usd_per_mtok_out: float | None = None
    usd_per_call: float | None = None
    usd_per_second: float | None = None

    def effective_mode(self) -> Mode:
        if self.mode:
            return self.mode
        return "fake" if self.provider == "fake" else "live"

    def extra_options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class BudgetConfig(BaseModel):
    daily_usd_hard_cap: float = 2.50
    per_role_usd: dict[str, float] = Field(default_factory=lambda: {
        "explorer_llm": 0.60, "chat_llm": 0.40, "dreamer_llm": 0.30, "video": 1.00,
    })


class SchedulerConfig(BaseModel):
    tick_minutes: int = 10
    session_max_reads: int = 6
    sources_per_query: int = 2
    queries_per_plan: int = 2
    nap_wake_margin: float = 20.0  # wake from a nap at nap_below + margin
    nap_recovery_factor: float = 0.6  # naps recover this fraction of sleep rate
    dream_after_bedtime_minutes: int = 60
    explore_cooldown_minutes: int = 60  # after a session that found nothing new or hit errors


class EnergyCosts(BaseModel):
    search: float = 2.0
    read: float = 4.0
    chat_turn: float = 1.0
    wake_from_sleep: float = 5.0
    dream: float = 5.0
    video: float = 10.0
    proactive: float = 1.0


class ActionDurations(BaseModel):
    """Sim minutes each action takes (sim mode only; real mode just takes real time)."""

    plan: float = 1
    search: float = 2
    fetch: float = 1
    read: float = 7
    chat_turn: float = 1
    consolidate: float = 20
    dream: float = 15


class MemoryConfig(BaseModel):
    cluster_join_distance: float = 0.5  # cosine distance to join an existing topic cluster
    cluster_merge_distance: float = 0.18  # nightly: merge clusters whose centroids are this close
    duplicate_distance: float = 0.05  # cosine > 0.95 counts as a near-duplicate
    decay: float = 0.92  # strength multiplier per night unless accessed or dreamed
    archive_floor: float = 0.2
    w_recency: float = 1.0
    w_importance: float = 1.0
    w_similarity: float = 1.5
    recency_base: float = 0.995  # per hour since last access


class VideoConfig(BaseModel):
    frequency: Literal["nightly", "best_of_week", "stills_only", "off"] = "nightly"
    total_seconds: float = 15
    shots: int = 3
    require_approval_over_usd: float = 1.0  # auto-approve at or below this estimate
    approval_enabled: bool = True
    audio_bed: bool = True
    width: int = 854
    height: int = 480
    fps: int = 24


class ApiConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    admin_token: str | None = Field(default=None, repr=False)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])


class TracingConfig(BaseModel):
    provider: Literal["none", "langfuse", "langsmith"] = "none"


class SafetyConfig(BaseModel):
    domain_blocklist: list[str] = Field(default_factory=lambda: [
        "pornhub.com", "xvideos.com", "4chan.org", "8kun.top",
    ])
    moderation: bool = True
    max_fetch_chars: int = 6000
    per_domain_min_seconds: float = 5.0
    user_agent: str = "DreamPet/0.1 (+https://github.com/weirui828/dream-pet; self-hosted AI companion)"
    respect_robots: bool = True


class MediaConfig(BaseModel):
    backend: Literal["local", "s3"] = "local"
    path: str = "media"  # relative to data_dir
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = Field(default=None, repr=False)
    s3_secret_key: str | None = Field(default=None, repr=False)


class AppConfig(BaseModel):
    data_dir: str = "data"
    database_url: str | None = None  # default: sqlite in data_dir
    pet_id: str = "pet"
    drives_file: str = "drives.yaml"
    drive_model: str = "hybrid_lp"
    checkpoints: Literal["sqlite", "postgres", "memory"] | None = None  # default follows the database
    replay_fallback: Literal["fail", "fake"] = "fake"
    fixtures_dir: str = "fixtures"
    corpus_path: str | None = None
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    budgets: BudgetConfig = BudgetConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    energy_costs: EnergyCosts = EnergyCosts()
    durations: ActionDurations = ActionDurations()
    memory: MemoryConfig = MemoryConfig()
    video: VideoConfig = VideoConfig()
    api: ApiConfig = ApiConfig()
    tracing: TracingConfig = TracingConfig()
    safety: SafetyConfig = SafetyConfig()
    media: MediaConfig = MediaConfig()

    # Set by load_config: directory the config file lives in, used to resolve relative paths.
    base_dir: str = "."

    def role(self, name: str) -> RoleConfig:
        return self.roles.get(name) or RoleConfig()

    def path(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else Path(self.base_dir) / q

    @property
    def data_path(self) -> Path:
        return self.path(self.data_dir)

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_path / 'dreampet.sqlite'}"

    def resolved_corpus_path(self) -> Path:
        if self.corpus_path:
            return self.path(self.corpus_path)
        return repo_root() / "corpus" / "wikipedia.jsonl.gz"

    def public_roles(self) -> dict[str, dict[str, Any]]:
        """What the UI may see: provider, model and mode, never keys."""
        out = {}
        for name in ROLE_NAMES:
            rc = self.role(name)
            out[name] = {
                "provider": rc.provider,
                "model": rc.model,
                "mode": rc.effective_mode(),
                "configured": name in self.roles,
                "has_key": bool(rc.api_key),
            }
        return out


_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            return env.get(m.group(1), m.group(2) or "")
        out = _ENV_RE.sub(sub, value)
        return out if out != "" or value == "" else None
    if isinstance(value, dict):
        return {k: _expand_env(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v, env) for v in value]
    return value


def load_secrets_file(path: Path) -> dict[str, str]:
    """A dotenv-style local secrets file (KEY=value per line)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "corpus").is_dir() and (parent / "backend").is_dir():
            return parent
    return Path.cwd()


def find_config_path(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    env = os.environ.get("DREAMPET_CONFIG")
    if env:
        return Path(env)
    for cand in (Path.cwd() / "dreampet.yaml", repo_root() / "dreampet.yaml"):
        if cand.exists():
            return cand
    return None


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> AppConfig:
    cfg_path = find_config_path(path)
    raw: dict[str, Any] = {}
    base_dir = Path.cwd()
    if cfg_path and cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text()) or {}
        base_dir = cfg_path.resolve().parent
    env = {**load_secrets_file(base_dir / ".secrets"), **os.environ}
    raw = _expand_env(raw, env)
    if overrides:
        raw = deep_merge(raw, overrides)
    raw["base_dir"] = str(base_dir)
    cfg = AppConfig.model_validate(raw)
    if env.get("DREAMPET_DATABASE_URL"):
        cfg.database_url = env["DREAMPET_DATABASE_URL"]
    if not cfg.api.admin_token:
        cfg.api.admin_token = env.get("DREAMPET_ADMIN_TOKEN")
    register_config_secrets(cfg, load_secrets_file(base_dir / ".secrets"))
    return cfg


_SECRET_ENV = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)")


def register_config_secrets(cfg: AppConfig, secrets_file: dict[str, str] | None = None) -> None:
    """Everything secret this process knows, so logs and events can mask it (dreampet.redact)."""
    vals: list[str | None] = [cfg.api.admin_token, url_password(cfg.database_url)]
    vals += [rc.api_key for rc in cfg.roles.values()]
    vals += [url_password(rc.url) for rc in cfg.roles.values()]
    vals += list((secrets_file or {}).values())
    vals += [v for k, v in os.environ.items() if _SECRET_ENV.search(k)]
    vals += [url_password(v) for k, v in os.environ.items() if k.endswith("_URL")]
    register_secrets(vals)


def deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out
