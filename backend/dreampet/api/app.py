"""FastAPI service (v1, /api/v1). The web UI and CLI are thin clients of this.

It runs the live pet (scheduler + video worker threads) in-process and streams live updates
over Server-Sent Events. v1 auth: one owner, one admin token; binds to localhost by default."""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from dreampet.clock import RealClock
from dreampet.config import AppConfig, deep_merge, load_config, repo_root
from dreampet.drives.params import DriveParams, apply_overrides
from dreampet.memory.repo import MemoryRepo
from dreampet.persona.model import Persona, dump_persona_yaml, load_persona_file
from dreampet.providers import registry
from dreampet.runtime.bootstrap import open_pet, open_repo, reembed_stale, stale_embedding_count
from dreampet.runtime.media import http_url
from dreampet.scheduler.runtime import PetRuntime
from dreampet.sim.runner import SIM_PET, run_scenario
from dreampet.sim.scenario import Scenario

API = "/api/v1"


class ChatIn(BaseModel):
    message: str
    conversation_id: str = "main"
    stream: bool = True


class ApproveIn(BaseModel):
    approved: bool


class MemoryPatch(BaseModel):
    content: str | None = None
    title: str | None = None
    importance: float | None = None


class SimRunIn(BaseModel):
    scenario: dict[str, Any] | None = None
    scenario_yaml: str | None = None
    scenario_file: str | None = None
    overrides: dict[str, Any] | None = None


class ClockIn(BaseModel):
    speed: float


MIN_SPEED, MAX_SPEED = 1.0, 10_000.0


class PresetIn(BaseModel):
    name: str


class AvatarSelect(BaseModel):
    uri: str


class State:
    cfg: AppConfig
    repo: MemoryRepo
    runtime: PetRuntime
    sim_threads: dict[str, threading.Thread]
    sim_progress: dict[str, dict[str, Any]]


def create_app(cfg: AppConfig | None = None, *, start_scheduler: bool = True, with_worker: bool = True,
               runtime: PetRuntime | None = None, clock=None, persona: Persona | None = None) -> FastAPI:
    cfg = cfg or load_config()
    st = State()
    st.cfg = cfg
    st.sim_threads = {}
    st.sim_progress = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if runtime is not None:
            st.runtime = runtime
            st.repo = runtime.ctx.repo
        else:
            st.repo = open_repo(cfg)
            opened = open_pet(cfg, repo=st.repo, clock=clock or RealClock(), persona=persona)
            st.runtime = PetRuntime(opened.ctx)
            stale = stale_embedding_count(opened.ctx)
            if stale["stale"]:
                threading.Thread(target=reembed_stale, args=(opened.ctx,), daemon=True, name="reembed").start()
            if start_scheduler:
                st.runtime.start_background(with_worker=with_worker)
        yield
        st.runtime.shutdown()

    app = FastAPI(title="Dream Pet", version="0.1.0", lifespan=lifespan)
    app.state.st = st
    app.add_middleware(CORSMiddleware, allow_origins=cfg.api.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    def auth(request: Request) -> None:
        token = cfg.api.admin_token
        if not token:
            return  # no token configured: localhost-only dev mode
        got = request.headers.get("authorization", "")
        got = got[7:] if got.lower().startswith("bearer ") else request.query_params.get("token", "")
        if not secrets.compare_digest(got or "", token):
            raise HTTPException(401, "missing or bad admin token")

    def ctx():
        return st.runtime.ctx

    def check_pet(pet_id: str):
        c = ctx()
        if pet_id not in (c.pet_id, "me"):
            raise HTTPException(404, "unknown pet")
        return c

    def J(obj: Any) -> JSONResponse:
        return JSONResponse(jsonable_encoder(obj, custom_encoder={bytes: lambda b: None}))

    # ---- health & meta ------------------------------------------------------------------------

    @app.get(f"{API}/health")
    def health():
        return {"ok": True}

    @app.get(f"{API}/pets", dependencies=[Depends(auth)])
    def pets():
        return J([{"id": p["id"], "name": p["name"], "language": p["language"]} for p in st.repo.list_pets()])

    # ---- status & live stream ----------------------------------------------------------------

    @app.get(API + "/pets/{pet_id}/status", dependencies=[Depends(auth)])
    def status(pet_id: str):
        c = check_pet(pet_id)
        last = c.repo.last_event(c.pet_id, c.run_id, ["activity"])
        meter = c.providers.meter
        return J({
            "pet": {"id": c.pet_id, "name": c.persona.name, "language": c.persona.language,
                    "avatar_url": http_url(c.persona.avatar_uri), "temperament": c.persona.temperament},
            "now": c.now(), "timezone": c.persona.timezone, "simulated": c.clock.simulated,
            "state": c.state.lifecycle, "drives": c.drives_snapshot(),
            "explain": c.drive_model.explain(c.state),
            "activity": {"line": last["payload"]["line"], "t": last["t"]} if last else None,
            "unread": c.repo.unread_count(c.pet_id),
            "usage_today": c.repo.usage(c.pet_id, c.run_id, meter.day()),
            "spent_today": round(meter.spent_today(), 4), "daily_cap": c.cfg.budgets.daily_usd_hard_cap,
            "schedule": {"bedtime": c.persona.bedtime, "wake_time": c.persona.wake_time},
            "counts": {"memories": c.repo.count_memories(c.pet_id), "dreams": len(c.repo.list_dreams(c.pet_id, limit=10000))},
            "pending_video_approvals": [
                {"job_id": j["id"], "dream_id": j["dream_id"], "est_usd": j["est_cost_usd"]}
                for j in c.repo.video_jobs(c.pet_id, statuses=["awaiting_approval"])],
        })

    @app.get(API + "/pets/{pet_id}/stream", dependencies=[Depends(auth)])
    async def stream(pet_id: str, request: Request):
        c = check_pet(pet_id)
        q = c.bus.subscribe(asyncio.get_running_loop())

        async def gen():
            try:
                yield {"event": "hello", "data": json.dumps({"state": c.state.lifecycle, "drives": c.drives_snapshot()})}
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(q.get(), timeout=15)
                    except TimeoutError:
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {"event": item["kind"], "data": json.dumps(jsonable_encoder(item["data"]))}
            finally:
                c.bus.unsubscribe(q)

        return EventSourceResponse(gen())

    @app.get(API + "/pets/{pet_id}/samples", dependencies=[Depends(auth)])
    def samples(pet_id: str, hours: float = Query(24.0, gt=0, le=24 * 365), max_points: int = Query(1500, ge=50, le=20000)):
        c = check_pet(pet_id)
        now = c.now()
        rows = c.repo.samples(c.pet_id, c.run_id, since=now - timedelta(hours=hours), until=now)
        return J(thin_samples(rows, max_points))

    @app.get(API + "/pets/{pet_id}/events", dependencies=[Depends(auth)])
    def events(pet_id: str, types: str | None = None, hours: float = 24.0, limit: int = 500):
        c = check_pet(pet_id)
        now = c.now()
        ev = c.repo.events(c.pet_id, c.run_id, types=types.split(",") if types else None,
                           since=now - timedelta(hours=hours), until=now, limit=limit, newest_first=True)
        return J(ev)

    # ---- chat ---------------------------------------------------------------------------------

    @app.post(API + "/pets/{pet_id}/chat", dependencies=[Depends(auth)])
    async def chat(pet_id: str, body: ChatIn):
        check_pet(pet_id)
        if not body.message.strip():
            raise HTTPException(400, "empty message")
        if not body.stream:
            out = await run_in_threadpool(st.runtime.chat, body.message[:4000], body.conversation_id)
            return J(out)

        async def gen():
            out = await run_in_threadpool(st.runtime.chat, body.message[:4000], body.conversation_id)
            words = out["reply"].split(" ")
            for i in range(0, len(words), 3):
                yield {"event": "token", "data": json.dumps({"text": " ".join(words[i:i + 3]) + " "})}
                await asyncio.sleep(0.02)
            yield {"event": "done", "data": json.dumps(jsonable_encoder(out))}

        return EventSourceResponse(gen())

    @app.get(API + "/pets/{pet_id}/messages", dependencies=[Depends(auth)])
    def messages(pet_id: str, conversation_id: str | None = None, limit: int = 100):
        c = check_pet(pet_id)
        return J(c.repo.messages(c.pet_id, conversation_id, limit=limit))

    @app.post(API + "/pets/{pet_id}/messages/read", dependencies=[Depends(auth)])
    def mark_read(pet_id: str):
        c = check_pet(pet_id)
        c.repo.mark_read(c.pet_id)
        return {"ok": True}

    # ---- memories -----------------------------------------------------------------------------

    @app.get(API + "/pets/{pet_id}/memories", dependencies=[Depends(auth)])
    def memories(pet_id: str, kind: str | None = None, q: str | None = None, cluster_id: str | None = None,
                 limit: int = Query(50, le=500), offset: int = 0, archived: bool = False):
        c = check_pet(pet_id)
        ms = c.repo.list_memories(c.pet_id, kinds=kind.split(",") if kind else None, q=q, cluster_id=cluster_id,
                                  limit=limit, offset=offset, include_archived=archived)
        labels = {cl.id: cl.label for cl in c.repo.list_clusters(c.pet_id)}
        return J([{**m.public(), "cluster": labels.get(m.cluster_id)} for m in ms])

    @app.get(API + "/memories/{mid}", dependencies=[Depends(auth)])
    def memory(mid: str):
        c = ctx()
        m = c.repo.get_memory(mid)
        if not m or m.pet_id != c.pet_id:
            raise HTTPException(404)
        links = c.repo.links([mid])
        others = {m2.id: m2 for m2 in c.repo.get_memories(
            sorted({lk["dst_id"] if lk["src_id"] == mid else lk["src_id"] for lk in links}))}
        return J({**m.public(), "links": [
            {**lk, "other": {"id": o.id, "title": o.title, "kind": o.kind} if (o := others.get(
                lk["dst_id"] if lk["src_id"] == mid else lk["src_id"])) else None} for lk in links]})

    @app.patch(API + "/memories/{mid}", dependencies=[Depends(auth)])
    def edit_memory(mid: str, body: MemoryPatch):
        c = ctx()
        m = c.repo.get_memory(mid)
        if not m or m.pet_id != c.pet_id:
            raise HTTPException(404)
        vals: dict[str, Any] = {k: v for k, v in body.model_dump().items() if v is not None}
        if "content" in vals:
            vals["embedding"] = c.providers.embeddings.embed_one(vals["content"])
            vals["embed_model"] = c.providers.embeddings.model_name
        c.repo.update_memory(mid, **vals)
        c.emit("memory_edited", {"memory_id": mid, "fields": sorted(vals)})
        return J(c.repo.get_memory(mid).public())

    @app.delete(API + "/memories/{mid}", dependencies=[Depends(auth)])
    def forget_memory(mid: str):
        c = ctx()
        m = c.repo.get_memory(mid)
        if not m or m.pet_id != c.pet_id:
            raise HTTPException(404)
        c.repo.delete_memory(mid)
        c.emit("memory_forgotten", {"memory_id": mid})
        return {"ok": True}

    @app.get(API + "/pets/{pet_id}/clusters", dependencies=[Depends(auth)])
    def clusters(pet_id: str):
        c = check_pet(pet_id)
        sizes = c.repo.cluster_sizes(c.pet_id)
        return J([{**cl.public(), "size": sizes.get(cl.id, 0)} for cl in c.repo.list_clusters(c.pet_id)])

    # ---- dreams & video -----------------------------------------------------------------------

    def dream_view(repo: MemoryRepo, d: dict[str, Any], token_q: str = "") -> dict[str, Any]:
        ids = sorted({i for el in d["elements"] for i in el.get("memory_ids", [])})
        mems = {m.id: m for m in repo.get_memories(ids)}
        job = repo.video_job_for_dream(d["id"])
        return {**d, "elements": [
            {**el, "memories": [{"id": i, "title": mems[i].title, "kind": mems[i].kind,
                                 "source_url": mems[i].source_url, "content": mems[i].content[:300]}
                                for i in el.get("memory_ids", []) if i in mems]} for el in d["elements"]],
            "video": None if not job else {
                "job_id": job["id"], "status": job["status"], "est_cost_usd": job["est_cost_usd"],
                "cost_usd": job["cost_usd"], "url": http_url(job["file_uri"]), "poster_url": http_url(job["poster_uri"]),
                "shot_map": job["shot_map"], "screenplay": job["screenplay"], "error": job["error"]}}

    @app.get(API + "/pets/{pet_id}/dreams", dependencies=[Depends(auth)])
    def dreams(pet_id: str, night: str | None = None, limit: int = 60):
        c = check_pet(pet_id)
        return J([dream_view(c.repo, d) for d in c.repo.list_dreams(c.pet_id, night=night, limit=limit)])

    @app.get(API + "/dreams/{did}", dependencies=[Depends(auth)])
    def dream(did: str):
        c = ctx()
        d = c.repo.get_dream(did)
        if not d or d["pet_id"] != c.pet_id:
            raise HTTPException(404)
        return J(dream_view(c.repo, d))

    @app.post(API + "/dreams/{did}/render", dependencies=[Depends(auth)])
    def render(did: str):
        c = ctx()
        if not c.repo.get_dream(did):
            raise HTTPException(404)
        return {"job_id": st.runtime.render_dream(did)}

    @app.post(API + "/video_jobs/{jid}/approve", dependencies=[Depends(auth)])
    async def approve(jid: str, body: ApproveIn):
        out = await run_in_threadpool(st.runtime.approve_video, jid, body.approved)
        if out is None:
            raise HTTPException(404)
        return J(out)

    # ---- persona & drives ---------------------------------------------------------------------

    @app.get(API + "/pets/{pet_id}/persona", dependencies=[Depends(auth)])
    def get_persona(pet_id: str):
        c = check_pet(pet_id)
        return J({"persona": c.persona.model_dump(mode="json"), "compiled": c.compiled.model_dump(mode="json"),
                  "avatar_url": http_url(c.persona.avatar_uri)})

    @app.patch(API + "/pets/{pet_id}/persona", dependencies=[Depends(auth)])
    def patch_persona(pet_id: str, body: dict[str, Any]):
        c = check_pet(pet_id)
        merged = deep_merge(c.persona.model_dump(mode="json"), body)
        if "interests" in body:
            merged["interests"] = body["interests"]
        if "locks" in body:
            merged["locks"] = body["locks"]
        try:
            p = Persona.model_validate(merged)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        with st.runtime._gate:
            old = c.persona.model_dump(mode="json")
            c.save_persona(p)
            from dreampet.memory.clustering import seed_clusters

            seed_clusters(c)
        c.emit("persona_edited", {"fields": sorted(k for k in body if old.get(k) != merged.get(k))})
        return get_persona(pet_id)

    @app.get(API + "/pets/{pet_id}/persona/export", dependencies=[Depends(auth)])
    def export_persona(pet_id: str):
        c = check_pet(pet_id)
        return PlainTextResponse(dump_persona_yaml(c.persona), media_type="application/yaml")

    @app.post(API + "/pets/{pet_id}/persona/import", dependencies=[Depends(auth)])
    async def import_persona(pet_id: str, request: Request):
        c = check_pet(pet_id)
        raw = yaml.safe_load(await request.body()) or {}
        try:
            p = Persona.model_validate(raw.get("persona", raw))
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        p.avatar_uri = c.persona.avatar_uri
        with st.runtime._gate:
            c.save_persona(p)
        c.emit("persona_imported", {"name": p.name})
        return get_persona(pet_id)

    @app.get(f"{API}/presets", dependencies=[Depends(auth)])
    def presets():
        out = []
        for f in sorted((repo_root() / "presets").glob("*.yaml")):
            p = load_persona_file(f)
            out.append({"id": f.stem, "name": p.name, "temperament": p.temperament,
                        "interests": [i.topic for i in p.interests], "persona": p.model_dump(mode="json")})
        return J(out)

    @app.post(API + "/pets/{pet_id}/persona/preset", dependencies=[Depends(auth)])
    def apply_preset(pet_id: str, body: PresetIn):
        c = check_pet(pet_id)
        f = repo_root() / "presets" / f"{Path(body.name).stem}.yaml"
        if not f.exists():
            raise HTTPException(404, "unknown preset")
        p = load_persona_file(f)
        p.avatar_uri = c.persona.avatar_uri
        with st.runtime._gate:
            c.save_persona(p)
            from dreampet.memory.clustering import seed_clusters

            seed_clusters(c)
        c.emit("persona_preset", {"preset": body.name})
        return get_persona(pet_id)

    @app.get(API + "/pets/{pet_id}/persona/changes", dependencies=[Depends(auth)])
    def persona_changes(pet_id: str):
        c = check_pet(pet_id)
        return J(c.repo.persona_changes(c.pet_id))

    @app.get(API + "/pets/{pet_id}/drives", dependencies=[Depends(auth)])
    def get_drives(pet_id: str):
        c = check_pet(pet_id)
        return J({"params": c.params.model_dump(), "flat": c.params.flat(), "bounds": DriveParams.bounds(),
                  "base": c.drives_file.get().flat(), "overrides": c.overrides, "trace": c.compiled.trace,
                  "model": c.drive_model.name, "drives_file_error": c.drives_file.last_error,
                  "explain": c.drive_model.explain(c.state)})

    @app.patch(API + "/pets/{pet_id}/drives", dependencies=[Depends(auth)])
    def patch_drives(pet_id: str, body: dict[str, Any]):
        """Owner overrides (advanced panel). Dotted keys; null removes an override."""
        c = check_pet(pet_id)
        new = dict(c.overrides)
        for k, v in body.items():
            if v is None:
                new.pop(k, None)
            else:
                new[k] = v
        try:
            apply_overrides(c.drives_file.get(), new)  # validate bounds
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        with st.runtime._gate:
            c.overrides = new
            c.refresh_params()
            row = c.repo.get_pet(c.pet_id)
            c.repo.update_pet(c.pet_id, drives_config={**(row.get("drives_config") or {}), "overrides": new})
        c.emit("drives_edited", {"overrides": new})
        return get_drives(pet_id)

    # ---- avatar -------------------------------------------------------------------------------

    @app.post(API + "/pets/{pet_id}/avatar/generate", dependencies=[Depends(auth)])
    async def avatar_generate(pet_id: str):
        c = check_pet(pet_id)
        p = c.persona
        prompt = (f"portrait of a small {p.temperament} creature named {p.name}, cute companion pet, "
                  f"{p.visual_style}, centered, simple background")

        def gen():
            imgs = c.providers.image.generate(prompt, 512, 512, n=4, seed=c.rng.randrange(1_000_000))
            out = []
            batch = c.new_id("av")
            for i, b in enumerate(imgs):
                rel = f"avatars/{batch}_{i}.png"
                c.media.local_path(rel).write_bytes(b)
                uri = c.media.publish(rel)
                out.append({"uri": uri, "url": http_url(uri)})
            return out

        return J(await run_in_threadpool(gen))

    @app.post(API + "/pets/{pet_id}/avatar/select", dependencies=[Depends(auth)])
    def avatar_select(pet_id: str, body: AvatarSelect):
        c = check_pet(pet_id)
        if not body.uri.startswith(("media://", "s3://")):
            raise HTTPException(400, "bad uri")
        p = Persona.model_validate({**c.persona.model_dump(), "avatar_uri": body.uri})
        c.save_persona(p)
        return {"avatar_url": http_url(body.uri)}

    # ---- media --------------------------------------------------------------------------------

    @app.get(API + "/media/{path:path}", dependencies=[Depends(auth)])
    def media(path: str):
        p = ctx().media.resolve("media://" + path)
        if p is None:
            # sim runs keep their own media dirs
            parts = path.split("/", 2)
            if len(parts) == 3 and parts[0] == "sim":
                cand = (cfg.data_path / "sim" / parts[1] / "media" / parts[2]).resolve()
                if str(cand).startswith(str((cfg.data_path / "sim").resolve())) and cand.exists():
                    return FileResponse(cand)
            raise HTTPException(404)
        return FileResponse(p)

    @app.get(API + "/media-s3/{path:path}", dependencies=[Depends(auth)])
    def media_s3(path: str):
        url = ctx().media.presigned("s3://" + path)
        if not url:
            raise HTTPException(404)
        return RedirectResponse(url)

    # ---- settings & usage ---------------------------------------------------------------------

    def clock_view() -> dict[str, Any]:
        clk = ctx().clock
        return {"simulated": clk.simulated, "now": clk.now(), "speed": getattr(clk, "speed", None) if clk.simulated else 1.0,
                "adjustable": clk.simulated, "min": MIN_SPEED, "max": MAX_SPEED}

    @app.get(f"{API}/clock", dependencies=[Depends(auth)])
    def get_clock():
        return J(clock_view())

    @app.patch(f"{API}/clock", dependencies=[Depends(auth)])
    def set_clock(body: ClockIn):
        """Change how fast a simulated (demo) pet lives. The real clock can't be changed."""
        c = ctx()
        if not c.clock.simulated:
            raise HTTPException(409, "this pet runs on the real clock; start it with `dreampet demo` for adjustable speed")
        if not MIN_SPEED <= body.speed <= MAX_SPEED:
            raise HTTPException(422, f"speed must be between {MIN_SPEED:g} and {MAX_SPEED:g}")
        c.clock.speed = body.speed
        (cfg.data_path / "clock_speed.txt").write_text(f"{body.speed:g}")  # the demo restarts at this speed
        c.emit("clock_speed", {"speed": body.speed})
        return J(clock_view())

    @app.get(f"{API}/settings", dependencies=[Depends(auth)])
    def settings():
        c = ctx()
        return J({"roles": cfg.public_roles(), "active": {k: r.describe() for k, r in c.providers.roles.items()},
                  "budgets": cfg.budgets.model_dump(), "video": cfg.video.model_dump(),
                  "scheduler": cfg.scheduler.model_dump(), "energy_costs": cfg.energy_costs.model_dump(),
                  "providers_available": registry.available(), "embeddings": stale_embedding_count(c),
                  "tracing": cfg.tracing.provider, "database": "postgres" if c.repo.dialect == "postgresql" else "sqlite",
                  "auth": bool(cfg.api.admin_token)})

    @app.get(f"{API}/usage", dependencies=[Depends(auth)])
    def usage(days: int = 14):
        c = ctx()
        rows = c.repo.usage(c.pet_id, c.run_id)
        return J(rows[-days * 12:])

    # ---- privacy ------------------------------------------------------------------------------

    @app.get(API + "/pets/{pet_id}/export", dependencies=[Depends(auth)])
    def export_all(pet_id: str):
        c = check_pet(pet_id)
        return J(export_pet(c.repo, c.pet_id))

    @app.post(API + "/pets/{pet_id}/wipe", dependencies=[Depends(auth)])
    def wipe(pet_id: str, confirm: str = ""):
        c = check_pet(pet_id)
        if confirm != c.pet_id:
            raise HTTPException(400, f"pass ?confirm={c.pet_id} to wipe")
        with st.runtime._gate:
            c.repo.wipe_pet(c.pet_id)
        return {"ok": True, "note": "restart the service to create a fresh pet"}

    # ---- simulation ---------------------------------------------------------------------------

    @app.get(f"{API}/scenarios", dependencies=[Depends(auth)])
    def scenarios():
        out = []
        for f in sorted((repo_root() / "scenarios").glob("*.yaml")):
            out.append({"file": f"scenarios/{f.name}", "yaml": f.read_text(), **(yaml.safe_load(f.read_text()) or {})})
        return J(out)

    @app.post(f"{API}/sim/runs", dependencies=[Depends(auth)])
    def start_sim(body: SimRunIn):
        if body.scenario_yaml:
            raw = yaml.safe_load(body.scenario_yaml)
        elif body.scenario_file:
            f = (repo_root() / body.scenario_file).resolve()
            if not str(f).startswith(str(repo_root())) or not f.exists():
                raise HTTPException(404, "scenario file not found")
            raw = yaml.safe_load(f.read_text())
        elif body.scenario:
            raw = body.scenario
        else:
            raise HTTPException(400, "give scenario, scenario_yaml or scenario_file")
        if body.overrides:
            raw = deep_merge(raw, body.overrides)
        try:
            scn = Scenario.model_validate(raw)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        running = [k for k, t in st.sim_threads.items() if t.is_alive()]
        if len(running) >= 2:
            raise HTTPException(429, "two simulations are already running")
        run_id = f"{scn.name.lower().replace(' ', '-')[:30]}-{secrets.token_hex(3)}"
        st.sim_progress[run_id] = {"fraction": 0.0}

        def work():
            try:
                run_scenario(scn, cfg, base_dir=repo_root(), run_id=run_id,
                             progress=lambda p: st.sim_progress.__setitem__(run_id, p))
            except Exception as exc:
                st.sim_progress[run_id] = {"error": str(exc)[:1000], "fraction": 1.0, "done": True}

        t = threading.Thread(target=work, name=f"sim-{run_id}", daemon=True)
        st.sim_threads[run_id] = t
        t.start()
        return {"run_id": run_id}

    def sim_row(run_id: str) -> dict[str, Any]:
        row = st.repo.get_sim_run(run_id)
        if not row:
            raise HTTPException(404)
        return row

    def sim_repo(run_id: str) -> MemoryRepo:
        row = sim_row(run_id)
        return MemoryRepo(f"sqlite:///{row['db_path']}", st.repo.dim)

    @app.get(f"{API}/sim/runs", dependencies=[Depends(auth)])
    def list_sims():
        rows = st.repo.list_sim_runs()
        return J([{"id": r["id"], "name": r["name"], "status": r["status"], "created_at": r["created_at"],
                   "passed": (r["summary"] or {}).get("passed"), "progress": st.sim_progress.get(r["id"]),
                   "headline": _headline((r["summary"] or {}).get("summary"))} for r in rows])

    @app.get(API + "/sim/runs/{run_id}", dependencies=[Depends(auth)])
    def get_sim(run_id: str):
        row = sim_row(run_id)
        return J({**row, "progress": st.sim_progress.get(run_id)})

    @app.get(API + "/sim/runs/{run_id}/samples", dependencies=[Depends(auth)])
    def sim_samples(run_id: str, every: int = 1):
        r = sim_repo(run_id)
        try:
            rows = r.samples(SIM_PET, run_id)
        finally:
            r.close()
        return J(rows[:: max(1, every)])

    @app.get(API + "/sim/runs/{run_id}/events", dependencies=[Depends(auth)])
    def sim_events(run_id: str, types: str | None = None, limit: int = 2000):
        r = sim_repo(run_id)
        try:
            return J(r.events(SIM_PET, run_id, types=types.split(",") if types else None, limit=limit))
        finally:
            r.close()

    @app.get(API + "/sim/runs/{run_id}/dreams", dependencies=[Depends(auth)])
    def sim_dreams(run_id: str):
        r = sim_repo(run_id)
        try:
            out = []
            for d in r.list_dreams(SIM_PET):
                v = dream_view(r, d)
                if v["video"]:
                    for k in ("url", "poster_url"):
                        if v["video"][k]:
                            v["video"][k] = v["video"][k].replace("/api/v1/media/", f"/api/v1/media/sim/{run_id}/")
                out.append(v)
            return J(out)
        finally:
            r.close()

    @app.get(f"{API}/sim/compare", dependencies=[Depends(auth)])
    def compare(a: str, b: str):
        from dreampet.sim.compare import compare_summaries

        sa = (sim_row(a)["summary"] or {}).get("summary")
        sb = (sim_row(b)["summary"] or {}).get("summary")
        if not sa or not sb:
            raise HTTPException(409, "both runs must be finished")
        return J(compare_summaries(sa, sb))

    @app.post(API + "/sim/runs/{run_id}/promote", dependencies=[Depends(auth)])
    def promote(run_id: str):
        """Promote a run's drive overrides and persona changes to the live pet."""
        row = sim_row(run_id)
        scn = Scenario.model_validate(row["scenario"])
        c = ctx()
        applied: dict[str, Any] = {}
        if scn.drives:
            patch_drives(c.pet_id, scn.drives)
            applied["drives"] = scn.drives
        if scn.persona_overrides:
            patch_persona(c.pet_id, {k: v for k, v in scn.persona_overrides.items() if k != "name"})
            applied["persona"] = scn.persona_overrides
        c.emit("sim_promoted", {"run_id": run_id, **applied})
        return J({"ok": True, "applied": applied})

    return app


def thin_samples(rows: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    """Downsample for long chart ranges, keeping every state change (so state bands stay
    exact) and the latest point."""
    if len(rows) <= max_points:
        return rows
    step = -(-len(rows) // max_points)
    return [r for i, r in enumerate(rows)
            if i % step == 0 or i == len(rows) - 1 or r["state"] != rows[i - 1]["state"]]


def _headline(s: dict[str, Any] | None) -> dict[str, Any] | None:
    if not s:
        return None
    return {k: s.get(k) for k in ("days", "explorations", "reads", "dreams", "topic_entropy_bits", "est_usd")}


def export_pet(repo: MemoryRepo, pet_id: str) -> dict[str, Any]:
    pet = repo.get_pet(pet_id)
    mems = repo.list_memories(pet_id, limit=1_000_000, include_archived=True)
    return {
        "pet": pet,
        "memories": [m.public() for m in mems],
        "links": repo.links([m.id for m in mems]),
        "clusters": [c.public() for c in repo.list_clusters(pet_id)],
        "dreams": repo.list_dreams(pet_id, limit=100000),
        "messages": repo.messages(pet_id, limit=1_000_000),
        "persona_changes": repo.persona_changes(pet_id, limit=100000),
        "usage": repo.usage(pet_id),
    }
