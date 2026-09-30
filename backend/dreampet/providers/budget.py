"""Budget guard and usage meter. Spending is capped here, in code, never by prompt."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from dreampet.clock import Clock
from dreampet.config import DEFAULT_PRICING, LLM_ROLES, BudgetConfig, RoleConfig
from dreampet.providers.base import BudgetExceeded, Usage


def pricing_for(role: str, rc: RoleConfig) -> dict[str, float]:
    kind = "llm" if role in LLM_ROLES else role
    p = dict(DEFAULT_PRICING.get(kind, {}))
    for k in ("usd_per_mtok_in", "usd_per_mtok_out", "usd_per_call", "usd_per_second"):
        v = getattr(rc, k)
        if v is not None:
            p[k] = v
    return p


def estimate_usd(pricing: dict[str, float], u: Usage) -> float:
    return (
        u.tokens_in / 1e6 * pricing.get("usd_per_mtok_in", 0.0)
        + u.tokens_out / 1e6 * pricing.get("usd_per_mtok_out", 0.0)
        + u.calls * pricing.get("usd_per_call", 0.0)
        + u.seconds * pricing.get("usd_per_second", 0.0)
    )


class Meter:
    def __init__(self, repo, pet_id: str, run_id: str, clock: Clock, tz: str, budgets: BudgetConfig,
                 emit: Callable[[str, dict[str, Any]], None] | None = None):
        self.repo = repo
        self.pet_id = pet_id
        self.run_id = run_id
        self.clock = clock
        self.tz = ZoneInfo(tz)
        self.budgets = budgets
        self.emit = emit or (lambda *_: None)

    def day(self) -> str:
        return self.clock.now().astimezone(self.tz).date().isoformat()

    def spent_today(self, role: str | None = None) -> float:
        return self.repo.spend(self.pet_id, self.run_id, self.day(), role)

    def remaining(self, role: str) -> float:
        total_left = self.budgets.daily_usd_hard_cap - self.spent_today()
        cap = self.budgets.per_role_usd.get(role)
        role_left = cap - self.spent_today(role) if cap is not None else float("inf")
        return max(0.0, min(total_left, role_left))

    def check(self, role: str, est_usd: float) -> None:
        if est_usd <= 0:
            return
        day = self.day()
        spent = self.repo.spend(self.pet_id, self.run_id, day)
        if spent + est_usd > self.budgets.daily_usd_hard_cap:
            self.emit("budget_refused", {"role": role, "est_usd": est_usd, "spent": spent,
                                         "cap": self.budgets.daily_usd_hard_cap, "scope": "daily_hard_cap"})
            raise BudgetExceeded(f"daily hard cap ${self.budgets.daily_usd_hard_cap:.2f} would be exceeded")
        cap = self.budgets.per_role_usd.get(role)
        if cap is not None:
            spent_role = self.repo.spend(self.pet_id, self.run_id, day, role)
            if spent_role + est_usd > cap:
                self.emit("budget_refused", {"role": role, "est_usd": est_usd, "spent": spent_role,
                                             "cap": cap, "scope": "role"})
                raise BudgetExceeded(f"{role} budget ${cap:.2f} would be exceeded")

    def record(self, role: str, u: Usage, usd: float, est_usd: float) -> None:
        self.repo.add_usage(self.pet_id, self.run_id, self.day(), role, calls=u.calls, tokens_in=u.tokens_in,
                            tokens_out=u.tokens_out, seconds=u.seconds, usd=usd, est_usd=est_usd)
