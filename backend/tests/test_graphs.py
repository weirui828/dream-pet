"""Each graph against fake providers, with golden outputs (UPDATE_GOLDEN=1 to regenerate)."""

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from langgraph.types import Command

from dreampet.dreams import ffmpeg
from dreampet.dreams.weave import critique
from dreampet.drives.base import Lifecycle
from dreampet.graphs.common import thread_config

GOLDEN = Path(__file__).parent / "golden"


def golden(name: str, data):
    GOLDEN.mkdir(exist_ok=True)
    data = json.loads(json.dumps(data))
    p = GOLDEN / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN") or not p.exists():
        p.write_text(json.dumps(data, indent=1, sort_keys=True))
    assert data == json.loads(p.read_text()), f"golden mismatch for {name}; UPDATE_GOLDEN=1 to accept"


def _explore(runtime):
    ctx = runtime.ctx
    ctx.state.boredom = 0.9
    return runtime.run_explore()


def test_explore_reads_and_satisfies(runtime):
    ctx = runtime.ctx
    out = _explore(runtime)
    reads = ctx.repo.events(ctx.pet_id, ctx.run_id, types=["read"])
    assert out["reads"] == len(reads) > 0
    assert ctx.state.lifecycle in (Lifecycle.IDLE, Lifecycle.NAPPING)
    assert out["stop_reason"] in ("satisfied", "session_cap", "tired", "nothing_new", "topics_exhausted")
    mem = ctx.repo.get_memory(reads[0]["payload"]["memory_id"])
    assert mem.source_url and mem.cluster_id and 0 <= mem.surprise <= 2
    for k in ("novelty", "learnability", "habituation", "satisfaction", "prediction"):
        assert k in mem.meta
    golden("explore", {
        "reads": [(r["payload"]["title"], r["payload"]["cluster"]) for r in reads],
        "stop_reason": out["stop_reason"],
        "boredom": round(ctx.state.boredom, 3),
    })


def test_chat_uses_memories_and_threads(runtime):
    _explore(runtime)
    ctx = runtime.ctx
    out = runtime.chat("what did you learn about black holes?", "c1")
    assert out["reply"] and out["mode"] == "awake"
    runtime.chat("and anything else?", "c1")
    msgs = ctx.repo.messages(ctx.pet_id, "c1")
    assert [m["sender"] for m in msgs] == ["owner", "pet", "owner", "pet"]
    snap = runtime.g_chat.get_state(thread_config(f"chat:{ctx.pet_id}:c1"))
    assert len(snap.values["history"]) == 4  # the thread remembers the conversation
    golden("chat", {"reply": out["reply"], "recalled": [r["title"] for r in out["recalled"]]})


def test_chat_while_asleep_sleeptalks_or_wakes(runtime):
    ctx = runtime.ctx
    ctx.set_lifecycle(Lifecycle.ASLEEP, "test")
    assert runtime.chat("are you awake?")["mode"] == "sleeptalk"
    assert ctx.state.lifecycle == Lifecycle.ASLEEP
    ctx.persona.sleep_chat = "wake"
    e0 = ctx.state.energy
    assert runtime.chat("wake up!")["mode"] == "grumpy"
    assert ctx.state.lifecycle == Lifecycle.IDLE
    assert ctx.state.energy < e0


def test_sleep_makes_a_grounded_dream(runtime):
    ctx = runtime.ctx
    for _ in range(2):
        _explore(runtime)
        ctx.clock.advance(timedelta(hours=3))
    ctx.clock.advance(timedelta(hours=10))
    out = runtime.run_sleep("2026-10-01")
    dream = ctx.repo.get_dream(out["dream_id"])
    assert 3 <= len(dream["elements"]) <= 6 and dream["mood"]
    ids = {i for el in dream["elements"] for i in el["memory_ids"]}
    assert len(ctx.repo.get_memories(sorted(ids))) == len(ids)
    for i in ids:
        assert ctx.repo.has_link_kind(i, "dream_used")
    golden("dream", {"title": dream["title"], "mood": dream["mood"], "n": len(dream["elements"])})


def test_critic_catches_ungrounded_and_copying():
    src = {"m1": "The quick brown fox jumps over the lazy dog near the old stone bridge by the river bank today"}
    base = {"mood": "eerie", "narrative": "word " * 200,
            "elements": [{"text": "a", "memory_ids": ["m1"]}] * 3}
    assert critique(base, {"m1"}, src)["ok"]
    bad = {**base, "elements": [{"text": "a", "memory_ids": ["nope"]}] * 3}
    assert not critique(bad, {"m1"}, src)["ok"]
    copied = {**base, "narrative": base["narrative"] + src["m1"]}
    r = critique(copied, {"m1"}, src)
    assert not r["ok"] and r["max_verbatim"] > 12


@pytest.mark.skipif(not ffmpeg.available(), reason="ffmpeg not installed")
def test_video_renders_with_budget_fallback(runtime):
    ctx = runtime.ctx
    _explore(runtime)
    ctx.clock.advance(timedelta(hours=14))
    out = runtime.run_sleep("2026-10-01")
    job = ctx.repo.video_job_for_dream(out["dream_id"])
    assert job["status"] == "done", job
    kinds = [s["kind"] for s in job["shot_map"]]
    # default: 3 × 5 s at $0.08/s = $1.20 > $1.00 cap -> the last shot falls back to a still
    assert kinds.count("still") == 1 and kinds.count("video") == 2
    path = ctx.media.resolve(job["file_uri"])
    assert path and path.stat().st_size > 1000
    assert job["cost_usd"] == 0  # fake provider: estimated, never charged


@pytest.mark.skipif(not ffmpeg.available(), reason="ffmpeg not installed")
def test_video_approval_interrupt(runtime):
    ctx = runtime.ctx
    ctx.cfg.video.require_approval_over_usd = 0.1
    runtime.auto_approve_video = False
    _explore(runtime)
    ctx.clock.advance(timedelta(hours=14))
    out = runtime.run_sleep("2026-10-01")
    job = ctx.repo.video_job_for_dream(out["dream_id"])
    assert job["status"] == "awaiting_approval"
    runtime.approve_video(job["id"], False)  # rejected -> free stills
    job = ctx.repo.get_video_job(job["id"])
    assert job["status"] == "done" and {s["kind"] for s in job["shot_map"]} == {"still"}
    _ = Command  # (resume path covered via approve_video)
