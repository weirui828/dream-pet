"""Side-by-side comparison of two simulation runs."""

from __future__ import annotations

from typing import Any

METRICS = [
    ("explorations", "explore sessions"),
    ("reads", "articles read"),
    ("chats", "chat turns"),
    ("dreams", "dreams"),
    ("weak_dreams", "weak dreams"),
    ("clusters", "topic clusters"),
    ("topic_entropy_bits", "topic diversity (bits)"),
    ("mean_satisfaction_per_read", "satisfaction / read"),
    ("mean_prediction_error", "prediction error"),
    ("persona_changes", "persona changes"),
    ("proactive_messages", "proactive messages"),
    ("est_usd", "estimated real cost ($)"),
    ("usd", "actual spend ($)"),
]


def compare_summaries(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for key, label in METRICS:
        va, vb = a.get(key, 0) or 0, b.get(key, 0) or 0
        rows.append({"metric": key, "label": label, "a": va, "b": vb, "delta": round(vb - va, 4)})
    for drive in ("boredom", "energy"):
        for stat in ("mean", "min", "max"):
            va, vb = a[drive][stat], b[drive][stat]
            rows.append({"metric": f"{drive}.{stat}", "label": f"{drive} {stat}", "a": va, "b": vb,
                         "delta": round(vb - va, 4)})
    return {"a": a.get("run_id"), "b": b.get("run_id"), "rows": rows,
            "top_topics": {"a": a.get("top_topics", []), "b": b.get("top_topics", [])}}
