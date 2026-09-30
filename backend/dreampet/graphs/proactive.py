"""proactive: draft_message. Drafts a message the pet sends first; the scheduler decides when."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from dreampet import prompts
from dreampet.graphs.common import GraphState
from dreampet.providers.base import BudgetExceeded, ChatRequest, ProviderError

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext

TRIGGER_TEXT = {
    "wake_dream": "you just woke up from a dream and want to tell them about it",
    "finding": "you just read something surprising and want to share one fact",
    "silence": "you haven't heard from them in a long while",
    "drift": "you've noticed you're getting into a new topic",
}


class ProactiveState(GraphState):
    trigger: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    text: str = ""


def build_proactive_graph(ctx: PetContext):
    def draft_message(s: ProactiveState) -> dict:
        req = ChatRequest(
            task="proactive",
            system=prompts.render(prompts.PROACTIVE_SYSTEM, ctx, trigger=TRIGGER_TEXT.get(s.trigger, s.trigger)),
            user=f"Details: {s.detail}", max_tokens=120,
            context={"trigger": s.trigger, "detail": s.detail, "name": ctx.persona.name},
        )
        try:
            return {"text": ctx.providers.chat.generate(req).strip()}
        except (ProviderError, BudgetExceeded):
            return {"text": ""}

    g = StateGraph(ProactiveState)
    g.add_node("draft_message", draft_message)
    g.add_edge(START, "draft_message")
    g.add_edge("draft_message", END)
    return g.compile()
