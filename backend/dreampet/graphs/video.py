"""video: screenwrite → approve? (interrupt) → generate_shots (fan-out via Send) → stitch → store.

A best-effort rendering of a saved dream. Shots over budget, failed shots (after one retry) and
rejected renders fall back to stills with a Ken Burns pan; with no ffmpeg or nothing rendered,
the dream stays text-only."""

from __future__ import annotations

import operator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt
from pydantic import BaseModel, Field

from dreampet import prompts
from dreampet.clock import real_sleep, wall_monotonic
from dreampet.dreams import ffmpeg
from dreampet.graphs.common import GraphState
from dreampet.providers.base import BudgetExceeded, ChatRequest, ProviderError, ShotRequest
from dreampet.schemas import Screenplay

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext

POLL_TIMEOUT_S = 15 * 60


class VideoState(GraphState):
    job_id: str = ""
    dream_id: str = ""
    screenplay: dict[str, Any] | None = None
    plan: list[dict[str, Any]] = Field(default_factory=list)
    est_cost: float = 0.0
    approved: bool | None = None
    results: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
    file_uri: str | None = None
    poster_uri: str | None = None
    duration: float = 0.0
    status: str = ""


class ShotTask(BaseModel):
    job_id: str
    shot: dict[str, Any]
    style: str


def build_video_graph(ctx: PetContext, checkpointer=None):
    repo = ctx.repo
    P = ctx.providers
    vc = ctx.cfg.video

    def _job_dir(job_id: str) -> str:
        return f"videos/{job_id}"

    def screenwrite(s: VideoState) -> dict:
        dream = repo.get_dream(s.dream_id)
        if dream is None:
            raise ValueError(f"dream {s.dream_id} not found")
        limits = P.video.limits
        n = max(1, min(vc.shots, len(dream["elements"]) or 1))
        total = min(vc.total_seconds, limits.max_total_seconds)
        shot_seconds = max(limits.min_shot_seconds, min(limits.max_shot_seconds, total / n))
        style = {"look": ctx.persona.visual_style, "palette": ctx.persona.palette[:4], "camera": "slow drift"}
        compact = {"title": dream["title"], "mood": dream["mood"],
                   "elements": [{"text": e["text"], "image_hint": e.get("image_hint", "")} for e in dream["elements"]]}
        try:
            sp: Screenplay = P.screenwriter.structured(ChatRequest(
                task="screenwrite",
                system=prompts.render(prompts.SCREENWRITE_SYSTEM, ctx, shots=n, shot_seconds=round(shot_seconds, 1)),
                user=prompts.screenwrite_user(compact, style), max_tokens=1200,
                context={"dream": compact, "style": style, "shots": n, "shot_seconds": shot_seconds},
            ), Screenplay)
        except (ProviderError, BudgetExceeded) as exc:
            ctx.emit("provider_error", {"role": "screenwriter_llm", "node": "screenwrite", "error": str(exc)[:300]})
            sp = Screenplay.model_validate({
                "title": dream["title"], "style_bible": style, "total_seconds": shot_seconds * n,
                "shots": [{"id": i + 1, "seconds": shot_seconds, "prompt": e.get("image_hint") or e["text"],
                           "element_ref": i} for i, e in enumerate(dream["elements"][:n])]})
        # clamp to provider limits and the configured length
        shots, used = [], 0.0
        for sh in sp.shots[:n]:
            secs = max(limits.min_shot_seconds, min(limits.max_shot_seconds, sh.seconds))
            if used + secs > total + 1e-6:
                secs = total - used
                if secs < limits.min_shot_seconds:
                    break
            used += secs
            shots.append({**sh.model_dump(), "seconds": round(secs, 2),
                          "element_ref": max(0, min(len(dream["elements"]) - 1, sh.element_ref))})
        screenplay = {**sp.model_dump(), "shots": shots, "total_seconds": round(used, 2),
                      "style_bible": sp.style_bible.model_dump()}
        # budget: shots whose cumulative estimate exceeds what's left fall back to stills
        remaining = P.video.meter.remaining("video")
        cap = ctx.cfg.budgets.per_role_usd.get("video", float("inf"))
        allowance = min(remaining, cap)
        plan, cum = [], 0.0
        for sh in shots:
            est = P.video.estimate(sh["seconds"])
            mode = "video"
            if vc.frequency == "stills_only":
                mode = "still"
            elif cum + est > allowance + 1e-9:
                mode = "still"
            else:
                cum += est
            plan.append({**sh, "mode": mode, "est_usd": round(est if mode == "video" else 0.0, 4)})
        repo.update_video_job(s.job_id, screenplay=screenplay, est_cost_usd=round(cum, 4), status="planned",
                              updated_at=ctx.now())
        ctx.emit("screenplay", {"job_id": s.job_id, "shots": len(plan), "est_usd": round(cum, 4),
                                "stills": sum(1 for p in plan if p["mode"] == "still")})
        return {"screenplay": screenplay, "plan": plan, "est_cost": round(cum, 4), **ctx.base_payload()}

    def approve(s: VideoState) -> dict:
        if s.approved is not None:
            return {}
        if not vc.approval_enabled or s.est_cost <= vc.require_approval_over_usd:
            return {"approved": True}
        repo.update_video_job(s.job_id, status="awaiting_approval", updated_at=ctx.now())
        ctx.emit("video_approval_needed", {"job_id": s.job_id, "est_usd": s.est_cost})
        decision = interrupt({"job_id": s.job_id, "est_usd": s.est_cost, "shots": len(s.plan)})
        ok = bool(decision.get("approved") if isinstance(decision, dict) else decision)
        repo.update_video_job(s.job_id, status="rendering" if ok else "rejected_stills", updated_at=ctx.now())
        return {"approved": ok}

    def fan_out(s: VideoState):
        repo.update_video_job(s.job_id, status="rendering", updated_at=ctx.now())
        look = (s.screenplay or {}).get("style_bible", {})
        style = f"{look.get('look', '')}, palette {' '.join(look.get('palette', []))}, {look.get('camera', '')}"
        tasks = []
        for sh in s.plan:
            shot = dict(sh)
            if not s.approved:
                shot["mode"] = "still"
            tasks.append(Send("generate_shot", ShotTask(job_id=s.job_id, shot=shot, style=style)))
        return tasks

    def generate_shot(t: ShotTask) -> dict:
        sh = t.shot
        prompt = f"{t.style}. {sh['prompt']}"  # the style bible is prepended to every shot
        base = _job_dir(t.job_id)
        out_rel = f"{base}/shot_{sh['id']:02d}.mp4"
        out = ctx.media.local_path(out_rel)
        result = {"id": sh["id"], "element_ref": sh["element_ref"], "seconds": sh["seconds"],
                  "transition": sh.get("transition", "dissolve"), "path": None, "kind": "missing", "error": None,
                  "usd": 0.0}
        if sh["mode"] == "video":
            for attempt in range(2):  # failed shots retry once
                try:
                    _render_video_shot(ctx, prompt, sh, out, seed=sh["id"] * 1000 + attempt)
                    result.update(path=str(out), kind="video", usd=sh["est_usd"] if P.video.mode in ("live", "record") else 0.0)
                    return {"results": [result]}
                except (ProviderError, BudgetExceeded) as exc:
                    result["error"] = str(exc)[:300]
                    ctx.emit("shot_failed", {"job_id": t.job_id, "shot": sh["id"], "attempt": attempt + 1,
                                             "error": result["error"]})
                    if isinstance(exc, BudgetExceeded):
                        break
        try:  # still + Ken Burns pan
            png = ctx.media.local_path(f"{base}/still_{sh['id']:02d}.png")
            imgs = P.image.generate(prompt, vc.width, vc.height, n=1, seed=sh["id"])
            if not imgs:
                raise ProviderError("image provider returned nothing")
            png.write_bytes(imgs[0])
            if not ffmpeg.available():
                raise ProviderError("ffmpeg not found")
            ffmpeg.ken_burns(png, out, sh["seconds"], vc.width, vc.height, vc.fps, zoom_in=sh["id"] % 2 == 1)
            result.update(path=str(out), kind="still")
        except (ProviderError, BudgetExceeded) as exc:
            result["error"] = str(exc)[:300]
        return {"results": [result]}

    def stitch(s: VideoState) -> dict:
        done = sorted((r for r in s.results if r["path"]), key=lambda r: r["id"])
        if not done or not ffmpeg.available():
            return {"status": "text_only"}
        base = _job_dir(s.job_id)
        normed = []
        try:
            for r in done:
                p = ctx.media.local_path(f"{base}/norm_{r['id']:02d}.mp4")
                ffmpeg.normalize(Path(r["path"]), p, r["seconds"], vc.width, vc.height, vc.fps)
                normed.append((p, r["seconds"]))
            final_rel = f"{base}/dream.mp4"
            duration = ffmpeg.stitch(normed, ctx.media.local_path(final_rel), audio_bed=vc.audio_bed)
            poster_rel = f"{base}/poster.jpg"
            ffmpeg.poster(ctx.media.local_path(final_rel), ctx.media.local_path(poster_rel), at=min(1.5, duration / 2))
        except ProviderError as exc:
            ctx.emit("stitch_failed", {"job_id": s.job_id, "error": str(exc)[:300]})
            return {"status": "text_only"}
        for p, _ in normed:
            p.unlink(missing_ok=True)
        return {"status": "done", "file_uri": ctx.media.publish(final_rel), "poster_uri": ctx.media.publish(poster_rel),
                "duration": round(duration, 2)}

    def store(s: VideoState) -> dict:
        dream = repo.get_dream(s.dream_id) or {"elements": []}
        done = sorted(s.results, key=lambda r: r["id"])
        shot_map, t = [], 0.0
        for i, r in enumerate(r for r in done if r["path"]):
            start = max(0.0, t - (ffmpeg.XFADE if i else 0.0))
            el = dream["elements"][r["element_ref"]] if r["element_ref"] < len(dream["elements"]) else {}
            shot_map.append({"shot_id": r["id"], "kind": r["kind"], "element_ref": r["element_ref"],
                             "memory_ids": el.get("memory_ids", []), "start": round(start, 2),
                             "end": round(start + r["seconds"], 2)})
            t = start + r["seconds"]
        cost = round(sum(r.get("usd", 0.0) for r in done), 4)
        status = s.status or "text_only"
        repo.update_video_job(s.job_id, status=status, file_uri=s.file_uri, poster_uri=s.poster_uri,
                              shot_map=shot_map, cost_usd=cost, updated_at=ctx.now(),
                              error=None if status == "done" else "; ".join(filter(None, (r.get("error") for r in done)))[:500] or None)
        ctx.emit("video_ready" if status == "done" else "video_text_only",
                 {"job_id": s.job_id, "dream_id": s.dream_id, "file_uri": s.file_uri, "cost_usd": cost,
                  "shots": {k: sum(1 for r in done if r["kind"] == k) for k in ("video", "still", "missing")},
                  "duration": s.duration})
        return {"status": status}

    g = StateGraph(VideoState)
    g.add_node("screenwrite", screenwrite)
    g.add_node("approve", approve)
    g.add_node("generate_shot", generate_shot, input_schema=ShotTask)
    g.add_node("stitch", stitch)
    g.add_node("store", store)
    g.add_edge(START, "screenwrite")
    g.add_edge("screenwrite", "approve")
    g.add_conditional_edges("approve", fan_out, ["generate_shot"])
    g.add_edge("generate_shot", "stitch")
    g.add_edge("stitch", "store")
    g.add_edge("store", END)
    return g.compile(checkpointer=checkpointer)


def _render_video_shot(ctx: PetContext, prompt: str, sh: dict, out: Path, seed: int) -> None:
    vc = ctx.cfg.video
    role = ctx.providers.video
    job = role.submit(ShotRequest(prompt=prompt, negative=sh.get("negative", ""), seconds=sh["seconds"],
                                  width=vc.width, height=vc.height, seed=seed))
    delay, started = 2.0, wall_monotonic()
    while True:
        status = role.poll(job)
        if status == "done":
            break
        if status == "failed":
            raise ProviderError(f"video job {job} failed")
        if wall_monotonic() - started > POLL_TIMEOUT_S:
            raise ProviderError(f"video job {job} timed out")
        real_sleep(delay)
        delay = min(15.0, delay * 1.5)
    role.download(job, out, seconds=sh["seconds"])
