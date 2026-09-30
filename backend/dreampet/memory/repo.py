"""MemoryRepo: the single storage interface. SQLAlchemy Core keeps the SQL shared between
SQLite (+ sqlite-vec) and Postgres (+ pgvector); only vector distance and index setup differ."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import (
    Engine,
    Float,
    and_,
    create_engine,
    delete,
    event,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.engine import Row

from dreampet.memory.schema import Embedding, Schema


@dataclass
class Memory:
    id: str
    pet_id: str
    kind: str
    content: str
    created_at: datetime
    title: str | None = None
    embedding: np.ndarray | None = None
    embed_model: str | None = None
    source_url: str | None = None
    importance: float = 0.5
    surprise: float = 0.0
    salience: float = 0.0
    strength: float = 1.0
    access_count: int = 0
    last_accessed_at: datetime | None = None
    cluster_id: str | None = None
    archived: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "embedding"}
        for k in ("created_at", "last_accessed_at"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d


@dataclass
class Cluster:
    id: str
    pet_id: str
    label: str
    centroid: np.ndarray | None
    error_history: list[float]
    lp: float
    visits: int
    size: int
    seed: bool
    created_at: datetime

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "error_history": self.error_history, "lp": self.lp,
            "visits": self.visits, "size": self.size, "seed": self.seed,
            "created_at": self.created_at.isoformat(),
        }


def _row_dict(row: Row) -> dict[str, Any]:
    return dict(row._mapping)


def _iso(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()}


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        path = url.split(":///", 1)[1] if ":///" in url else None
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _):
            import sqlite_vec

            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
            dbapi_conn.enable_load_extension(False)
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        return engine
    if url.startswith("postgres"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1).replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    return create_engine(url, pool_pre_ping=True)


class MemoryRepo:
    def __init__(self, url: str, dim: int):
        self.url = url
        self.engine = make_engine(url)
        self.dialect = self.engine.dialect.name
        self.s = Schema(dim)
        self.dim = dim

    # ---- lifecycle -------------------------------------------------------------------------

    def init(self) -> None:
        if self.dialect == "postgresql":
            with self.engine.begin() as c:
                c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        self.s.metadata.create_all(self.engine)
        if self.dialect == "postgresql":
            with self.engine.begin() as c:
                c.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_memories_embedding_hnsw ON memories "
                    "USING hnsw (embedding vector_cosine_ops)"
                ))

    def close(self) -> None:
        self.engine.dispose()

    # ---- vector helpers --------------------------------------------------------------------

    def _vec_literal(self, vec: np.ndarray):
        return literal(np.asarray(vec, dtype=np.float32), type_=Embedding(self.dim))

    def _cos_dist(self, col, vec: np.ndarray):
        if self.dialect == "postgresql":
            return col.op("<=>", return_type=Float())(self._vec_literal(vec))
        return func.vec_distance_cosine(col, self._vec_literal(vec))

    # ---- pets ------------------------------------------------------------------------------

    def create_pet(self, pet_id: str, name: str, language: str, persona: dict, drives_config: dict,
                   created_at: datetime, owner_id: str = "owner") -> None:
        with self.engine.begin() as c:
            c.execute(insert(self.s.pets).values(
                id=pet_id, owner_id=owner_id, name=name, language=language, persona=persona,
                drives_config=drives_config, created_at=created_at,
            ))

    def get_pet(self, pet_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(select(self.s.pets).where(self.s.pets.c.id == pet_id)).first()
        return _row_dict(row) if row else None

    def list_pets(self) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            return [_row_dict(r) for r in c.execute(select(self.s.pets))]

    def update_pet(self, pet_id: str, **values) -> None:
        with self.engine.begin() as c:
            c.execute(update(self.s.pets).where(self.s.pets.c.id == pet_id).values(**values))

    # ---- memories --------------------------------------------------------------------------

    def _mem(self, row: Row) -> Memory:
        return Memory(**_row_dict(row))

    def add_memory(self, m: Memory) -> Memory:
        with self.engine.begin() as c:
            c.execute(insert(self.s.memories).values(**m.__dict__))
        return m

    def get_memory(self, mid: str) -> Memory | None:
        with self.engine.connect() as c:
            row = c.execute(select(self.s.memories).where(self.s.memories.c.id == mid)).first()
        return self._mem(row) if row else None

    def get_memories(self, ids: Sequence[str]) -> list[Memory]:
        if not ids:
            return []
        with self.engine.connect() as c:
            rows = c.execute(select(self.s.memories).where(self.s.memories.c.id.in_(list(ids)))).all()
        by_id = {r.id: self._mem(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def update_memory(self, mid: str, **values) -> None:
        with self.engine.begin() as c:
            c.execute(update(self.s.memories).where(self.s.memories.c.id == mid).values(**values))

    def delete_memory(self, mid: str) -> None:
        m = self.s.memories
        lk = self.s.memory_links
        with self.engine.begin() as c:
            c.execute(delete(lk).where(or_(lk.c.src_id == mid, lk.c.dst_id == mid)))
            c.execute(delete(m).where(m.c.id == mid))

    def list_memories(
        self, pet_id: str, kinds: Sequence[str] | None = None, q: str | None = None,
        since: datetime | None = None, until: datetime | None = None, cluster_id: str | None = None,
        include_archived: bool = False, limit: int = 100, offset: int = 0, newest_first: bool = True,
    ) -> list[Memory]:
        m = self.s.memories
        conds = [m.c.pet_id == pet_id]
        if kinds:
            conds.append(m.c.kind.in_(list(kinds)))
        if q:
            like = f"%{q}%"
            conds.append(or_(m.c.content.ilike(like), m.c.title.ilike(like)))
        if since:
            conds.append(m.c.created_at >= since)
        if until:
            conds.append(m.c.created_at < until)
        if cluster_id:
            conds.append(m.c.cluster_id == cluster_id)
        if not include_archived:
            conds.append(m.c.archived.is_(False))
        order = (m.c.created_at.desc(), m.c.id.desc()) if newest_first else (m.c.created_at, m.c.id)
        stmt = select(m).where(and_(*conds)).order_by(*order).limit(limit).offset(offset)
        with self.engine.connect() as c:
            return [self._mem(r) for r in c.execute(stmt)]

    def count_memories(self, pet_id: str, kinds: Sequence[str] | None = None, include_archived: bool = False) -> int:
        m = self.s.memories
        conds = [m.c.pet_id == pet_id]
        if kinds:
            conds.append(m.c.kind.in_(list(kinds)))
        if not include_archived:
            conds.append(m.c.archived.is_(False))
        with self.engine.connect() as c:
            return int(c.execute(select(func.count()).select_from(m).where(and_(*conds))).scalar_one())

    def knn(
        self, pet_id: str, vec: np.ndarray, k: int, since: datetime | None = None,
        kinds: Sequence[str] | None = None, embed_model: str | None = None, exclude_ids: Iterable[str] = (),
        include_archived: bool = False,
    ) -> list[tuple[Memory, float]]:
        m = self.s.memories
        dist = self._cos_dist(m.c.embedding, vec).label("dist")
        conds = [m.c.pet_id == pet_id, m.c.embedding.is_not(None)]
        if since:
            conds.append(m.c.created_at >= since)
        if kinds:
            conds.append(m.c.kind.in_(list(kinds)))
        if embed_model:
            conds.append(m.c.embed_model == embed_model)
        ex = list(exclude_ids)
        if ex:
            conds.append(m.c.id.not_in(ex))
        if not include_archived:
            conds.append(m.c.archived.is_(False))
        stmt = select(m, dist).where(and_(*conds)).order_by(dist, m.c.id).limit(k)
        with self.engine.connect() as c:
            out = []
            for r in c.execute(stmt):
                d = _row_dict(r)
                dd = float(d.pop("dist"))
                out.append((Memory(**d), dd))
            return out

    def touch(self, ids: Sequence[str], t: datetime) -> None:
        if not ids:
            return
        m = self.s.memories
        with self.engine.begin() as c:
            c.execute(update(m).where(m.c.id.in_(list(ids))).values(
                access_count=m.c.access_count + 1, last_accessed_at=t,
            ))

    def url_seen(self, pet_id: str, url: str) -> bool:
        m = self.s.memories
        with self.engine.connect() as c:
            return c.execute(
                select(m.c.id).where(m.c.pet_id == pet_id, m.c.source_url == url).limit(1)
            ).first() is not None

    def embed_models(self, pet_id: str) -> dict[str, int]:
        m = self.s.memories
        with self.engine.connect() as c:
            rows = c.execute(select(m.c.embed_model, func.count()).where(m.c.pet_id == pet_id)
                             .group_by(m.c.embed_model)).all()
        return {r[0] or "": int(r[1]) for r in rows}

    # ---- links -----------------------------------------------------------------------------

    def _upsert(self, table, values: dict, keys: list[str], increments: dict):
        """INSERT ... ON CONFLICT DO UPDATE (atomic on both SQLite and Postgres)."""
        if self.dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        else:
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        stmt = dialect_insert(table).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=keys, set_={
            k: table.c[k] + stmt.excluded[k] for k in increments})
        return stmt

    def add_link(self, src: str, dst: str, kind: str, weight: float = 1.0) -> None:
        lk = self.s.memory_links
        with self.engine.begin() as c:
            c.execute(self._upsert(lk, {"src_id": src, "dst_id": dst, "kind": kind, "weight": weight},
                                   ["src_id", "dst_id", "kind"], {"weight": weight}))

    def links(self, ids: Sequence[str], kinds: Sequence[str] | None = None) -> list[dict[str, Any]]:
        if not ids:
            return []
        lk = self.s.memory_links
        conds = [or_(lk.c.src_id.in_(list(ids)), lk.c.dst_id.in_(list(ids)))]
        if kinds:
            conds.append(lk.c.kind.in_(list(kinds)))
        with self.engine.connect() as c:
            return [_row_dict(r) for r in c.execute(select(lk).where(and_(*conds)).order_by(lk.c.src_id, lk.c.dst_id))]

    def has_link_kind(self, mid: str, kind: str) -> bool:
        lk = self.s.memory_links
        with self.engine.connect() as c:
            return c.execute(select(lk.c.src_id).where(
                or_(lk.c.src_id == mid, lk.c.dst_id == mid), lk.c.kind == kind).limit(1)).first() is not None

    def relink(self, old: str, new: str) -> None:
        """Point every link at `old` to `new` (used when merging near-duplicates)."""
        lk = self.s.memory_links
        with self.engine.begin() as c:
            for col_a in (lk.c.src_id, lk.c.dst_id):
                rows = c.execute(select(lk).where(col_a == old)).all()
                for r in rows:
                    d = _row_dict(r)
                    c.execute(delete(lk).where(lk.c.src_id == d["src_id"], lk.c.dst_id == d["dst_id"], lk.c.kind == d["kind"]))
                    d[col_a.name] = new
                    if d["src_id"] == d["dst_id"]:
                        continue
                    exists = c.execute(select(lk.c.weight).where(
                        lk.c.src_id == d["src_id"], lk.c.dst_id == d["dst_id"], lk.c.kind == d["kind"])).first()
                    if not exists:
                        c.execute(insert(lk).values(**d))

    # ---- clusters --------------------------------------------------------------------------

    def _cluster(self, row: Row) -> Cluster:
        return Cluster(**_row_dict(row))

    def list_clusters(self, pet_id: str) -> list[Cluster]:
        t = self.s.topic_clusters
        with self.engine.connect() as c:
            return [self._cluster(r) for r in c.execute(select(t).where(t.c.pet_id == pet_id).order_by(t.c.created_at, t.c.id))]

    def get_cluster(self, cid: str) -> Cluster | None:
        t = self.s.topic_clusters
        with self.engine.connect() as c:
            row = c.execute(select(t).where(t.c.id == cid)).first()
        return self._cluster(row) if row else None

    def upsert_cluster(self, cl: Cluster) -> None:
        t = self.s.topic_clusters
        with self.engine.begin() as c:
            exists = c.execute(select(t.c.id).where(t.c.id == cl.id)).first()
            vals = dict(cl.__dict__)
            if exists:
                c.execute(update(t).where(t.c.id == cl.id).values(**vals))
            else:
                c.execute(insert(t).values(**vals))

    def delete_cluster(self, cid: str) -> None:
        t = self.s.topic_clusters
        with self.engine.begin() as c:
            c.execute(delete(t).where(t.c.id == cid))

    def cluster_sizes(self, pet_id: str) -> dict[str, int]:
        m = self.s.memories
        with self.engine.connect() as c:
            rows = c.execute(select(m.c.cluster_id, func.count()).where(
                m.c.pet_id == pet_id, m.c.archived.is_(False), m.c.cluster_id.is_not(None)).group_by(m.c.cluster_id)).all()
        return {r[0]: int(r[1]) for r in rows}

    # ---- drive samples & events ------------------------------------------------------------

    def add_sample(self, pet_id: str, run_id: str, t: datetime, boredom: float, energy: float,
                   curiosity: float, state: str) -> None:
        with self.engine.begin() as c:
            c.execute(insert(self.s.drive_samples).values(
                pet_id=pet_id, run_id=run_id, t=t, boredom=boredom, energy=energy, curiosity=curiosity, state=state))

    def samples(self, pet_id: str, run_id: str | None = None, since: datetime | None = None,
                until: datetime | None = None, limit: int = 100000) -> list[dict[str, Any]]:
        d = self.s.drive_samples
        conds = [d.c.pet_id == pet_id]
        if run_id:
            conds.append(d.c.run_id == run_id)
        if since:
            conds.append(d.c.t >= since)
        if until:
            conds.append(d.c.t <= until)
        with self.engine.connect() as c:
            rows = c.execute(select(d).where(and_(*conds)).order_by(d.c.t, d.c.id).limit(limit)).all()
        return [_iso(_row_dict(r)) for r in rows]

    def last_sample(self, pet_id: str, run_id: str) -> dict[str, Any] | None:
        d = self.s.drive_samples
        with self.engine.connect() as c:
            row = c.execute(select(d).where(d.c.pet_id == pet_id, d.c.run_id == run_id)
                            .order_by(d.c.t.desc(), d.c.id.desc()).limit(1)).first()
        return _row_dict(row) if row else None

    def add_event(self, pet_id: str, run_id: str, t: datetime, type_: str, payload: dict | None = None) -> int:
        with self.engine.begin() as c:
            res = c.execute(insert(self.s.events).values(pet_id=pet_id, run_id=run_id, t=t, type=type_, payload=payload or {}))
            return int(res.inserted_primary_key[0])

    def events(self, pet_id: str, run_id: str | None = None, types: Sequence[str] | None = None,
               since: datetime | None = None, until: datetime | None = None, limit: int = 1000,
               newest_first: bool = False) -> list[dict[str, Any]]:
        e = self.s.events
        conds = [e.c.pet_id == pet_id]
        if run_id:
            conds.append(e.c.run_id == run_id)
        if types:
            conds.append(e.c.type.in_(list(types)))
        if since:
            conds.append(e.c.t >= since)
        if until:
            conds.append(e.c.t <= until)
        order = (e.c.t.desc(), e.c.id.desc()) if newest_first else (e.c.t, e.c.id)
        with self.engine.connect() as c:
            rows = c.execute(select(e).where(and_(*conds)).order_by(*order).limit(limit)).all()
        return [_row_dict(r) for r in rows]

    def last_event(self, pet_id: str, run_id: str, types: Sequence[str]) -> dict[str, Any] | None:
        ev = self.events(pet_id, run_id, types=types, limit=1, newest_first=True)
        return ev[0] if ev else None

    def count_events(self, pet_id: str, run_id: str, type_: str, since: datetime | None = None) -> int:
        e = self.s.events
        conds = [e.c.pet_id == pet_id, e.c.run_id == run_id, e.c.type == type_]
        if since:
            conds.append(e.c.t >= since)
        with self.engine.connect() as c:
            return int(c.execute(select(func.count()).select_from(e).where(and_(*conds))).scalar_one())

    # ---- dreams ----------------------------------------------------------------------------

    def add_dream(self, **values) -> None:
        with self.engine.begin() as c:
            c.execute(insert(self.s.dreams).values(**values))

    def get_dream(self, did: str) -> dict[str, Any] | None:
        d = self.s.dreams
        with self.engine.connect() as c:
            row = c.execute(select(d).where(d.c.id == did)).first()
        return _row_dict(row) if row else None

    def list_dreams(self, pet_id: str, night: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        d = self.s.dreams
        conds = [d.c.pet_id == pet_id]
        if night:
            conds.append(d.c.night == night)
        with self.engine.connect() as c:
            rows = c.execute(select(d).where(and_(*conds)).order_by(d.c.created_at.desc(), d.c.id.desc()).limit(limit)).all()
        return [_row_dict(r) for r in rows]

    # ---- video jobs ------------------------------------------------------------------------

    def add_video_job(self, **values) -> None:
        with self.engine.begin() as c:
            c.execute(insert(self.s.video_jobs).values(**values))

    def get_video_job(self, jid: str) -> dict[str, Any] | None:
        v = self.s.video_jobs
        with self.engine.connect() as c:
            row = c.execute(select(v).where(v.c.id == jid)).first()
        return _row_dict(row) if row else None

    def video_job_for_dream(self, dream_id: str) -> dict[str, Any] | None:
        v = self.s.video_jobs
        with self.engine.connect() as c:
            row = c.execute(select(v).where(v.c.dream_id == dream_id).order_by(v.c.created_at.desc()).limit(1)).first()
        return _row_dict(row) if row else None

    def update_video_job(self, jid: str, **values) -> None:
        v = self.s.video_jobs
        with self.engine.begin() as c:
            c.execute(update(v).where(v.c.id == jid).values(**values))

    def video_jobs(self, pet_id: str, statuses: Sequence[str] | None = None) -> list[dict[str, Any]]:
        v = self.s.video_jobs
        conds = [v.c.pet_id == pet_id]
        if statuses:
            conds.append(v.c.status.in_(list(statuses)))
        with self.engine.connect() as c:
            return [_row_dict(r) for r in c.execute(select(v).where(and_(*conds)).order_by(v.c.created_at, v.c.id))]

    # ---- usage -----------------------------------------------------------------------------

    def add_usage(self, pet_id: str, run_id: str, day: str, role: str, *, calls: int = 1, tokens_in: int = 0,
                  tokens_out: int = 0, seconds: float = 0.0, usd: float = 0.0, est_usd: float = 0.0) -> None:
        vals = {"calls": calls, "tokens_in": tokens_in, "tokens_out": tokens_out, "seconds": seconds, "usd": usd,
                "est_usd": est_usd}
        with self.engine.begin() as c:
            c.execute(self._upsert(self.s.usage, {"pet_id": pet_id, "run_id": run_id, "day": day, "role": role, **vals},
                                   ["pet_id", "run_id", "day", "role"], vals))

    def usage(self, pet_id: str, run_id: str | None = None, day: str | None = None) -> list[dict[str, Any]]:
        u = self.s.usage
        conds = [u.c.pet_id == pet_id]
        if run_id:
            conds.append(u.c.run_id == run_id)
        if day:
            conds.append(u.c.day == day)
        with self.engine.connect() as c:
            return [_row_dict(r) for r in c.execute(select(u).where(and_(*conds)).order_by(u.c.day, u.c.role))]

    def spend(self, pet_id: str, run_id: str, day: str, role: str | None = None) -> float:
        u = self.s.usage
        conds = [u.c.pet_id == pet_id, u.c.run_id == run_id, u.c.day == day]
        if role:
            conds.append(u.c.role == role)
        with self.engine.connect() as c:
            return float(c.execute(select(func.coalesce(func.sum(u.c.usd), 0.0)).where(and_(*conds))).scalar_one())

    # ---- persona changes -------------------------------------------------------------------

    def add_persona_change(self, pet_id: str, t: datetime, field: str, old, new, reason: str, memory_ids: list[str]) -> None:
        with self.engine.begin() as c:
            c.execute(insert(self.s.persona_changes).values(
                pet_id=pet_id, t=t, field=field, old=old, new=new, reason=reason, memory_ids=memory_ids))

    def persona_changes(self, pet_id: str, limit: int = 200) -> list[dict[str, Any]]:
        p = self.s.persona_changes
        with self.engine.connect() as c:
            return [_row_dict(r) for r in c.execute(select(p).where(p.c.pet_id == pet_id).order_by(p.c.t.desc(), p.c.id.desc()).limit(limit))]

    # ---- messages --------------------------------------------------------------------------

    def add_message(self, **values) -> None:
        with self.engine.begin() as c:
            c.execute(insert(self.s.messages).values(**values))

    def messages(self, pet_id: str, conversation_id: str | None = None, limit: int = 100,
                 since: datetime | None = None) -> list[dict[str, Any]]:
        m = self.s.messages
        conds = [m.c.pet_id == pet_id]
        if conversation_id:
            conds.append(m.c.conversation_id == conversation_id)
        if since:
            conds.append(m.c.t >= since)
        with self.engine.connect() as c:
            rows = c.execute(select(m).where(and_(*conds)).order_by(m.c.t.desc(), m.c.id.desc()).limit(limit)).all()
        return [_row_dict(r) for r in reversed(rows)]

    def last_owner_message(self, pet_id: str) -> dict[str, Any] | None:
        m = self.s.messages
        with self.engine.connect() as c:
            row = c.execute(select(m).where(m.c.pet_id == pet_id, m.c.sender == "owner")
                            .order_by(m.c.t.desc()).limit(1)).first()
        return _row_dict(row) if row else None

    def unread_count(self, pet_id: str) -> int:
        m = self.s.messages
        with self.engine.connect() as c:
            return int(c.execute(select(func.count()).select_from(m).where(
                m.c.pet_id == pet_id, m.c.read.is_(False))).scalar_one())

    def mark_read(self, pet_id: str) -> None:
        m = self.s.messages
        with self.engine.begin() as c:
            c.execute(update(m).where(m.c.pet_id == pet_id, m.c.read.is_(False)).values(read=True))

    def count_proactive(self, pet_id: str, since: datetime) -> int:
        m = self.s.messages
        with self.engine.connect() as c:
            return int(c.execute(select(func.count()).select_from(m).where(
                m.c.pet_id == pet_id, m.c.proactive.is_not(None), m.c.t >= since)).scalar_one())

    # ---- sim runs --------------------------------------------------------------------------

    def add_sim_run(self, **values) -> None:
        with self.engine.begin() as c:
            c.execute(insert(self.s.sim_runs).values(**values))

    def update_sim_run(self, rid: str, **values) -> None:
        r = self.s.sim_runs
        with self.engine.begin() as c:
            c.execute(update(r).where(r.c.id == rid).values(**values))

    def get_sim_run(self, rid: str) -> dict[str, Any] | None:
        r = self.s.sim_runs
        with self.engine.connect() as c:
            row = c.execute(select(r).where(r.c.id == rid)).first()
        return _row_dict(row) if row else None

    def list_sim_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        r = self.s.sim_runs
        with self.engine.connect() as c:
            return [_row_dict(x) for x in c.execute(select(r).order_by(r.c.created_at.desc()).limit(limit))]

    # ---- wipe ------------------------------------------------------------------------------

    def wipe_pet(self, pet_id: str) -> None:
        s = self.s
        with self.engine.begin() as c:
            ids = [r[0] for r in c.execute(select(s.memories.c.id).where(s.memories.c.pet_id == pet_id))]
            for chunk in range(0, len(ids), 500):
                part = ids[chunk:chunk + 500]
                c.execute(delete(s.memory_links).where(or_(s.memory_links.c.src_id.in_(part), s.memory_links.c.dst_id.in_(part))))
            for t in (s.memories, s.topic_clusters, s.drive_samples, s.events, s.dreams, s.video_jobs, s.usage,
                      s.persona_changes, s.messages, s.pets):
                col = t.c.id if t is s.pets else t.c.pet_id
                c.execute(delete(t).where(col == pet_id))
