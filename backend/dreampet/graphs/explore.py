"""explore: pick_topic → plan_queries → search → select_sources → fetch_and_sanitize →
predict_then_read → memorize → update_drives → loop?

Runs while B > θ_high and there is energy; ends when B < θ_low, energy is low, the session cap
is hit or it's bedtime. Only `search` and `fetch` touch the network, and fetched text reaches
the LLM only as quoted data."""

from __future__ import annotations

import operator
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from dreampet import prompts
from dreampet.drives.base import Event
from dreampet.drives.signals import habituation, knn_novelty, learnability, softmax_sample, softmax_scale
from dreampet.graphs.common import GraphState
from dreampet.memory import clustering
from dreampet.memory.repo import Memory
from dreampet.providers.base import BudgetExceeded, ChatRequest, ProviderError
from dreampet.schemas import Prediction, QueryPlan, ReadNotes
from dreampet.text import key_phrases, moderation_flags, sanitize_fetched, truncate_words

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext

NOVELTY_KINDS = ("episodic", "semantic", "chat")


class ExploreState(GraphState):
    session_id: str = ""
    deadline: str | None = None  # bedtime (ISO); the session ends when it passes
    topics_tried: int = 0
    tried_clusters: list[str] = Field(default_factory=list)
    cluster_id: str | None = None
    topic: str = ""
    wildcard: bool = False
    questions: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    queue: list[dict[str, Any]] = Field(default_factory=list)
    seen_urls: list[str] = Field(default_factory=list)
    page: dict[str, Any] | None = None
    prediction: str | None = None
    notes: dict[str, Any] | None = None
    reads: int = 0
    empty_rounds: int = 0
    pending: list[dict[str, Any]] = Field(default_factory=list)
    log: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
    next: str = ""
    stop_reason: str | None = None


def _averse(ctx: PetContext, text: str) -> bool:
    low = text.lower()
    return any(a.lower() in low for a in ctx.persona.aversions if a.strip())


def build_explore_graph(ctx: PetContext, checkpointer=None):
    repo = ctx.repo
    P = ctx.providers

    def pick_topic(s: ExploreState) -> dict:
        cp = ctx.params.curiosity
        clusters = [c for c in repo.list_clusters(ctx.pet_id)
                    if c.id not in s.tried_clusters and not _averse(ctx, c.label)]
        wildcard = not clusters or ctx.rng.random() < cp.epsilon
        cluster_id, label, lp = None, "", 0.0
        if not wildcard:
            scores = softmax_scale([c.lp for c in clusters])
            c = clusters[softmax_sample(scores, cp.tau, ctx.rng.random())]
            cluster_id, label, lp = c.id, c.label, c.lp
        else:
            label = _wildcard_topic(ctx, [c.label for c in repo.list_clusters(ctx.pet_id)])
            match = next((c for c in clusters if c.label.lower() == label.lower()), None)
            cluster_id = match.id if match else None
        ctx.emit("topic_picked", {"cluster_id": cluster_id, "label": label, "lp": round(lp, 4),
                                  "wildcard": wildcard, "session": s.session_id})
        ctx.activity(f"wondering about {label}")
        ctx.spend("plan")
        return {"cluster_id": cluster_id, "topic": label, "wildcard": wildcard, "topics_tried": s.topics_tried + 1,
                "tried_clusters": s.tried_clusters + ([cluster_id] if cluster_id else []), "queue": [],
                **ctx.base_payload()}

    def plan_queries(s: ExploreState) -> dict:
        facts = []
        if s.cluster_id:
            facts = [truncate_words(m.content, 30) for m in repo.list_memories(ctx.pet_id, cluster_id=s.cluster_id, limit=5)]
        n = ctx.cfg.scheduler.queries_per_plan
        also_en = "" if ctx.persona.language.startswith("en") else ", plus at least one query in English"
        req = ChatRequest(
            task="plan_queries",
            system=prompts.render(prompts.PLAN_SYSTEM, ctx, also_english=also_en),
            user=prompts.plan_user(s.topic, facts, s.wildcard),
            context={"label": s.topic, "facts": facts, "n": n},
        )
        try:
            plan: QueryPlan = P.explorer.structured(req, QueryPlan)
            queries = [q for q in plan.queries if q.strip()][:n] or [s.topic]
            questions = plan.questions[:3]
        except (ProviderError, BudgetExceeded) as exc:
            ctx.emit("provider_error", {"role": "explorer_llm", "node": "plan_queries", "error": str(exc)[:300]})
            queries, questions = [s.topic], []
        ctx.emit("plan", {"topic": s.topic, "questions": questions, "queries": queries})
        return {"queries": queries, "questions": questions}

    def search(s: ExploreState) -> dict:
        results: list[dict] = []
        pending = list(s.pending)
        seen = set()
        for q in s.queries:
            try:
                hits = P.search.search(q, k=5, language=ctx.persona.language)
            except (ProviderError, BudgetExceeded) as exc:
                ctx.emit("provider_error", {"role": "search", "node": "search", "error": str(exc)[:300]})
                continue
            ctx.spend("search")
            pending.append({"type": "search", "payload": {"query": q, "n": len(hits),
                                                          "energy_cost": ctx.energy_cost("search")}})
            ctx.emit("search", {"query": q, "n": len(hits)})
            for h in hits:
                if h.url not in seen:
                    seen.add(h.url)
                    results.append({**h.to_dict(), "query": q})
        return {"queue": results, "pending": pending}

    def select_sources(s: ExploreState) -> dict:
        limit = min(3, ctx.cfg.scheduler.sources_per_query * max(1, len(s.queries)))
        chosen, skipped = [], []
        for r in s.queue:
            url = r["url"]
            why = None
            if P.guard.blocked(url):
                why = "blocked_domain"
            elif url in s.seen_urls or repo.url_seen(ctx.pet_id, url):
                why = "already_read"
            elif _averse(ctx, r.get("title", "")):
                why = "aversion"
            elif ctx.cfg.safety.moderation and moderation_flags(r.get("title", "") + " " + r.get("snippet", "")):
                why = "moderation"
            if why:
                skipped.append({"url": url, "why": why})
                continue
            chosen.append(r)
            if len(chosen) >= limit:
                break
        ctx.emit("sources_selected", {"topic": s.topic, "chosen": [c["url"] for c in chosen],
                                      "skipped": len(skipped)})
        upd: dict[str, Any] = {"queue": chosen, "seen_urls": s.seen_urls + [c["url"] for c in chosen]}
        if not chosen:
            upd["empty_rounds"] = s.empty_rounds + 1
            if upd["empty_rounds"] >= 3 or s.topics_tried >= 4:
                upd["next"], upd["stop_reason"] = "end", "nothing_new"
            else:
                upd["next"] = "pick"
        else:
            upd["next"] = "fetch"
        return upd

    def fetch_and_sanitize(s: ExploreState) -> dict:
        if not s.queue:
            return {"page": None}
        src, rest = s.queue[0], s.queue[1:]
        try:
            page = P.fetch.fetch(src["url"])
        except (ProviderError, BudgetExceeded) as exc:
            ctx.emit("fetch_failed", {"url": src["url"], "error": str(exc)[:300]})
            return {"page": None, "queue": rest}
        ctx.spend("fetch")
        text = sanitize_fetched(page.text, ctx.cfg.safety.max_fetch_chars)
        if len(text) < 200:
            ctx.emit("fetch_failed", {"url": src["url"], "error": "too little text"})
            return {"page": None, "queue": rest}
        snapshot = None
        if not ctx.clock.simulated:
            rel = f"pages/{s.session_id}/{len(s.log) + 1}.txt"
            ctx.media.local_path(rel).write_text(f"{page.url}\n{page.title}\n\n{text}")
            snapshot = ctx.media.publish(rel)
        return {"page": {"url": page.url, "title": page.title or src.get("title", ""), "text": text,
                         "snapshot": snapshot}, "queue": rest}

    def predict_then_read(s: ExploreState) -> dict:
        page = s.page
        assert page is not None
        ctx.activity(f"reading about {page['title']}")
        title_vec = P.embeddings.embed_one(page["title"])
        known = [m.content for m, _ in repo.knn(ctx.pet_id, title_vec, 3, kinds=NOVELTY_KINDS + ("insight",),
                                                 embed_model=P.embeddings.model_name)]
        try:
            pred: Prediction = P.explorer.structured(ChatRequest(
                task="predict", system=prompts.render(prompts.PREDICT_SYSTEM, ctx),
                user=prompts.predict_user(page["title"], [truncate_words(k, 40) for k in known]),
                context={"title": page["title"], "known": known, "topic": s.topic},
            ), Prediction)
            notes: ReadNotes = P.explorer.structured(ChatRequest(
                task="read", system=prompts.render(prompts.READ_SYSTEM, ctx),
                user=prompts.read_user(page["title"], page["url"], page["text"]),
                context={"title": page["title"], "text": page["text"]},
            ), ReadNotes)
        except (ProviderError, BudgetExceeded) as exc:
            ctx.emit("provider_error", {"role": "explorer_llm", "node": "predict_then_read", "error": str(exc)[:300]})
            return {"notes": None, "prediction": None}
        ctx.spend("read")
        if not notes.safe or (ctx.cfg.safety.moderation and moderation_flags(notes.summary)):
            ctx.emit("discarded", {"url": page["url"], "why": "moderation"})
            return {"notes": None, "prediction": pred.gist}
        return {"notes": notes.model_dump(), "prediction": pred.gist}

    def memorize(s: ExploreState) -> dict:
        page, notes = s.page, s.notes
        assert page is not None and notes is not None
        cp = ctx.params.curiosity
        summary = notes["summary"]
        emb_sum, emb_pred = P.embeddings.embed([summary, s.prediction or page["title"]])
        error = clustering.cos_dist(emb_pred, emb_sum)
        model = P.embeddings.model_name
        k = cp.novelty_k
        today = repo.knn(ctx.pet_id, emb_sum, k, since=ctx.today_start(), kinds=NOVELTY_KINDS, embed_model=model)
        everything = repo.knn(ctx.pet_id, emb_sum, k, kinds=NOVELTY_KINDS, embed_model=model)
        novelty = knn_novelty([d for _, d in today], [d for _, d in everything], cp.episodic_alpha)

        cluster = clustering.assign(ctx, emb_sum, page["title"])
        learn = learnability(error, cluster.lp)
        n_same = len(repo.list_memories(ctx.pet_id, kinds=["episodic"], since=ctx.today_start(),
                                        cluster_id=cluster.id, limit=1000))
        hab = habituation(cp.habituation, n_same)
        lp_before = cluster.lp
        clustering.record_error(ctx, cluster, error)
        w = ctx.drive_model.kind_weight("read")
        satisfaction = w * novelty * learn * hab

        mid = ctx.new_id("m")
        repo.add_memory(Memory(
            id=mid, pet_id=ctx.pet_id, kind="episodic", title=page["title"], content=summary, created_at=ctx.now(),
            embedding=emb_sum, embed_model=model, source_url=page["url"], importance=float(notes["importance"]),
            surprise=round(error, 4), salience=float(notes["salience"]), cluster_id=cluster.id,
            meta={"facts": notes.get("facts", []), "prediction": s.prediction, "topic": s.topic,
                  "session": s.session_id, "novelty": round(novelty, 4), "learnability": round(learn, 4),
                  "habituation": round(hab, 4), "satisfaction": round(satisfaction, 4), "lp": round(lp_before, 4),
                  "snapshot": page.get("snapshot"), "language": ctx.persona.language},
        ))
        if everything and everything[0][1] < 0.3:
            repo.add_link(mid, everything[0][0].id, "similar", weight=round(1 - everything[0][1], 3))
        if s.log:
            repo.add_link(s.log[-1]["memory_id"], mid, "co_occurred")

        payload = {"memory_id": mid, "title": page["title"], "url": page["url"], "cluster_id": cluster.id,
                   "cluster": cluster.label, "novelty": round(novelty, 4), "learnability": round(learn, 4),
                   "habituation": round(hab, 4), "error": round(error, 4), "satisfaction": round(satisfaction, 4),
                   "energy_cost": ctx.energy_cost("read")}
        ctx.emit("read", payload)
        if error > 0.45 and notes["salience"] >= 0.6:
            facts = notes.get("facts") or [summary]
            ctx.hook("finding", {"memory_id": mid, "title": page["title"], "fact": facts[0], "surprise": error})
        return {"pending": s.pending + [{"type": "read", "payload": payload}], "reads": s.reads + 1,
                "log": [{"memory_id": mid, "title": page["title"], "satisfaction": round(satisfaction, 4)}],
                "page": None, "notes": None}

    def update_drives(s: ExploreState) -> dict:
        events = [Event(type=e["type"], t=ctx.now(), payload=e["payload"]) for e in s.pending]
        st = ctx.step_drives(events)
        upd: dict[str, Any] = {"pending": [], **ctx.base_payload()}
        dm = ctx.drive_model
        if st.energy <= ctx.params.energy.nap_below:
            upd["next"], upd["stop_reason"] = "end", "tired"
        elif dm.should_stop_exploring(st):
            upd["next"], upd["stop_reason"] = "end", "satisfied"
        elif s.reads >= ctx.cfg.scheduler.session_max_reads:
            upd["next"], upd["stop_reason"] = "end", "session_cap"
        elif s.deadline and ctx.now() >= datetime.fromisoformat(s.deadline):
            upd["next"], upd["stop_reason"] = "end", "bedtime"
        elif s.queue:
            upd["next"] = "fetch"
        elif s.topics_tried >= 4:
            upd["next"], upd["stop_reason"] = "end", "topics_exhausted"
        else:
            upd["next"] = "pick"
        return upd

    def after_select(s: ExploreState) -> str:
        return {"fetch": "fetch_and_sanitize", "pick": "pick_topic"}.get(s.next, "finish")

    def after_fetch(s: ExploreState) -> str:
        return "predict_then_read" if s.page else "update_drives"

    def after_read(s: ExploreState) -> str:
        return "memorize" if s.notes else "update_drives"

    def after_update(s: ExploreState) -> str:
        return {"fetch": "fetch_and_sanitize", "pick": "pick_topic"}.get(s.next, "finish")

    def finish(s: ExploreState) -> dict:
        # settle any energy spent on searches that never led to a read
        if s.pending:
            ctx.step_drives([Event(type=e["type"], t=ctx.now(), payload=e["payload"]) for e in s.pending])
        ctx.emit("explore_end", {"session": s.session_id, "reads": s.reads, "reason": s.stop_reason or "done",
                                 "drives": ctx.drives_snapshot()})
        return {"pending": [], **ctx.base_payload()}

    g = StateGraph(ExploreState)
    for name, fn in [("pick_topic", pick_topic), ("plan_queries", plan_queries), ("search", search),
                     ("select_sources", select_sources), ("fetch_and_sanitize", fetch_and_sanitize),
                     ("predict_then_read", predict_then_read), ("memorize", memorize),
                     ("update_drives", update_drives), ("finish", finish)]:
        g.add_node(name, fn)
    g.add_edge(START, "pick_topic")
    g.add_edge("pick_topic", "plan_queries")
    g.add_edge("plan_queries", "search")
    g.add_edge("search", "select_sources")
    g.add_conditional_edges("select_sources", after_select, ["fetch_and_sanitize", "pick_topic", "finish"])
    g.add_conditional_edges("fetch_and_sanitize", after_fetch, ["predict_then_read", "update_drives"])
    g.add_conditional_edges("predict_then_read", after_read, ["memorize", "update_drives"])
    g.add_edge("memorize", "update_drives")
    g.add_conditional_edges("update_drives", after_update, ["fetch_and_sanitize", "pick_topic", "finish"])
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


def _wildcard_topic(ctx: PetContext, existing_labels: list[str]) -> str:
    """A new seed from interests, or a random frontier topic from recent memories."""
    existing = {x.lower() for x in existing_labels}
    interests = [i for i in ctx.persona.interests if i.weight > 0 and not _averse(ctx, i.topic)]
    if interests and ctx.rng.random() < 0.5:
        total = sum(i.weight for i in interests)
        u = ctx.rng.random() * total
        for i in interests:
            u -= i.weight
            if u <= 0:
                return i.topic
        return interests[-1].topic
    recent = ctx.repo.list_memories(ctx.pet_id, kinds=["episodic"], limit=20)
    ctx.rng.shuffle(recent)
    for m in recent:
        for p in key_phrases(m.content, 6):
            if p.lower() not in existing and len(p) > 3 and not _averse(ctx, p):
                return p
    return interests[0].topic if interests else "something surprising"
