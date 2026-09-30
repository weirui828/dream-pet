"""Run summaries and scenario assertions."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from dreampet.memory.clustering import topic_entropy
from dreampet.memory.repo import MemoryRepo


def _stats(xs: list[float]) -> dict[str, float]:
    if not xs:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    return {"min": round(min(xs), 4), "max": round(max(xs), 4), "mean": round(sum(xs) / len(xs), 4)}


def summarize(repo: MemoryRepo, pet_id: str, run_id: str, tz) -> dict[str, Any]:
    samples = repo.samples(pet_id, run_id)
    events = repo.events(pet_id, run_id, limit=10_000_000)
    by_type: dict[str, int] = defaultdict(int)
    per_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for e in events:
        by_type[e["type"]] += 1
        day = e["t"].astimezone(tz).date().isoformat()
        if e["type"] in ("read", "chat", "explore_end", "dream_saved", "proactive", "persona_drift"):
            per_day[day][e["type"]] += 1
    for u in repo.usage(pet_id, run_id):
        per_day[u["day"]]["usd"] += u["usd"]
        per_day[u["day"]]["est_usd"] += u["est_usd"]
    dreams = repo.list_dreams(pet_id, limit=10000)
    grounded, bad = check_grounded(repo, dreams)
    jobs = repo.video_jobs(pet_id)
    usage = repo.usage(pet_id, run_id)
    by_role: dict[str, float] = defaultdict(float)
    for u in usage:
        by_role[u["role"]] += u["est_usd"]
    reads = [e for e in events if e["type"] == "read"]
    clusters = repo.list_clusters(pet_id)
    return {
        "samples": len(samples),
        "boredom": _stats([s["boredom"] for s in samples]),
        "energy": _stats([s["energy"] for s in samples]),
        "explorations": by_type.get("explore_end", 0),
        "reads": len(reads),
        "chats": by_type.get("chat", 0),
        "dreams": len(dreams),
        "weak_dreams": sum(1 for d in dreams if d["weak"]),
        "dreams_grounded": grounded,
        "ungrounded_elements": bad[:20],
        "videos": {s: sum(1 for j in jobs if j["status"] == s) for s in sorted({j["status"] for j in jobs})},
        "clusters": len(clusters),
        "topic_entropy_bits": round(topic_entropy(repo, pet_id), 4),
        "mean_satisfaction_per_read": round(sum(e["payload"].get("satisfaction", 0) for e in reads) / max(1, len(reads)), 4),
        "mean_prediction_error": round(sum(e["payload"].get("error", 0) for e in reads) / max(1, len(reads)), 4),
        "persona_changes": by_type.get("persona_drift", 0),
        "proactive_messages": by_type.get("proactive", 0),
        "provider_errors": by_type.get("provider_error", 0),
        "budget_refusals": by_type.get("budget_refused", 0),
        "usd": round(sum(u["usd"] for u in usage), 4),
        "est_usd": round(sum(u["est_usd"] for u in usage), 4),
        "est_usd_by_role": {k: round(v, 4) for k, v in sorted(by_role.items())},
        "per_day": {d: {k: round(v, 4) for k, v in sorted(vals.items())} for d, vals in sorted(per_day.items())},
        "top_topics": [{"label": c.label, "size": c.size, "lp": round(c.lp, 4)}
                       for c in sorted(clusters, key=lambda c: (-c.size, c.id))[:8]],
        "event_counts": dict(sorted(by_type.items())),
    }


def check_grounded(repo: MemoryRepo, dreams: list[dict]) -> tuple[bool, list[dict]]:
    bad = []
    for d in dreams:
        for i, el in enumerate(d["elements"]):
            ids = el.get("memory_ids") or []
            found = {m.id for m in repo.get_memories(ids)}
            if not ids or any(x not in found for x in ids):
                bad.append({"dream_id": d["id"], "element": i, "memory_ids": ids})
    return (not bad), bad


def check_assertions(summary: dict[str, Any], assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    per_day = summary.get("per_day", {})
    for a in assertions:
        for key, want in a.items():
            ok, got = _check(key, want, summary, per_day)
            out.append({"assert": key, "want": want, "got": got, "ok": ok})
    return out


def _check(key: str, want: Any, s: dict[str, Any], per_day: dict[str, dict[str, float]]) -> tuple[bool, Any]:
    if key == "boredom_within":
        got = [s["boredom"]["min"], s["boredom"]["max"]]
        return want[0] <= got[0] and got[1] <= want[1], got
    if key == "energy_within":
        got = [s["energy"]["min"], s["energy"]["max"]]
        return want[0] <= got[0] and got[1] <= want[1], got
    if key == "dreams_count":
        return s["dreams"] == want, s["dreams"]
    if key == "dreams_min":
        return s["dreams"] >= want, s["dreams"]
    if key == "every_dream_element_grounded":
        return s["dreams_grounded"] == bool(want), s["dreams_grounded"]
    if key == "daily_usd_max":
        got = max((d.get("usd", 0.0) for d in per_day.values()), default=0.0)
        return got <= want + 1e-9, round(got, 4)
    if key == "daily_est_usd_max":
        got = max((d.get("est_usd", 0.0) for d in per_day.values()), default=0.0)
        return got <= want + 1e-9, round(got, 4)
    if key == "explorations_min":
        return s["explorations"] >= want, s["explorations"]
    if key == "reads_min":
        return s["reads"] >= want, s["reads"]
    if key == "topic_entropy_min":
        return s["topic_entropy_bits"] >= want, s["topic_entropy_bits"]
    if key == "videos_rendered_min":
        got = s["videos"].get("done", 0)
        return got >= want, got
    if key == "no_provider_errors":
        return (s["provider_errors"] == 0) == bool(want), s["provider_errors"]
    if key == "proactive_max_per_day":
        got = max((d.get("proactive", 0) for d in per_day.values()), default=0)
        return got <= want, got
    return False, f"unknown assertion {key!r}"
