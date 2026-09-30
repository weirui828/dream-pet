"""PetRuntime: the scheduler. Owns the lifecycle state machine and decides which graph runs.

States: idle, exploring, napping, asleep, dreaming. Transitions fire on drive thresholds and
the pet's schedule; an LLM never decides them. One graph runs per pet at a time, except chat,
which can interrupt explore at a node boundary (explore then resumes)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from langgraph.types import Command

from dreampet.drives.base import Event, Lifecycle
from dreampet.graphs.awake_tick import build_awake_tick_graph
from dreampet.graphs.chat import build_chat_graph
from dreampet.graphs.common import thread_config
from dreampet.graphs.explore import build_explore_graph
from dreampet.graphs.proactive import build_proactive_graph
from dreampet.graphs.sleep import build_sleep_graph
from dreampet.graphs.video import build_video_graph
from dreampet.runtime.context import PetContext
from dreampet.scheduler.lifecycle import in_window, is_night, next_bedtime, night_id, night_start


class PetRuntime:
    def __init__(self, ctx: PetContext, *, run_video_inline: bool = False, auto_approve_video: bool = False):
        self.ctx = ctx
        cp = ctx.checkpointer
        self.g_awake = build_awake_tick_graph(ctx)
        self.g_explore = build_explore_graph(ctx, cp)
        self.g_chat = build_chat_graph(ctx, cp)
        self.g_sleep = build_sleep_graph(ctx, cp)
        self.g_video = build_video_graph(ctx, cp)
        self.g_proactive = build_proactive_graph(ctx)
        self.run_video_inline = run_video_inline
        self.auto_approve_video = auto_approve_video

        self.stop = threading.Event()
        self._gate = threading.RLock()  # held while a node (or a tick, or a chat) executes
        self._cond = threading.Condition()
        self._chat_pending = 0
        self._video_lock = threading.Lock()
        self.between_steps: Callable[[], None] | None = None  # sim hook: deliver due scenario events
        self.proactive_queue: list[dict[str, Any]] = []
        self._finding_sent_session: str | None = None
        self._current_session: str | None = None
        self._cooldown_until = None
        self._threads: list[threading.Thread] = []
        ctx.hooks.update({"finding": self._on_finding, "drift": self._on_drift})

    # ------------------------------------------------------------------------------------------
    # main loop

    def run(self, until=None) -> None:
        self.resume_interrupted()
        ctx = self.ctx
        tick = timedelta(minutes=ctx.cfg.scheduler.tick_minutes)
        while not self.stop.is_set():
            start = ctx.now()
            if until is not None and start >= until:
                break
            if self.between_steps:
                self.between_steps()
            self.tick()
            if self.run_video_inline:
                self.process_video_jobs()
            target = start + tick
            if until is not None:
                target = min(target, until)
            if ctx.now() < target:
                ctx.clock.sleep_until(target, self.stop)

    def start_background(self, with_worker: bool = True) -> None:
        t = threading.Thread(target=self.run, name="dreampet-scheduler", daemon=True)
        t.start()
        self._threads.append(t)
        if with_worker:
            w = threading.Thread(target=self.worker_loop, name="dreampet-video-worker", daemon=True)
            w.start()
            self._threads.append(w)

    def shutdown(self, timeout: float = 10.0) -> None:
        self.stop.set()
        for t in self._threads:
            t.join(timeout)

    def worker_loop(self, interval: float = 5.0) -> None:
        while not self.stop.wait(interval):
            try:
                self.process_video_jobs()
            except Exception as exc:  # keep the worker alive; the job row records the failure
                self.ctx.emit("worker_error", {"error": str(exc)[:500]})

    # ------------------------------------------------------------------------------------------
    # one tick

    def tick(self) -> None:
        ctx = self.ctx
        p = ctx.persona
        with self._gate:
            ctx.refresh_params()  # drives.yaml hot reload
            now = ctx.now()
            state = ctx.state.lifecycle
            night = is_night(now, p.bedtime, p.wake_time, ctx.tz)
            explore = False
            if state in Lifecycle.AWAKE:
                if night:
                    self._go_to_sleep("bedtime")
                else:
                    out = self.g_awake.invoke({**ctx.base_payload(),
                                               "bedtime_at": next_bedtime(now, p.bedtime, ctx.tz).isoformat()})
                    nxt = out["next"]
                    if nxt == "sleep":
                        self._go_to_sleep("bedtime")
                    elif nxt == "nap":
                        ctx.set_lifecycle(Lifecycle.NAPPING, "low energy")
                    elif nxt == "explore":
                        if self._cooldown_until is None or now >= self._cooldown_until:
                            explore = True
                        elif state != Lifecycle.IDLE:
                            ctx.set_lifecycle(Lifecycle.IDLE, "cooling down")
                    elif state != Lifecycle.IDLE:
                        ctx.set_lifecycle(Lifecycle.IDLE, "settled")
            elif state == Lifecycle.NAPPING:
                ctx.step_drives()
                if night:
                    self._go_to_sleep("bedtime")
                elif ctx.state.energy >= ctx.params.energy.nap_below + ctx.cfg.scheduler.nap_wake_margin:
                    ctx.set_lifecycle(Lifecycle.IDLE, "rested")
            elif state in (Lifecycle.ASLEEP, Lifecycle.DREAMING):
                ctx.step_drives()
                if not night:
                    self._wake()
        # graphs that step node-by-node run outside the tick's gate so chat can slip in
        if explore:
            self.run_explore()
        elif ctx.state.lifecycle == Lifecycle.ASLEEP:
            self._maybe_dream()
        self._proactive_tick()

    # ------------------------------------------------------------------------------------------
    # graph execution

    def _wait_for_chats(self) -> None:
        with self._cond:
            while self._chat_pending > 0 and not self.stop.is_set():
                self._cond.wait(0.5)

    def _run_graph(self, graph, name: str, thread_id: str, inp: Any) -> dict[str, Any] | None:
        """Step a checkpointed graph one node at a time. Between nodes, pending chats run and sim
        events are delivered; on stop the checkpoint stays and resume_interrupted() continues it."""
        ctx = self.ctx
        cfg = thread_config(thread_id)
        if inp is not None:
            ctx.emit("graph_started", {"graph": name, "thread_id": thread_id})
        it = iter(graph.stream(inp, cfg, stream_mode="updates"))
        while True:
            self._wait_for_chats()
            with self._gate:
                try:
                    next(it)
                except StopIteration:
                    break
            if self.between_steps:
                self.between_steps()
            if self.stop.is_set():
                return None
        ctx.emit("graph_finished", {"graph": name, "thread_id": thread_id})
        return graph.get_state(cfg).values

    def run_explore(self) -> dict[str, Any] | None:
        ctx = self.ctx
        session = ctx.new_id("xs")
        self._current_session = session
        ctx.set_lifecycle(Lifecycle.EXPLORING, "bored")
        deadline = next_bedtime(ctx.now(), ctx.persona.bedtime, ctx.tz)
        out = self._run_graph(self.g_explore, "explore", f"explore:{ctx.pet_id}:{session}",
                              {**ctx.base_payload(), "session_id": session, "deadline": deadline.isoformat()})
        if out is not None:
            self._after_explore()
            if out.get("stop_reason") in ("nothing_new", "topics_exhausted") or out.get("reads", 0) == 0:
                self._cooldown_until = ctx.now() + timedelta(minutes=ctx.cfg.scheduler.explore_cooldown_minutes)
                ctx.emit("explore_cooldown", {"until": self._cooldown_until.isoformat(),
                                              "reason": out.get("stop_reason")})
        return out

    def _after_explore(self) -> None:
        ctx = self.ctx
        with self._gate:
            if ctx.state.lifecycle != Lifecycle.EXPLORING:
                return
            if ctx.state.energy <= ctx.params.energy.nap_below:
                ctx.set_lifecycle(Lifecycle.NAPPING, "tired after exploring")
            else:
                ctx.set_lifecycle(Lifecycle.IDLE, "done exploring")

    # ------------------------------------------------------------------------------------------
    # sleep, dreams, waking

    def _go_to_sleep(self, reason: str) -> None:
        self.ctx.set_lifecycle(Lifecycle.ASLEEP, reason)
        self.ctx.activity("asleep")

    def _dreamed(self, night: str) -> bool:
        ev = self.ctx.repo.events(self.ctx.pet_id, self.ctx.run_id, types=["sleep_done"], limit=20,
                                  newest_first=True)
        return any(e["payload"].get("night") == night for e in ev)

    def _maybe_dream(self) -> None:
        ctx = self.ctx
        p = ctx.persona
        now = ctx.now()
        start = night_start(now, p.bedtime, ctx.tz)
        night = night_id(now, p.bedtime, ctx.tz)
        if now < start + timedelta(minutes=ctx.cfg.scheduler.dream_after_bedtime_minutes) or self._dreamed(night):
            return
        self.run_sleep(night)

    def run_sleep(self, night: str) -> dict[str, Any] | None:
        ctx = self.ctx
        # "today" = since the last wake-up before this night
        since = ctx.today_start()
        start = night_start(ctx.now(), ctx.persona.bedtime, ctx.tz)
        if since > start:
            since = since - timedelta(days=1)
        with self._gate:
            ctx.set_lifecycle(Lifecycle.DREAMING, "REM")
        out = self._run_graph(self.g_sleep, "sleep", f"sleep:{ctx.pet_id}:{night}",
                              {**ctx.base_payload(), "night": night, "since": since.isoformat()})
        if out is None:
            return None
        with self._gate:
            if ctx.state.lifecycle == Lifecycle.DREAMING:
                ctx.set_lifecycle(Lifecycle.ASLEEP, "dream over")
        ctx.emit("sleep_done", {"night": night, "dream_id": out.get("dream_id"), "weak": out.get("weak"),
                                "drift": len(out.get("drift") or [])})
        if out.get("dream_id"):
            self._schedule_video(out["dream_id"], night)
        if self.run_video_inline:
            self.process_video_jobs()
        return out

    def _wake(self) -> None:
        ctx = self.ctx
        ctx.set_lifecycle(Lifecycle.IDLE, "morning")
        ctx.activity("just woke up")
        yesterday = night_id(ctx.now() - timedelta(hours=1), ctx.persona.bedtime, ctx.tz)
        dreams = ctx.repo.list_dreams(ctx.pet_id, night=yesterday, limit=1)
        if dreams:
            d = dreams[0]
            first = d["elements"][0]["text"] if d["elements"] else ""
            self._queue_proactive("wake_dream", {"dream_id": d["id"], "title": d["title"],
                                                 "first_scene": first[:1].lower() + first[1:].rstrip(".")},
                                  link={"kind": "dream", "id": d["id"]})
        if ctx.persona.traits.sociability >= 70:
            self._queue_proactive("silence", {"greeting": True})

    # ------------------------------------------------------------------------------------------
    # video

    def _schedule_video(self, dream_id: str, night: str) -> None:
        ctx = self.ctx
        freq = ctx.cfg.video.frequency
        if freq == "off":
            return
        if freq == "best_of_week":
            if date.fromisoformat(night).weekday() != 6:  # render on Sunday nights
                return
            since = ctx.now() - timedelta(days=7)
            week = [d for d in ctx.repo.list_dreams(ctx.pet_id, limit=14) if d["created_at"] >= since]
            if not week:
                return
            dream_id = max(week, key=lambda d: (d["score"], d["created_at"]))["id"]
        self.enqueue_video(dream_id)

    def enqueue_video(self, dream_id: str) -> str:
        ctx = self.ctx
        jid = ctx.new_id("vid")
        now = ctx.now()
        ctx.repo.add_video_job(id=jid, dream_id=dream_id, pet_id=ctx.pet_id, provider=ctx.providers.video.model_name,
                               status="queued", created_at=now, updated_at=now, thread_id=f"video:{ctx.pet_id}:{jid}")
        ctx.step_drives([Event(type="video", t=now, payload={"energy_cost": ctx.energy_cost("video")})])
        ctx.emit("video_queued", {"job_id": jid, "dream_id": dream_id})
        return jid

    def process_video_jobs(self) -> None:
        if not self._video_lock.acquire(blocking=False):
            return
        try:
            for job in self.ctx.repo.video_jobs(self.ctx.pet_id, statuses=["queued", "planned", "rendering"]):
                self._run_video(job)
        finally:
            self._video_lock.release()

    def _run_video(self, job: dict[str, Any]) -> None:
        ctx = self.ctx
        cfg = thread_config(job["thread_id"])
        snap = self.g_video.get_state(cfg)
        if snap.next and not snap.interrupts:
            self.g_video.invoke(None, cfg)  # resume after a crash
        elif not snap.next and not snap.values:
            self.g_video.invoke({**ctx.base_payload(), "job_id": job["id"], "dream_id": job["dream_id"]}, cfg)
        snap = self.g_video.get_state(cfg)
        if snap.interrupts and self.auto_approve_video:
            self.g_video.invoke(Command(resume={"approved": True}), cfg)
        elif not snap.next and snap.values and ctx.repo.get_video_job(job["id"])["status"] in ("queued", "planned"):
            ctx.repo.update_video_job(job["id"], status="failed", error="graph ended early", updated_at=ctx.now())

    def approve_video(self, job_id: str, approved: bool) -> dict[str, Any] | None:
        job = self.ctx.repo.get_video_job(job_id)
        if not job or job["status"] != "awaiting_approval":
            return job
        with self._video_lock:
            self.g_video.invoke(Command(resume={"approved": approved}), thread_config(job["thread_id"]))
        return self.ctx.repo.get_video_job(job_id)

    def render_dream(self, dream_id: str) -> str:
        """Owner asked to (re)render a dream now."""
        jid = self.enqueue_video(dream_id)
        if self.run_video_inline:
            self.process_video_jobs()
        return jid

    # ------------------------------------------------------------------------------------------
    # chat

    def chat(self, message: str, conversation_id: str = "main") -> dict[str, Any]:
        ctx = self.ctx
        with self._cond:
            self._chat_pending += 1
        try:
            with self._gate:
                mode = self._chat_mode()
                out = self.g_chat.invoke(
                    {**ctx.base_payload(), "conversation_id": conversation_id, "message": message, "mode": mode},
                    thread_config(f"chat:{ctx.pet_id}:{conversation_id}"),
                )
            return {"reply": out["reply"], "reply_id": out.get("reply_id"), "mode": mode,
                    "recalled": out.get("recalled", []), "drives": ctx.drives_snapshot()}
        finally:
            with self._cond:
                self._chat_pending -= 1
                self._cond.notify_all()

    def _chat_mode(self) -> str:
        ctx = self.ctx
        st = ctx.state.lifecycle
        if st == Lifecycle.DREAMING:
            return "sleeptalk"
        if st in (Lifecycle.ASLEEP, Lifecycle.NAPPING):
            if ctx.persona.sleep_chat == "wake":
                ctx.set_lifecycle(Lifecycle.IDLE, "woken by chat")
                return "grumpy"
            return "sleeptalk"
        if ctx.state.energy <= ctx.params.energy.nap_below * 1.5:
            return "tired"
        return "awake"

    # ------------------------------------------------------------------------------------------
    # proactive messages

    def _on_finding(self, payload: dict[str, Any]) -> None:
        if self._finding_sent_session == self._current_session:
            return
        self._finding_sent_session = self._current_session
        self._queue_proactive("finding", payload, link={"kind": "memory", "id": payload["memory_id"]})

    def _on_drift(self, payload: dict[str, Any]) -> None:
        self._queue_proactive("drift", payload)

    def _queue_proactive(self, trigger: str, detail: dict[str, Any], link: dict | None = None) -> None:
        now = self.ctx.now()
        self.proactive_queue.append({"trigger": trigger, "detail": detail, "link": link,
                                     "expires": now + timedelta(hours=12)})

    def _silence_check(self) -> None:
        ctx = self.ctx
        if ctx.state.lifecycle not in Lifecycle.AWAKE:
            return
        now = ctx.now()
        last = ctx.repo.last_owner_message(ctx.pet_id)
        pet = ctx.repo.get_pet(ctx.pet_id)
        since = last["t"] if last else (pet["created_at"] if pet else now)
        if now - since < timedelta(hours=ctx.compiled.silence_hours):
            return
        recent = ctx.repo.messages(ctx.pet_id, since=since, limit=50)
        if any(m["proactive"] == "silence" for m in recent) or any(q["trigger"] == "silence" for q in self.proactive_queue):
            return
        self._queue_proactive("silence", {"hours": round((now - since).total_seconds() / 3600, 1)})

    def _proactive_tick(self) -> None:
        ctx = self.ctx
        p = ctx.persona
        if not p.proactive.enabled:
            self.proactive_queue.clear()
            return
        self._silence_check()
        if not self.proactive_queue:
            return
        now = ctx.now()
        self.proactive_queue = [q for q in self.proactive_queue if q["expires"] > now]
        if ctx.state.lifecycle not in Lifecycle.AWAKE:
            return
        if in_window(now, p.proactive.quiet_hours[0], p.proactive.quiet_hours[1], ctx.tz):
            return
        day_start = now - timedelta(hours=24)
        while self.proactive_queue:
            if ctx.repo.count_proactive(ctx.pet_id, day_start) >= ctx.compiled.proactive_per_day:
                dropped = [q["trigger"] for q in self.proactive_queue]
                self.proactive_queue.clear()
                ctx.emit("proactive_suppressed", {"triggers": dropped, "why": "daily limit"})
                return
            if ctx.state.energy <= ctx.params.energy.nap_below + ctx.energy_cost("proactive"):
                return
            q = self.proactive_queue.pop(0)
            with self._gate:
                out = self.g_proactive.invoke({**ctx.base_payload(), "trigger": q["trigger"], "detail": q["detail"]})
                text = out.get("text")
                if not text:
                    continue
                mid = ctx.new_id("msg")
                ctx.repo.add_message(id=mid, pet_id=ctx.pet_id, conversation_id="main", t=ctx.now(), sender="pet",
                                     content=text, recalled=[], proactive=q["trigger"], link=q["link"], read=False)
                ctx.step_drives([Event(type="proactive", t=ctx.now(),
                                       payload={"energy_cost": ctx.energy_cost("proactive")})])
                ctx.emit("proactive", {"trigger": q["trigger"], "message_id": mid, "text": text})
                ctx.bus.publish("message", {"id": mid, "conversation_id": "main", "sender": "pet", "content": text,
                                            "proactive": q["trigger"], "link": q["link"], "t": ctx.now().isoformat()})

    # ------------------------------------------------------------------------------------------
    # crash recovery

    def resume_interrupted(self) -> None:
        """Resume any graph that was mid-run when the process stopped, from its checkpoint."""
        ctx = self.ctx
        if ctx.checkpointer is None:
            return
        ev = ctx.repo.events(ctx.pet_id, ctx.run_id, types=["graph_started", "graph_finished"], limit=40,
                             newest_first=True)
        finished = {e["payload"]["thread_id"] for e in ev if e["type"] == "graph_finished"}
        for e in ev:
            if e["type"] != "graph_started" or e["payload"]["thread_id"] in finished:
                continue
            name, thread = e["payload"]["graph"], e["payload"]["thread_id"]
            graph = {"explore": self.g_explore, "sleep": self.g_sleep}.get(name)
            if graph is None:
                continue
            snap = graph.get_state(thread_config(thread))
            if not snap.next:
                continue
            ctx.emit("graph_resumed", {"graph": name, "thread_id": thread, "next": list(snap.next)})
            if name == "explore":
                self._current_session = thread.rsplit(":", 1)[-1]
                with self._gate:
                    if ctx.state.lifecycle != Lifecycle.EXPLORING:
                        ctx.set_lifecycle(Lifecycle.EXPLORING, "resuming after restart")
                if self._run_graph(graph, name, thread, None) is not None:
                    self._after_explore()
            else:
                out = self._run_graph(graph, name, thread, None)
                if out is not None:
                    with self._gate:
                        if ctx.state.lifecycle == Lifecycle.DREAMING:
                            ctx.set_lifecycle(Lifecycle.ASLEEP, "dream over")
                    night = thread.rsplit(":", 1)[-1]
                    ctx.emit("sleep_done", {"night": night, "dream_id": out.get("dream_id")})
                    if out.get("dream_id"):
                        self._schedule_video(out["dream_id"], night)
            break  # only one graph can have been active
