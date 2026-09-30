"""awake_tick: update_drives → route (idle / explore / nap / sleep). Returns the next state.

The route is pure threshold logic, never an LLM."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from dreampet.graphs.common import GraphState

if TYPE_CHECKING:
    from dreampet.runtime.context import PetContext


class AwakeState(GraphState):
    bedtime_at: str | None = None
    next: str = "idle"


def build_awake_tick_graph(ctx: PetContext):
    def update_drives(s: AwakeState) -> dict:
        ctx.step_drives()
        return ctx.base_payload()

    def route(s: AwakeState) -> dict:
        st = ctx.state
        if s.bedtime_at and ctx.now() >= datetime.fromisoformat(s.bedtime_at):
            nxt = "sleep"
        elif st.energy <= ctx.params.energy.nap_below:
            nxt = "nap"
        elif ctx.drive_model.wants_to_explore(st):
            nxt = "explore"
        else:
            nxt = "idle"
        return {"next": nxt}

    g = StateGraph(AwakeState)
    g.add_node("update_drives", update_drives)
    g.add_node("route", route)
    g.add_edge(START, "update_drives")
    g.add_edge("update_drives", "route")
    g.add_edge("route", END)
    return g.compile()
