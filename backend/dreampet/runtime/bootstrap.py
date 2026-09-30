"""Wiring: config + clock + repo + providers + persona -> PetContext / PetRuntime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dreampet.clock import Clock, RealClock
from dreampet.config import AppConfig
from dreampet.drives.params import DrivesFile
from dreampet.graphs.common import make_checkpointer
from dreampet.memory import clustering
from dreampet.memory.repo import MemoryRepo
from dreampet.persona.model import Persona
from dreampet.providers.budget import Meter
from dreampet.providers.roles import Providers
from dreampet.runtime.bus import Bus
from dreampet.runtime.context import PetContext
from dreampet.runtime.media import MediaStore


def tracing_callbacks(cfg: AppConfig) -> list:
    if cfg.tracing.provider == "langfuse":
        try:
            from langfuse.langchain import CallbackHandler

            return [CallbackHandler()]
        except ImportError:
            return []
    if cfg.tracing.provider == "langsmith":
        os.environ.setdefault("LANGSMITH_TRACING", "true")
    return []


def embed_dim(cfg: AppConfig) -> int:
    return int(cfg.role("embeddings").dim or 256)


def open_repo(cfg: AppConfig, url: str | None = None) -> MemoryRepo:
    repo = MemoryRepo(url or cfg.resolved_database_url(), embed_dim(cfg))
    repo.init()
    return repo


@dataclass
class Opened:
    ctx: PetContext
    created: bool


def open_pet(
    cfg: AppConfig,
    *,
    repo: MemoryRepo | None = None,
    pet_id: str | None = None,
    persona: Persona | None = None,
    clock: Clock | None = None,
    run_id: str = "live",
    seed: int | None = None,
    mode_overrides: dict[str, str] | None = None,
    checkpointer_kind: str | None = None,
    checkpoint_path: Path | None = None,
    media_root: Path | None = None,
    bus: Bus | None = None,
    drives_file: Path | None = None,
) -> Opened:
    """Open (or create) a pet and build its context."""
    clock = clock or RealClock()
    repo = repo or open_repo(cfg)
    pet_id = pet_id or cfg.pet_id
    row = repo.get_pet(pet_id)
    created = False
    if row is None:
        persona = persona or Persona()
        repo.create_pet(pet_id, persona.name, persona.language, persona.model_dump(mode="json"), {}, clock.now())
        row = repo.get_pet(pet_id)
        created = True
    persona = Persona.model_validate(row["persona"])
    meter = Meter(repo, pet_id, run_id, clock, persona.timezone, cfg.budgets)
    providers = Providers(cfg, meter, seed=seed or 0, mode_overrides=mode_overrides, palette=persona.palette,
                          callbacks=tracing_callbacks(cfg))
    ctx = PetContext(
        cfg=cfg, pet_id=pet_id, run_id=run_id, clock=clock, repo=repo, providers=providers, persona=persona,
        drives_file=DrivesFile(drives_file if drives_file is not None else cfg.path(cfg.drives_file)),
        bus=bus or Bus(), media=MediaStore(cfg, media_root), seed=seed,
        checkpointer=make_checkpointer(cfg, checkpointer_kind, checkpoint_path),
    )
    meter.emit = ctx.emit
    ctx.overrides = dict((row.get("drives_config") or {}).get("overrides", {}))
    ctx.refresh_params()
    ctx.restore_state()
    if created:
        clustering.seed_clusters(ctx)
        ctx.emit("pet_created", {"name": persona.name, "language": persona.language})
        ctx.record_sample()
    return Opened(ctx=ctx, created=created)


def reembed_stale(ctx: PetContext, batch: int = 32) -> int:
    """Background re-embed after an embedding-model switch. Retrieval only uses rows that match
    the active model, so this can run while the pet lives on."""
    active = ctx.providers.embeddings.model_name
    done = 0
    while True:
        stale = [m for m in ctx.repo.list_memories(ctx.pet_id, limit=2000, include_archived=True)
                 if m.embed_model not in (active, None)]
        if not stale:
            break
        chunk = stale[:batch]
        vecs = ctx.providers.embeddings.embed([m.content for m in chunk])
        for m, v in zip(chunk, vecs, strict=True):
            ctx.repo.update_memory(m.id, embedding=v, embed_model=active)
        done += len(chunk)
    if done:
        ctx.emit("reembedded", {"count": done, "model": active})
    return done


def stale_embedding_count(ctx: PetContext) -> dict[str, Any]:
    active = ctx.providers.embeddings.model_name
    counts = ctx.repo.embed_models(ctx.pet_id)
    return {"active": active, "stale": sum(n for m, n in counts.items() if m and m != active), "by_model": counts}
