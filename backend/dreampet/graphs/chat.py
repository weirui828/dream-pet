"""chat: load_context → retrieve_memories → respond_in_persona → log_event → update_drives.

One reply per invoke; one checkpointed thread per conversation (`chat:{pet}:{conversation}`)."""

from __future__ import annotations

import operator
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, Any

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from dreampet import prompts
from dreampet.drives.base import Event
from dreampet.drives.signals import habituation, knn_novelty
from dreampet.graphs.common import GraphState
from dreampet.memory.repo import Memory
from dreampet.memory.retrieval import retrieve
from dreampet.providers.base import BudgetExceeded, ChatRequest, ProviderError
from dreampet.text import key_phrases, truncate_words

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext

CHAT_LEARNABILITY = 0.4

MODE_NOTES = {
    "awake": "",
    "tired": "You are very tired and low on energy: answer in one short, sleepy sentence.",
    "grumpy": "You were just woken up from sleep and are a bit grumpy about it, but still fond of your owner.",
    "sleeptalk": "",
}


class ChatState(GraphState):
    conversation_id: str = ""
    message: str = ""
    mode: str = "awake"  # awake | tired | grumpy | sleeptalk
    history: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
    recalled: list[dict[str, Any]] = Field(default_factory=list)
    reply: str = ""
    reply_id: str = ""
    pending: list[dict[str, Any]] = Field(default_factory=list)


def build_chat_graph(ctx: PetContext, checkpointer=None):
    repo = ctx.repo
    P = ctx.providers

    def load_context(s: ChatState) -> dict:
        return {**ctx.base_payload()}

    def retrieve_memories(s: ChatState) -> dict:
        if s.mode == "sleeptalk":
            recent = repo.list_memories(ctx.pet_id, kinds=["episodic", "dream"], limit=5)
            return {"recalled": [{"id": m.id, "title": m.title, "content": truncate_words(m.content, 40)} for m in recent]}
        try:
            hits = retrieve(ctx, s.message, k=3, kinds=["episodic", "semantic", "insight", "dream", "chat"])
        except (ProviderError, BudgetExceeded) as exc:
            ctx.emit("provider_error", {"role": "embeddings", "node": "retrieve_memories", "error": str(exc)[:300]})
            hits = []
        return {"recalled": [{"id": m.id, "title": m.title, "kind": m.kind, "content": truncate_words(m.content, 60),
                              "score": sc, "source_url": m.source_url} for m, sc in hits]}

    def respond_in_persona(s: ChatState) -> dict:
        if s.mode == "sleeptalk":
            phrases = []
            for r in s.recalled:
                phrases.extend(key_phrases((r.get("title") or "") + ". " + r["content"], 2))
            req = ChatRequest(task="sleeptalk", system=prompts.render(prompts.SLEEPTALK_SYSTEM, ctx),
                              user=f"Owner (while you sleep): {s.message}", max_tokens=60,
                              context={"phrases": phrases[:6] or ["the stars"]})
        else:
            mode = s.mode
            req = ChatRequest(
                task="chat",
                system=prompts.render(prompts.CHAT_SYSTEM, ctx, mode_note=MODE_NOTES.get(mode, "")),
                user=prompts.chat_user(s.message, s.recalled, s.history, s.drives),
                max_tokens=80 if mode == "tired" else 350,
                context={"message": s.message, "recalled": s.recalled, "name": ctx.persona.name,
                         "temperament": ctx.persona.temperament, "tired": mode == "tired", "mode": mode,
                         "question_back": ctx.persona.traits.sociability >= 60 and len(s.history) % 3 == 0},
            )
        try:
            reply = P.chat.generate(req)
        except BudgetExceeded:
            reply = "(I'm out of thinking budget for today — let's talk tomorrow!)"
        except ProviderError as exc:
            ctx.emit("provider_error", {"role": "chat_llm", "node": "respond_in_persona", "error": str(exc)[:300]})
            reply = "(I can't think straight right now — my chat provider isn't answering.)"
        ctx.spend("chat_turn")
        return {"reply": reply.strip()}

    def log_event(s: ChatState) -> dict:
        now = ctx.now()
        owner_id, pet_id = ctx.new_id("msg"), ctx.new_id("msg")
        repo.add_message(id=owner_id, pet_id=ctx.pet_id, conversation_id=s.conversation_id, t=now, sender="owner",
                         content=s.message, recalled=[], read=True)
        repo.add_message(id=pet_id, pet_id=ctx.pet_id, conversation_id=s.conversation_id,
                         t=now + timedelta(microseconds=1), sender="pet", content=s.reply,
                         recalled=[{"id": r["id"], "title": r.get("title")} for r in s.recalled], read=True)
        ctx.bus.publish("message", {"id": pet_id, "conversation_id": s.conversation_id, "sender": "pet",
                                    "content": s.reply, "t": now.isoformat()})
        pending: list[dict] = []
        if s.mode != "sleeptalk":
            cp = ctx.params.curiosity
            model = P.embeddings.model_name
            try:
                vec = P.embeddings.embed_one(s.message)
                today = repo.knn(ctx.pet_id, vec, cp.novelty_k, since=ctx.today_start(), embed_model=model)
                allm = repo.knn(ctx.pet_id, vec, cp.novelty_k, embed_model=model)
                novelty = knn_novelty([d for _, d in today], [d for _, d in allm], cp.episodic_alpha)
            except (ProviderError, BudgetExceeded):
                vec, novelty = None, 0.3
            recent_turns = len(repo.messages(ctx.pet_id, since=now - timedelta(hours=1), limit=200)) // 2
            hab = habituation(cp.habituation, max(0, recent_turns - 1))
            mid = ctx.new_id("m")
            repo.add_memory(Memory(
                id=mid, pet_id=ctx.pet_id, kind="chat", title="Chat with my owner",
                content=f"My owner said: {s.message}\nI replied: {s.reply}", created_at=now, embedding=vec,
                embed_model=model if vec is not None else None, importance=0.5, salience=0.4,
                meta={"conversation_id": s.conversation_id, "recalled": [r["id"] for r in s.recalled]},
            ))
            for r in s.recalled:
                repo.add_link(mid, r["id"], "co_occurred", weight=0.5)
            cost = ctx.energy_cost("chat_turn") + (ctx.energy_cost("wake_from_sleep") if s.mode == "grumpy" else 0)
            # A message has no predict-then-read step, so learnability is a fixed moderate value:
            # chatting helps with boredom, but one message shouldn't cure it.
            payload = {"memory_id": mid, "novelty": round(novelty, 4), "learnability": CHAT_LEARNABILITY,
                       "habituation": round(hab, 4), "energy_cost": cost, "conversation_id": s.conversation_id}
            pending.append({"type": "chat", "payload": payload})
            ctx.emit("chat", {**payload, "message": truncate_words(s.message, 30)})
        else:
            ctx.emit("sleeptalk", {"message": truncate_words(s.message, 30), "reply": s.reply})
        return {"history": [{"sender": "owner", "content": s.message}, {"sender": "pet", "content": s.reply}],
                "pending": pending, "reply_id": pet_id}

    def update_drives(s: ChatState) -> dict:
        ctx.step_drives([Event(type=e["type"], t=ctx.now(), payload=e["payload"]) for e in s.pending])
        return {"pending": [], **ctx.base_payload()}

    g = StateGraph(ChatState)
    for name, fn in [("load_context", load_context), ("retrieve_memories", retrieve_memories),
                     ("respond_in_persona", respond_in_persona), ("log_event", log_event),
                     ("update_drives", update_drives)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "retrieve_memories")
    g.add_edge("retrieve_memories", "respond_in_persona")
    g.add_edge("respond_in_persona", "log_event")
    g.add_edge("log_event", "update_drives")
    g.add_edge("update_drives", END)
    return g.compile(checkpointer=checkpointer)
