"""Tables. One schema for both backends: Postgres + pgvector in production, SQLite + sqlite-vec
for dev and simulation. Embeddings are pgvector `vector(dim)` on Postgres and float32 blobs on
SQLite (sqlite-vec reads those directly)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

JSONType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, returns aware UTC (SQLite drops tzinfo otherwise)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to the database; use the Clock")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Embedding(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def __init__(self, dim: int | None = None):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(LargeBinary())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        arr = np.asarray(value, dtype=np.float32)
        if dialect.name == "postgresql":
            return arr
        return arr.tobytes()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return np.asarray(value, dtype=np.float32)
        return np.frombuffer(value, dtype=np.float32).copy()


class Schema:
    def __init__(self, dim: int):
        self.dim = dim
        md = self.metadata = MetaData()

        self.pets = Table(
            "pets", md,
            Column("id", String, primary_key=True),
            Column("owner_id", String, nullable=False, default="owner"),
            Column("name", String, nullable=False),
            Column("language", String, nullable=False, default="en"),
            Column("persona", JSONType, nullable=False),
            Column("drives_config", JSONType, nullable=False, default=dict),
            Column("created_at", UTCDateTime, nullable=False),
        )

        self.memories = Table(
            "memories", md,
            Column("id", String, primary_key=True),
            Column("pet_id", String, nullable=False),
            Column("kind", String, nullable=False),  # episodic, semantic, insight, dream, chat
            Column("title", Text),
            Column("content", Text, nullable=False),
            Column("embedding", Embedding(dim)),
            Column("embed_model", String),
            Column("source_url", Text),
            Column("importance", Float, nullable=False, default=0.5),
            Column("surprise", Float, nullable=False, default=0.0),
            Column("salience", Float, nullable=False, default=0.0),
            Column("strength", Float, nullable=False, default=1.0),
            Column("access_count", Integer, nullable=False, default=0),
            Column("last_accessed_at", UTCDateTime),
            Column("created_at", UTCDateTime, nullable=False),
            Column("cluster_id", String),
            Column("archived", Boolean, nullable=False, default=False),
            Column("meta", JSONType, nullable=False, default=dict),
            Index("ix_memories_pet_created", "pet_id", "created_at"),
            Index("ix_memories_cluster", "cluster_id"),
        )

        self.memory_links = Table(
            "memory_links", md,
            Column("src_id", String, nullable=False),
            Column("dst_id", String, nullable=False),
            Column("kind", String, nullable=False),  # derived_from, similar, co_occurred, dream_used
            Column("weight", Float, nullable=False, default=1.0),
            PrimaryKeyConstraint("src_id", "dst_id", "kind"),
            Index("ix_links_dst", "dst_id"),
        )

        self.topic_clusters = Table(
            "topic_clusters", md,
            Column("id", String, primary_key=True),
            Column("pet_id", String, nullable=False),
            Column("label", Text, nullable=False),
            Column("centroid", Embedding(dim)),
            Column("error_history", JSONType, nullable=False, default=list),
            Column("lp", Float, nullable=False, default=0.0),
            Column("visits", Integer, nullable=False, default=0),
            Column("size", Integer, nullable=False, default=0),
            Column("seed", Boolean, nullable=False, default=False),
            Column("created_at", UTCDateTime, nullable=False),
        )

        self.drive_samples = Table(
            "drive_samples", md,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("pet_id", String, nullable=False),
            Column("run_id", String, nullable=False),
            Column("t", UTCDateTime, nullable=False),
            Column("boredom", Float, nullable=False),
            Column("energy", Float, nullable=False),
            Column("curiosity", Float, nullable=False, default=0.0),
            Column("state", String, nullable=False),
            Index("ix_samples_pet_t", "pet_id", "run_id", "t"),
        )

        self.events = Table(
            "events", md,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("pet_id", String, nullable=False),
            Column("run_id", String, nullable=False),
            Column("t", UTCDateTime, nullable=False),
            Column("type", String, nullable=False),
            Column("payload", JSONType, nullable=False, default=dict),
            Index("ix_events_pet_t", "pet_id", "run_id", "t"),
        )

        self.dreams = Table(
            "dreams", md,
            Column("id", String, primary_key=True),
            Column("pet_id", String, nullable=False),
            Column("night", String, nullable=False),  # local date of the evening, YYYY-MM-DD
            Column("title", Text),
            Column("narrative", Text, nullable=False),
            Column("elements", JSONType, nullable=False),  # [{text, memory_ids, image_hint}]
            Column("mood", String),
            Column("weak", Boolean, nullable=False, default=False),
            Column("score", Float, nullable=False, default=0.0),
            Column("critic", JSONType, nullable=False, default=dict),
            Column("created_at", UTCDateTime, nullable=False),
        )

        self.video_jobs = Table(
            "video_jobs", md,
            Column("id", String, primary_key=True),
            Column("dream_id", String, nullable=False),
            Column("pet_id", String, nullable=False),
            Column("provider", String, nullable=False),
            Column("screenplay", JSONType),
            Column("status", String, nullable=False),  # queued, awaiting_approval, rendering, done, text_only, failed, rejected
            Column("est_cost_usd", Float, nullable=False, default=0.0),
            Column("cost_usd", Float, nullable=False, default=0.0),
            Column("file_uri", Text),
            Column("poster_uri", Text),
            Column("shot_map", JSONType, nullable=False, default=list),
            Column("error", Text),
            Column("thread_id", String),
            Column("created_at", UTCDateTime, nullable=False),
            Column("updated_at", UTCDateTime, nullable=False),
        )

        self.usage = Table(
            "usage", md,
            Column("pet_id", String, nullable=False),
            Column("run_id", String, nullable=False),
            Column("day", String, nullable=False),
            Column("role", String, nullable=False),
            Column("calls", Integer, nullable=False, default=0),
            Column("tokens_in", Integer, nullable=False, default=0),
            Column("tokens_out", Integer, nullable=False, default=0),
            Column("seconds", Float, nullable=False, default=0.0),
            Column("usd", Float, nullable=False, default=0.0),
            Column("est_usd", Float, nullable=False, default=0.0),
            PrimaryKeyConstraint("pet_id", "run_id", "day", "role"),
        )

        self.persona_changes = Table(
            "persona_changes", md,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("pet_id", String, nullable=False),
            Column("t", UTCDateTime, nullable=False),
            Column("field", String, nullable=False),
            Column("old", JSONType),
            Column("new", JSONType),
            Column("reason", Text),
            Column("memory_ids", JSONType, nullable=False, default=list),
        )

        self.messages = Table(
            "messages", md,
            Column("id", String, primary_key=True),
            Column("pet_id", String, nullable=False),
            Column("conversation_id", String, nullable=False),
            Column("t", UTCDateTime, nullable=False),
            Column("sender", String, nullable=False),  # owner | pet
            Column("content", Text, nullable=False),
            Column("recalled", JSONType, nullable=False, default=list),
            Column("proactive", String),  # trigger name when the pet spoke first
            Column("link", JSONType),
            Column("read", Boolean, nullable=False, default=True),
            Index("ix_messages_pet_t", "pet_id", "t"),
        )

        self.sim_runs = Table(
            "sim_runs", md,
            Column("id", String, primary_key=True),
            Column("name", String, nullable=False),
            Column("scenario", JSONType, nullable=False),
            Column("status", String, nullable=False),
            Column("db_path", Text, nullable=False),
            Column("summary", JSONType),
            Column("error", Text),
            Column("created_at", UTCDateTime, nullable=False),
        )
