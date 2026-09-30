"""dreampet — the command-line client.

Live commands talk to the running API (`dreampet up`); read-only ones fall back to the local
database when the API isn't running. Simulation runs locally."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml
from rich.console import Console
from rich.table import Table

from dreampet.config import AppConfig, find_config_path, load_config, load_secrets_file, repo_root

app = typer.Typer(help="Dream Pet: a self-hosted AI companion that gets bored, explores, chats and dreams.",
                  no_args_is_help=True)
sim_app = typer.Typer(help="Run and compare simulations.", no_args_is_help=True)
dream_app = typer.Typer(help="Look at and render dreams.", no_args_is_help=True)
fixtures_app = typer.Typer(help="Record and prune provider fixtures.", no_args_is_help=True)
app.add_typer(sim_app, name="sim")
app.add_typer(dream_app, name="dream")
app.add_typer(fixtures_app, name="fixtures")
console = Console()

SPARK = "▁▂▃▄▅▆▇█"


def _cfg(config: str | None = None) -> AppConfig:
    return load_config(config)


class Api:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        base = os.environ.get("DREAMPET_API") or f"http://{cfg.api.host}:{cfg.api.port}"
        headers = {"Authorization": f"Bearer {cfg.api.admin_token}"} if cfg.api.admin_token else {}
        self.http = httpx.Client(base_url=base + "/api/v1", headers=headers, timeout=httpx.Timeout(120, connect=3))

    def up(self) -> bool:
        try:
            return self.http.get("/health").status_code == 200
        except httpx.HTTPError:
            return False

    def get(self, path: str, **params) -> Any:
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict | None = None) -> Any:
        r = self.http.post(path, json=body or {})
        r.raise_for_status()
        return r.json()


def _need_api(cfg: AppConfig) -> Api:
    api = Api(cfg)
    if not api.up():
        console.print("[red]The pet isn't running.[/red] Start it with [bold]dreampet up[/bold].")
        raise typer.Exit(1)
    return api


# ---------------------------------------------------------------------------------------------
# init / up / down


DEFAULT_CONFIG = """\
# Dream Pet configuration. Keys come from env vars or .secrets (KEY=value), never the database.
data_dir: data
pet_id: pet
# database_url: postgresql://dreampet:dreampet@localhost:5432/dreampet   # default: SQLite in data_dir

# Every AI capability is a role with its own provider, model, key and budget.
# `provider: fake` is deterministic and free. Modes per role: fake | record | replay | live.
roles:
  explorer_llm:     { provider: fake }
  chat_llm:         { provider: fake }
  dreamer_llm:      { provider: fake }
  screenwriter_llm: { provider: fake }
  embeddings:       { provider: fake, dim: 256 }
  search:           { provider: fake }
  fetch:            { provider: fake }
  video:            { provider: fake }
  image:            { provider: fake }
  # Examples:
  # explorer_llm:     { provider: anthropic, model: "<model>", api_key: ${ANTHROPIC_API_KEY} }
  # chat_llm:         { provider: openai, model: "<model>", api_key: ${OPENAI_API_KEY} }
  # dreamer_llm:      { provider: anthropic, model: "<model>", api_key: ${ANTHROPIC_API_KEY}, temperature: 1.0 }
  # embeddings:       { provider: ollama, model: "<embed-model>", dim: 768, url: http://localhost:11434 }
  # search:           { provider: searxng, url: http://localhost:8080 }
  # fetch:            { provider: trafilatura }
  # video:            { provider: fal, model: "<video-model>", api_key: ${FAL_KEY}, max_seconds: 15, usd_per_second: 0.08 }
  # image:            { provider: fal, model: fal-ai/flux/schnell, api_key: ${FAL_KEY} }

budgets:
  daily_usd_hard_cap: 2.50
  per_role_usd: { explorer_llm: 0.60, chat_llm: 0.40, dreamer_llm: 0.30, video: 1.00 }

video:
  frequency: nightly   # nightly | best_of_week | stills_only | off
  total_seconds: 15
  shots: 3
  require_approval_over_usd: 1.00

api:
  host: 127.0.0.1
  port: 8000
  # admin_token comes from DREAMPET_ADMIN_TOKEN in .secrets

tracing: { provider: none }   # none | langfuse | langsmith
"""

DEFAULT_DRIVES = """\
# Drive tunables (hot-reloaded). Persona traits modulate these; the owner's advanced overrides win.
boredom:
  growth_rate: 0.08      # per sim-hour, 0.01-0.5
  theta_high: 0.7        # explore above this, 0.3-0.95 and > theta_low + 0.1
  theta_low: 0.3         # stop below this, 0.05-0.8
  chat_weight: 0.5       # 0-2
curiosity:
  novelty_k: 10          # 3-50
  episodic_alpha: 0.6    # 0-1: today vs all-time novelty
  habituation: 0.8       # 0.3-1
  lp_window: 6           # 3-20
  epsilon: 0.1           # 0-0.5: wildcard topic probability
  tau: 0.5               # 0.05-2: softmax temperature over learning progress
energy:
  max: 100               # 10-1000
  recovery_per_sleep_hour: 15   # 1-100
  nap_below: 10          # 0-50
"""


@app.command()
def init(
    preset: str = typer.Option("naturalist", help="physicist | poet | naturalist | historian | night_owl"),
    name: str | None = typer.Option(None, help="Pet name (defaults to the preset's)"),
    language: str | None = typer.Option(None, help="BCP 47 code, e.g. en, zh-CN"),
    timezone: str | None = typer.Option(None, help="IANA timezone, e.g. Australia/Sydney"),
    directory: Path = typer.Option(Path("."), "--dir", help="Where to write dreampet.yaml"),
):
    """Create config files and a pet from a preset."""
    from dreampet.persona.model import Persona, load_persona_file
    from dreampet.runtime.bootstrap import open_pet, open_repo

    directory.mkdir(parents=True, exist_ok=True)
    cfg_path = directory / "dreampet.yaml"
    if not cfg_path.exists():
        cfg_path.write_text(DEFAULT_CONFIG)
        console.print(f"wrote {cfg_path}")
    drives = directory / "drives.yaml"
    if not drives.exists():
        drives.write_text(DEFAULT_DRIVES)
        console.print(f"wrote {drives}")
    sec = directory / ".secrets"
    existing = load_secrets_file(sec)
    if "DREAMPET_ADMIN_TOKEN" not in existing:
        with sec.open("a") as f:
            f.write(f"DREAMPET_ADMIN_TOKEN={secrets.token_urlsafe(24)}\n")
        os.chmod(sec, 0o600)
        console.print(f"wrote admin token to {sec}")
    cfg = load_config(cfg_path)
    preset_file = repo_root() / "presets" / f"{preset}.yaml"
    if not preset_file.exists():
        console.print(f"[red]unknown preset {preset}[/red]")
        raise typer.Exit(1)
    persona = load_persona_file(preset_file)
    data = persona.model_dump()
    if name:
        data["name"] = name
    if language:
        data["language"] = language
    if timezone:
        data["timezone"] = timezone
    persona = Persona.model_validate(data)
    repo = open_repo(cfg)
    if repo.get_pet(cfg.pet_id):
        console.print(f"pet [bold]{cfg.pet_id}[/bold] already exists; leaving it alone")
        return
    opened = open_pet(cfg, repo=repo, persona=persona)
    console.print(f"created [bold]{opened.ctx.persona.name}[/bold] ({preset}) — start it with [bold]dreampet up[/bold]")
    if not persona.language.startswith("en") and cfg.role("embeddings").provider == "fake":
        console.print("[yellow]warning:[/yellow] the pet's language isn't English; use a multilingual embeddings model.")


def _pidfile(cfg: AppConfig) -> Path:
    return cfg.data_path / "dreampet.pid"


@app.command()
def up(
    host: str | None = None,
    port: int | None = None,
    worker: bool = typer.Option(True, help="Run the video worker in this process"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in the background"),
    config: str | None = typer.Option(None, help="Path to dreampet.yaml"),
):
    """Run the live pet (scheduler + API)."""
    cfg = _cfg(config)
    host = host or cfg.api.host
    port = port or cfg.api.port
    if detach:
        args = [sys.executable, "-m", "dreampet.cli.main", "up", "--host", host, "--port", str(port)]
        if not worker:
            args.append("--no-worker")
        if config:
            args += ["--config", config]
        cfg.data_path.mkdir(parents=True, exist_ok=True)
        log = open(cfg.data_path / "dreampet.log", "a")
        p = subprocess.Popen(args, stdout=log, stderr=log, start_new_session=True)
        console.print(f"started (pid {p.pid}); logs in {cfg.data_path / 'dreampet.log'}")
        return
    import uvicorn

    from dreampet.api.app import create_app

    if host not in ("127.0.0.1", "localhost") and not cfg.api.admin_token:
        console.print("[red]Refusing to bind beyond localhost without an admin token.[/red]")
        raise typer.Exit(1)
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    _pidfile(cfg).write_text(str(os.getpid()))
    try:
        uvicorn.run(create_app(cfg, with_worker=worker), host=host, port=port, log_level="info")
    finally:
        _pidfile(cfg).unlink(missing_ok=True)


@app.command()
def demo(
    preset: str = typer.Option("naturalist", help="Persona preset for the demo pet"),
    speed: float | None = typer.Option(None, help="Sim seconds per real second (120: a day in 12 minutes). "
                                         "Defaults to the last speed set in Settings, else 120"),
    port: int | None = None,
    fresh: bool = typer.Option(False, help="Start the demo pet over"),
    config: str | None = typer.Option(None),
):
    """Run a demo pet on an accelerated clock with free fake providers (separate data dir)."""
    import shutil

    import uvicorn

    from dreampet.api.app import create_app
    from dreampet.clock import RealClock, SimClock
    from dreampet.persona.model import load_persona_file

    cfg = _cfg(config)
    cfg.data_dir = str(cfg.data_path / "demo")
    cfg.database_url = None
    for name in list(cfg.roles):
        cfg.roles[name] = cfg.roles[name].model_copy(update={"mode": "fake"})
    if fresh and cfg.data_path.exists():
        shutil.rmtree(cfg.data_path)
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    stamp = cfg.data_path / "clock.txt"
    from datetime import datetime

    start = datetime.fromisoformat(stamp.read_text()) if stamp.exists() else RealClock().now()
    # the stamp is only written on a clean exit; after a crash, resume from the pet's newest
    # data so sim time never runs backwards over existing history
    db = cfg.data_path / "dreampet.sqlite"
    if db.exists():
        from dreampet.runtime.bootstrap import open_repo

        repo = open_repo(cfg)
        last = repo.last_sample(cfg.pet_id, "live")
        repo.close()
        if last and last["t"] > start:
            start = last["t"]
    speed_file = cfg.data_path / "clock_speed.txt"
    if speed is None:
        speed = float(speed_file.read_text()) if speed_file.exists() else 120.0
    clock = SimClock(start, speed=speed)
    persona = load_persona_file(repo_root() / "presets" / f"{preset}.yaml")
    console.print(f"demo pet on a {speed:g}x clock, data in {cfg.data_path}")
    app_ = create_app(cfg, clock=clock, persona=persona)
    try:
        uvicorn.run(app_, host=cfg.api.host, port=port or cfg.api.port, log_level="warning")
    finally:
        stamp.write_text(clock.now().isoformat())  # resume the demo where it left off


@app.command()
def down(config: str | None = typer.Option(None)):
    """Stop the live pet."""
    cfg = _cfg(config)
    pf = _pidfile(cfg)
    if not pf.exists():
        console.print("not running")
        return
    pid = int(pf.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"stopped (pid {pid})")
    except ProcessLookupError:
        console.print("stale pidfile removed")
        pf.unlink(missing_ok=True)


@app.command()
def worker(config: str | None = typer.Option(None)):
    """Run only the video worker (for a separate container)."""
    from dreampet.clock import RealClock
    from dreampet.runtime.bootstrap import open_pet
    from dreampet.scheduler.runtime import PetRuntime

    cfg = _cfg(config)
    rt = PetRuntime(open_pet(cfg, clock=RealClock()).ctx)
    console.print("video worker running; Ctrl-C to stop")
    try:
        rt.worker_loop()
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------------------------
# chat / status


@app.command()
def chat(conversation: str = typer.Option("main", help="Conversation id"), config: str | None = typer.Option(None)):
    """Chat with the pet in the terminal."""
    cfg = _cfg(config)
    api = _need_api(cfg)
    st = api.get(f"/pets/{cfg.pet_id}/status")
    name = st["pet"]["name"]
    unread = [m for m in api.get(f"/pets/{cfg.pet_id}/messages", conversation_id=conversation, limit=20)
              if not m["read"]]
    for m in unread:
        console.print(f"[magenta]{name}[/magenta] [dim](while you were away)[/dim]: {m['content']}")
    if unread:
        api.post(f"/pets/{cfg.pet_id}/messages/read")
    console.print(f"[dim]{name} is {st['state']}. Type /quit to leave.[/dim]")
    while True:
        try:
            msg = console.input("[bold cyan]you[/bold cyan]: ")
        except (EOFError, KeyboardInterrupt):
            break
        if msg.strip() in ("/quit", "/exit", "/q"):
            break
        if not msg.strip():
            continue
        console.print(f"[magenta]{name}[/magenta]: ", end="")
        with api.http.stream("POST", f"/pets/{cfg.pet_id}/chat",
                             json={"message": msg, "conversation_id": conversation, "stream": True}) as r:
            event = None
            done = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if event == "token":
                        console.print(data["text"], end="", soft_wrap=True, highlight=False)
                    elif event == "done":
                        done = data
        console.print()
        if done and done.get("recalled"):
            chips = ", ".join((r.get("title") or r["id"]) for r in done["recalled"])
            console.print(f"[dim]  recalled: {chips}[/dim]")


def _bar(v: float, lo: float, hi: float, width: int = 20) -> str:
    f = 0 if hi <= lo else max(0.0, min(1.0, (v - lo) / (hi - lo)))
    n = round(f * width)
    return "█" * n + "░" * (width - n)


@app.command()
def status(config: str | None = typer.Option(None), as_json: bool = typer.Option(False, "--json")):
    """Drives, state and today's usage."""
    cfg = _cfg(config)
    api = Api(cfg)
    if api.up():
        st = api.get(f"/pets/{cfg.pet_id}/status")
    else:
        st = _offline_status(cfg)
    if as_json:
        console.print_json(json.dumps(st, default=str))
        return
    d = st["drives"]
    ex = st.get("explain") or {}
    console.print(f"[bold]{st['pet']['name']}[/bold] is [bold magenta]{st['state']}[/bold magenta]"
                  + ("" if st.get("live", True) else " [dim](offline: last known state)[/dim]"))
    if st.get("activity"):
        console.print(f"  [dim]{st['activity']['line']}[/dim]")
    band = ex.get("band", {})
    console.print(f"  boredom {_bar(d['boredom'], 0, 1)} {d['boredom']:.2f}  "
                  f"(explore > {band.get('theta_high', '?')}, stop < {band.get('theta_low', '?')})")
    console.print(f"  energy  {_bar(d['energy'], 0, ex.get('energy_max', 100))} {d['energy']:.0f}/{ex.get('energy_max', 100):.0f}")
    console.print(f"  curiosity (best learning progress) {d['curiosity']:.3f}")
    t = Table("role", "calls", "tokens in", "tokens out", "usd", "est usd", title="usage today", title_justify="left")
    for u in st.get("usage_today", []):
        t.add_row(u["role"], str(u["calls"]), str(u["tokens_in"]), str(u["tokens_out"]), f"{u['usd']:.4f}",
                  f"{u['est_usd']:.4f}")
    console.print(t)
    console.print(f"  spent today ${st['spent_today']:.4f} of ${st['daily_cap']:.2f} hard cap")
    if st.get("unread"):
        console.print(f"  [yellow]{st['unread']} unread message(s)[/yellow] — dreampet chat")


def _offline_status(cfg: AppConfig) -> dict[str, Any]:
    from dreampet.runtime.bootstrap import open_repo

    repo = open_repo(cfg)
    pet = repo.get_pet(cfg.pet_id)
    if not pet:
        console.print("no pet yet — run [bold]dreampet init[/bold]")
        raise typer.Exit(1)
    last = repo.last_sample(cfg.pet_id, "live") or {"boredom": 0, "energy": 0, "curiosity": 0, "state": "unknown"}
    act = repo.last_event(cfg.pet_id, "live", ["activity"])
    return {"pet": {"name": pet["name"]}, "state": last["state"], "live": False,
            "drives": {"boredom": last["boredom"], "energy": last["energy"], "curiosity": last["curiosity"]},
            "activity": {"line": act["payload"]["line"]} if act else None, "usage_today": [],
            "spent_today": 0.0, "daily_cap": cfg.budgets.daily_usd_hard_cap, "unread": repo.unread_count(cfg.pet_id)}


# ---------------------------------------------------------------------------------------------
# simulation


def _sparkline(xs: list[float], lo: float, hi: float) -> str:
    out = []
    for x in xs:
        f = 0 if hi <= lo else max(0.0, min(0.999, (x - lo) / (hi - lo)))
        out.append(SPARK[int(f * len(SPARK))])
    return "".join(out)


def _hourly(samples: list[dict], key: str) -> dict[str, list[float]]:
    """Per local day, the value at each hour (last sample in the hour)."""
    from datetime import datetime

    days: dict[str, dict[int, float]] = {}
    for s in samples:
        t = datetime.fromisoformat(s["t"]) if isinstance(s["t"], str) else s["t"]
        days.setdefault(t.date().isoformat(), {})[t.hour] = s[key]
    out = {}
    for d, hours in days.items():
        row, last = [], None
        for h in range(24):
            last = hours.get(h, last)
            row.append(last if last is not None else 0.0)
        out[d] = row
    return out


def print_run(result: dict[str, Any], samples: list[dict] | None = None) -> None:
    s = result["summary"]
    console.rule(f"[bold]{s['scenario']}[/bold]  run {s['run_id']}  seed {s['seed']}  ({s['persona']})")
    if samples:
        b, e = _hourly(samples, "boredom"), _hourly(samples, "energy")
        emax = max((x["energy"] for x in samples), default=100) or 100
        console.print("[dim]drive curves, one character per hour (00→23)[/dim]")
        for d in sorted(b):
            console.print(f"  {d}  boredom [cyan]{_sparkline(b[d], 0, 1)}[/cyan]  energy [green]{_sparkline(e[d], 0, emax)}[/green]")
    t = Table("metric", "value", title="summary", title_justify="left")
    for k in ("explorations", "reads", "chats", "dreams", "weak_dreams", "clusters", "topic_entropy_bits",
              "mean_satisfaction_per_read", "mean_prediction_error", "persona_changes", "proactive_messages",
              "provider_errors", "est_usd", "usd"):
        t.add_row(k, str(s.get(k)))
    t.add_row("videos", ", ".join(f"{k}: {v}" for k, v in s.get("videos", {}).items()) or "-")
    t.add_row("top topics", ", ".join(f"{x['label']} ({x['size']})" for x in s.get("top_topics", [])[:5]))
    console.print(t)
    if result.get("chats"):
        for c in result["chats"]:
            console.print(f"  [cyan]day {c['day']} you[/cyan]: {c['message']}\n  [magenta]pet[/magenta]: {c['reply']}")
    if result.get("assertions"):
        at = Table("assert", "want", "got", "", title="assertions", title_justify="left")
        for a in result["assertions"]:
            at.add_row(a["assert"], json.dumps(a["want"]), json.dumps(a["got"]),
                       "[green]✓[/green]" if a["ok"] else "[red]✗[/red]")
        console.print(at)


@sim_app.command("run")
def sim_run(
    scenario: Path = typer.Argument(..., exists=True, help="Scenario YAML"),
    days: float | None = typer.Option(None, help="Override the scenario's days"),
    seed: int | None = typer.Option(None, help="Override the seed"),
    provider: list[str] = typer.Option([], "--provider", "-p", help="role=mode, e.g. dreamer_llm=live"),
    as_json: bool = typer.Option(False, "--json"),
    config: str | None = typer.Option(None),
):
    """Run a scenario (days of pet life in minutes) and check its assertions."""
    from dreampet.memory.repo import MemoryRepo
    from dreampet.sim.runner import SIM_PET, run_scenario
    from dreampet.sim.scenario import load_scenario

    cfg = _cfg(config)
    scn, base = load_scenario(scenario)
    if days is not None:
        scn.days = days
    if seed is not None:
        scn.seed = seed
    for p in provider:
        role, _, mode = p.partition("=")
        scn.providers[role] = mode

    def progress(p: dict[str, Any]) -> None:
        if not as_json and not p.get("done"):
            d = p.get("drives", {})
            console.print(f"[dim]  {p.get('day')}  boredom {d.get('boredom', 0):.2f}  energy {d.get('energy', 0):.0f}[/dim]")

    result = run_scenario(scn, cfg, base_dir=base, progress=progress)
    if as_json:
        console.print_json(json.dumps(result.to_dict(), default=str))
    else:
        repo = MemoryRepo(f"sqlite:///{result.run_dir / 'sim.sqlite'}", 256)
        samples = repo.samples(SIM_PET, result.run_id)
        repo.close()
        print_run(result.to_dict(), samples)
        console.print(f"[dim]run data: {result.run_dir}[/dim]")
    raise typer.Exit(0 if result.passed else 1)


def _load_summary(cfg: AppConfig, ref: str) -> dict[str, Any]:
    p = Path(ref)
    for cand in (p / "summary.json", p, cfg.data_path / "sim" / ref / "summary.json"):
        if cand.is_file():
            return json.loads(cand.read_text())["summary"]
    console.print(f"[red]no run {ref!r}[/red]")
    raise typer.Exit(1)


@sim_app.command("compare")
def sim_compare(run_a: str, run_b: str, config: str | None = typer.Option(None)):
    """Compare two runs side by side."""
    from dreampet.sim.compare import compare_summaries

    cfg = _cfg(config)
    cmp = compare_summaries(_load_summary(cfg, run_a), _load_summary(cfg, run_b))
    t = Table("metric", run_a, run_b, "Δ")
    for r in cmp["rows"]:
        delta = r["delta"]
        color = "green" if delta > 0 else "red" if delta < 0 else "dim"
        t.add_row(r["label"], str(r["a"]), str(r["b"]), f"[{color}]{delta:+g}[/{color}]")
    console.print(t)
    console.print("top topics A: " + ", ".join(x["label"] for x in cmp["top_topics"]["a"][:5]))
    console.print("top topics B: " + ", ".join(x["label"] for x in cmp["top_topics"]["b"][:5]))


@sim_app.command("list")
def sim_list(config: str | None = typer.Option(None)):
    """List past runs."""
    cfg = _cfg(config)
    root = cfg.data_path / "sim"
    t = Table("run", "scenario", "passed", "reads", "dreams", "est $")
    for d in sorted(root.glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
        r = json.loads(d.read_text())
        s = r["summary"]
        t.add_row(r["run_id"], s["scenario"], "✓" if r["passed"] else "✗", str(s["reads"]), str(s["dreams"]),
                  str(s["est_usd"]))
    console.print(t)


# ---------------------------------------------------------------------------------------------
# dreams


@dream_app.command("show")
def dream_show(night: str | None = typer.Option(None, help="YYYY-MM-DD"), config: str | None = typer.Option(None)):
    """Show last night's dream (or a given night's)."""
    cfg = _cfg(config)
    api = Api(cfg)
    if api.up():
        dreams = api.get(f"/pets/{cfg.pet_id}/dreams", **({"night": night} if night else {}), limit=1)
    else:
        from dreampet.api.app import create_app  # noqa: F401  (keeps import cost off the fast path)
        from dreampet.runtime.bootstrap import open_repo

        repo = open_repo(cfg)
        rows = repo.list_dreams(cfg.pet_id, night=night, limit=1)
        dreams = []
        for d in rows:
            ids = sorted({i for el in d["elements"] for i in el["memory_ids"]})
            mems = {m.id: m for m in repo.get_memories(ids)}
            job = repo.video_job_for_dream(d["id"])
            dreams.append({**d, "elements": [{**el, "memories": [
                {"id": i, "title": mems[i].title, "source_url": mems[i].source_url} for i in el["memory_ids"] if i in mems]}
                for el in d["elements"]], "video": job and {"status": job["status"], "url": job["file_uri"]}})
    if not dreams:
        console.print("no dreams yet")
        return
    d = dreams[0]
    console.rule(f"[bold]{d['title']}[/bold]  ·  night of {d['night']}  ·  mood: {d['mood']}"
                 + ("  ·  [yellow]weak[/yellow]" if d.get("weak") else ""))
    console.print(d["narrative"])
    console.print()
    for i, el in enumerate(d["elements"]):
        console.print(f"[bold]{i + 1}.[/bold] {el['text']}")
        for m in el.get("memories", []):
            console.print(f"     [dim]← {m.get('title') or m['id']}  {m.get('source_url') or ''}[/dim]")
    v = d.get("video")
    if v:
        console.print(f"\nvideo: {v.get('status')}  {v.get('url') or ''}")


@dream_app.command("render")
def dream_render(dream_id: str, config: str | None = typer.Option(None)):
    """(Re)render a dream's video now."""
    cfg = _cfg(config)
    api = Api(cfg)
    if api.up():
        out = api.post(f"/dreams/{dream_id}/render")
        console.print(f"queued video job {out['job_id']}")
        return
    from dreampet.clock import RealClock
    from dreampet.runtime.bootstrap import open_pet
    from dreampet.scheduler.runtime import PetRuntime

    rt = PetRuntime(open_pet(cfg, clock=RealClock()).ctx, run_video_inline=True, auto_approve_video=False)
    jid = rt.render_dream(dream_id)
    job = rt.ctx.repo.get_video_job(jid)
    console.print(f"job {jid}: {job['status']}  {job['file_uri'] or ''}")


# ---------------------------------------------------------------------------------------------
# fixtures


@fixtures_app.command("record")
def fixtures_record(scenario: Path = typer.Argument(..., exists=True),
                    days: float | None = typer.Option(None), config: str | None = typer.Option(None)):
    """Run a scenario with live providers, saving every request/response to fixtures/."""
    from dreampet.sim.runner import run_scenario
    from dreampet.sim.scenario import load_scenario

    cfg = _cfg(config)
    scn, base = load_scenario(scenario)
    if days is not None:
        scn.days = days
    scn.providers = {"default": "record", "video": "fake", "image": "fake"}
    result = run_scenario(scn, cfg, base_dir=base)
    console.print(f"recorded run {result.run_id}; est cost ${result.summary['est_usd']}, "
                  f"actual ${result.summary['usd']}")


@fixtures_app.command("prune")
def fixtures_prune(scenarios: list[Path] = typer.Argument(..., exists=True),
                   dry_run: bool = typer.Option(False), config: str | None = typer.Option(None)):
    """Delete fixtures no given scenario uses (each is replayed to find out)."""
    from dreampet.providers.fixtures import FixtureStore
    from dreampet.sim import runner
    from dreampet.sim.scenario import load_scenario

    cfg = _cfg(config)
    cfg.replay_fallback = "fake"
    used: set[str] = set()
    original = runner.open_pet

    def spy(*a, **kw):
        opened = original(*a, **kw)
        stores.append(opened.ctx.providers.fixtures)
        return opened

    stores: list[FixtureStore] = []
    runner.open_pet = spy
    try:
        for sc in scenarios:
            scn, base = load_scenario(sc)
            scn.providers = {"default": "replay"}
            runner.run_scenario(scn, cfg, base_dir=base, register=False)
    finally:
        runner.open_pet = original
    for s in stores:
        used |= s.used
    store = FixtureStore(cfg.path(cfg.fixtures_dir))
    unused = [p for p in store.all_files() if str(p.relative_to(store.root)) not in used]
    if dry_run:
        console.print(f"{len(unused)} of {len(store.all_files())} fixtures unused")
        return
    for p in unused:
        p.unlink()
    console.print(f"removed {len(unused)} unused fixtures; kept {len(used)}")


# ---------------------------------------------------------------------------------------------
# privacy


@app.command()
def export(out: Path = typer.Option(Path("dreampet-export.json")), config: str | None = typer.Option(None)):
    """Export everything the pet knows (memories, dreams, messages, persona) as JSON."""
    from fastapi.encoders import jsonable_encoder

    from dreampet.api.app import export_pet
    from dreampet.runtime.bootstrap import open_repo

    cfg = _cfg(config)
    data = export_pet(open_repo(cfg), cfg.pet_id)
    out.write_text(json.dumps(jsonable_encoder(data), indent=1, ensure_ascii=False))
    console.print(f"wrote {out}")


@app.command()
def wipe(yes: bool = typer.Option(False, "--yes", help="Really delete all of the pet's data"),
         config: str | None = typer.Option(None)):
    """Delete the pet and all its data (memories, dreams, messages, usage)."""
    from dreampet.runtime.bootstrap import open_repo

    cfg = _cfg(config)
    if not yes:
        console.print("This permanently deletes the pet's memories, dreams and messages. Re-run with --yes.")
        raise typer.Exit(1)
    if Api(cfg).up():
        console.print("stop the pet first: dreampet down")
        raise typer.Exit(1)
    open_repo(cfg).wipe_pet(cfg.pet_id)
    console.print("wiped. `dreampet init` creates a new pet.")


@app.command()
def persona(action: str = typer.Argument(..., help="export | import"), file: Path | None = None,
            config: str | None = typer.Option(None)):
    """Export or import the persona as YAML (to share personas)."""
    cfg = _cfg(config)
    api = _need_api(cfg)
    if action == "export":
        r = api.http.get(f"/pets/{cfg.pet_id}/persona/export")
        r.raise_for_status()
        if file:
            file.write_text(r.text)
            console.print(f"wrote {file}")
        else:
            console.print(r.text)
    elif action == "import":
        if not file:
            console.print("give a file")
            raise typer.Exit(1)
        r = api.http.post(f"/pets/{cfg.pet_id}/persona/import", content=file.read_text())
        r.raise_for_status()
        console.print(f"imported {yaml.safe_load(file.read_text()).get('persona', {}).get('name', 'persona')}")


@app.command("config-path")
def config_path():
    """Show which dreampet.yaml is in use."""
    console.print(str(find_config_path() or "(none; defaults: all-fake providers, SQLite in ./data)"))


if __name__ == "__main__":
    app()
