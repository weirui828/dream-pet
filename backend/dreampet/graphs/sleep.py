"""sleep: sample_day → consolidate (reflect, merge, decay, prune) → recluster → dream_weave →
dream_critic → persist_dream → persona_drift.  Exit: dream saved (video job enqueued by the
scheduler)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from dreampet import prompts
from dreampet.dreams import weave
from dreampet.drives.base import Event
from dreampet.graphs.common import GraphState
from dreampet.memory import clustering, consolidation
from dreampet.memory.repo import Memory
from dreampet.persona.drift import apply_drift
from dreampet.providers.base import BudgetExceeded, ChatRequest, ProviderError
from dreampet.schemas import Critique, DreamDraft, DriftProposal

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext

MAX_RETRIES = 2


class SleepState(GraphState):
    night: str = ""
    since: str = ""
    day_ids: list[str] = Field(default_factory=list)
    insight_ids: list[str] = Field(default_factory=list)
    merged: int = 0
    archived: int = 0
    recluster: dict[str, Any] = Field(default_factory=dict)
    fragments: list[dict[str, Any]] = Field(default_factory=list)
    draft: dict[str, Any] | None = None
    critic: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    weak: bool = False
    dream_id: str | None = None
    dreamless: bool = False
    drift: list[dict[str, Any]] = Field(default_factory=list)


def build_sleep_graph(ctx: PetContext, checkpointer=None):
    repo = ctx.repo
    P = ctx.providers

    def sample_day(s: SleepState) -> dict:
        since = datetime.fromisoformat(s.since)
        day = repo.list_memories(ctx.pet_id, kinds=["episodic", "chat"], since=since, limit=500, newest_first=False)
        ids = [m.id for m in day]
        ctx.activity("drifting off to sleep")
        ctx.emit("sleep_start", {"night": s.night, "day_memories": len(ids)})
        return {"day_ids": ids, **ctx.base_payload()}

    def consolidate(s: SleepState) -> dict:
        day = repo.get_memories(s.day_ids)
        try:
            insight_ids = consolidation.reflect(ctx, day)
        except (ProviderError, BudgetExceeded) as exc:
            ctx.emit("provider_error", {"role": "dreamer_llm", "node": "consolidate", "error": str(exc)[:300]})
            insight_ids = []
        merged = consolidation.merge_duplicates(ctx, day + repo.get_memories(insight_ids))
        archived = consolidation.decay(ctx, datetime.fromisoformat(s.since))
        ctx.spend("consolidate")
        ctx.emit("consolidated", {"insights": len(insight_ids), "merged": len(merged), "archived": len(archived)})
        return {"insight_ids": insight_ids, "merged": len(merged), "archived": len(archived)}

    def recluster(s: SleepState) -> dict:
        stats = clustering.recluster(ctx)
        ctx.emit("reclustered", stats)
        return {"recluster": stats}

    def dream_weave(s: SleepState) -> dict:
        if not s.fragments:
            pool = [m for m in repo.get_memories(s.day_ids + s.insight_ids) if not m.archived]
            if len(pool) < 3:  # a quiet day: dream about the last week instead
                week = repo.list_memories(ctx.pet_id, kinds=list(weave.DREAMABLE),
                                          since=ctx.now() - timedelta(days=7), limit=200)
                pool += [m for m in week if m.id not in {p.id for p in pool}]
            seeds = weave.select_seeds(ctx, pool)
            if not seeds:
                ctx.emit("dreamless", {"night": s.night, "why": "no memories yet"})
                return {"dreamless": True}
            walked = weave.associative_walk(ctx, seeds, ctx.compiled.walk_hops)
            fragments = [weave.fragment(m) for m in walked]
        else:
            fragments = s.fragments
        ctx.activity("dreaming")
        salience = sum(f.get("salience", 0) for f in fragments) / max(1, len(fragments))
        req = ChatRequest(
            task="dream_weave", system=prompts.render(prompts.WEAVE_SYSTEM, ctx),
            user=prompts.weave_user(fragments, None) + (
                f"\n\nPrevious attempt problems: {s.critic.get('issues')}" if s.critic.get("issues") else ""),
            temperature=ctx.compiled.dreamer_temperature, max_tokens=1500,
            context={"fragments": fragments, "name": ctx.persona.name, "attempt": s.attempts,
                     "salience": salience},
        )
        try:
            draft: DreamDraft = P.dreamer.structured(req, DreamDraft)
        except (ProviderError, BudgetExceeded) as exc:
            ctx.emit("provider_error", {"role": "dreamer_llm", "node": "dream_weave", "error": str(exc)[:300]})
            return {"fragments": fragments, "draft": None, "attempts": s.attempts + 1}
        ctx.spend("dream")
        return {"fragments": fragments, "draft": draft.model_dump(), "attempts": s.attempts + 1}

    def dream_critic(s: SleepState) -> dict:
        if s.draft is None:
            return {"critic": {"ok": False, "issues": ["weaving failed"], "score": 0.0}}
        valid = {f["id"] for f in s.fragments}
        sources = _source_texts(ctx, s.fragments)
        result = weave.critique(s.draft, valid, sources)
        score = 0.0
        try:
            c: Critique = P.dreamer.structured(ChatRequest(
                task="dream_critic", system=prompts.CRITIC_SYSTEM, user=prompts.critic_user(s.draft), max_tokens=200,
                context={"n_elements": len(s.draft.get("elements", [])), "grounded_ratio": result["grounded_ratio"]},
            ), Critique)
            score = c.score
            result["notes"] = c.notes
        except (ProviderError, BudgetExceeded):
            score = 0.5 * result["grounded_ratio"]
        result["score"] = round(score if result["ok"] else score * 0.5, 4)
        ctx.emit("dream_critic", {"attempt": s.attempts, **result})
        return {"critic": result}

    def after_critic(s: SleepState) -> str:
        if s.critic.get("ok"):
            return "persist_dream"
        if s.attempts <= MAX_RETRIES:
            return "dream_weave"
        return "persist_dream"

    def persist_dream(s: SleepState) -> dict:
        if s.draft is None:
            ctx.emit("dreamless", {"night": s.night, "why": "weaving failed"})
            return {"dreamless": True}
        weak = not s.critic.get("ok", False)
        valid = {f["id"] for f in s.fragments}
        draft = weave.repair(s.draft, valid) if weak else s.draft
        if not draft["elements"]:
            ctx.emit("dreamless", {"night": s.night, "why": "no grounded scenes"})
            return {"dreamless": True, "weak": True}
        did = ctx.new_id("dream")
        now = ctx.now()
        repo.add_dream(id=did, pet_id=ctx.pet_id, night=s.night, title=draft["title"], narrative=draft["narrative"],
                       elements=draft["elements"], mood=draft["mood"], weak=weak,
                       score=float(s.critic.get("score", 0.0)), critic=s.critic, created_at=now)
        vec = P.embeddings.embed_one(draft["narrative"])
        mid = ctx.new_id("m")
        repo.add_memory(Memory(id=mid, pet_id=ctx.pet_id, kind="dream", title=draft["title"], content=draft["narrative"],
                               created_at=now, embedding=vec, embed_model=P.embeddings.model_name, importance=0.6,
                               salience=0.7, meta={"dream_id": did, "mood": draft["mood"]}))
        cited = sorted({i for el in draft["elements"] for i in el["memory_ids"]})
        for c in cited:
            repo.add_link(mid, c, "dream_used")
        ctx.step_drives([Event(type="dream", t=now, payload={"energy_cost": ctx.energy_cost("dream")})])
        ctx.emit("dream_saved", {"dream_id": did, "title": draft["title"], "mood": draft["mood"], "weak": weak,
                                 "elements": len(draft["elements"]), "memories": len(cited)})
        return {"dream_id": did, "weak": weak, "draft": draft}

    def persona_drift(s: SleepState) -> dict:
        day = [m for m in repo.get_memories(s.day_ids) if m.cluster_id]
        clusters = {c.id: c for c in repo.list_clusters(ctx.pet_id)}
        by_cluster: dict[str, list[str]] = {}
        for m in day:
            by_cluster.setdefault(m.cluster_id, []).append(m.id)
        top = sorted(by_cluster.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:3]
        top_clusters = [{"label": clusters[cid].label, "memory_ids": ids, "count": len(ids)}
                        for cid, ids in top if cid in clusters and len(ids) >= 2]
        if not top_clusters:
            return {"drift": []}
        p = ctx.persona
        new_clusters = sum(1 for c in clusters.values() if c.created_at >= datetime.fromisoformat(s.since))
        interests = [i.model_dump() for i in p.interests]
        try:
            prop: DriftProposal = P.dreamer.structured(ChatRequest(
                task="persona_drift", system=prompts.render(prompts.DRIFT_SYSTEM, ctx),
                user=prompts.drift_user(interests, p.traits.model_dump(), top_clusters, p.locks),
                context={"interests": interests, "traits": p.traits.model_dump(), "top_clusters": top_clusters,
                         "new_clusters": new_clusters},
            ), DriftProposal)
        except (ProviderError, BudgetExceeded) as exc:
            ctx.emit("provider_error", {"role": "dreamer_llm", "node": "persona_drift", "error": str(exc)[:300]})
            return {"drift": []}
        valid = {i for c in top_clusters for i in c["memory_ids"]} | set(s.day_ids)
        before = {i.topic.lower() for i in p.interests}
        applied = apply_drift(ctx, prop, valid)
        for ch in applied:
            ctx.emit("persona_drift", ch)
            topic = ch["field"].partition(".")[2]
            if ch["field"].startswith("interests.") and topic.lower() not in before:
                ctx.hook("drift", {"topic": topic, "field": ch["field"]})
        return {"drift": applied}

    g = StateGraph(SleepState)
    for name, fn in [("sample_day", sample_day), ("consolidate", consolidate), ("recluster", recluster),
                     ("dream_weave", dream_weave), ("dream_critic", dream_critic),
                     ("persist_dream", persist_dream), ("persona_drift", persona_drift)]:
        g.add_node(name, fn)
    g.add_edge(START, "sample_day")
    g.add_edge("sample_day", "consolidate")
    g.add_edge("consolidate", "recluster")
    g.add_edge("recluster", "dream_weave")
    g.add_conditional_edges("dream_weave", lambda s: "persona_drift" if s.dreamless else "dream_critic",
                            ["dream_critic", "persona_drift"])
    g.add_conditional_edges("dream_critic", after_critic, ["dream_weave", "persist_dream"])
    g.add_edge("persist_dream", "persona_drift")
    g.add_edge("persona_drift", END)
    return g.compile(checkpointer=checkpointer)


def _source_texts(ctx: PetContext, fragments: list[dict]) -> dict[str, str]:
    """What a dream must not copy from: memory text plus the fetched page snapshot when kept."""
    out = {}
    for m in ctx.repo.get_memories([f["id"] for f in fragments]):
        text = m.content + " " + " ".join(m.meta.get("facts", []))
        snap = m.meta.get("snapshot")
        if snap:
            p = ctx.media.resolve(snap)
            if p:
                text += " " + p.read_text(errors="ignore")
        out[m.id] = text
    return out
