from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dreampet.config import AppConfig


class GraphState(BaseModel):
    """Fields every graph state shares."""

    pet_id: str = ""
    sim_time: str = ""
    drives: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)


def make_checkpointer(cfg: AppConfig, kind: str | None = None, sqlite_path: Path | None = None):
    """PostgresSaver in production, SqliteSaver for dev, InMemorySaver when asked (fast sims)."""
    url = cfg.resolved_database_url()
    kind = kind or cfg.checkpoints or ("postgres" if url.startswith("postgres") else "sqlite")
    if kind == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    if kind == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row

        pg_url = url.replace("postgresql+psycopg://", "postgresql://")
        conn = Connection.connect(pg_url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
        saver = PostgresSaver(conn)
        saver.setup()
        return saver
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = sqlite_path or (cfg.data_path / "checkpoints.sqlite")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def thread_config(thread_id: str, **extra: Any) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 200, **extra}
