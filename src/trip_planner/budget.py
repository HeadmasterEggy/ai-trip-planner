"""USD totals and budget policy.

Ported from `packages/orchestrator/src/budget.ts`. Money is summed in integer
cents so repeated float addition cannot drift the total past a policy
threshold.
"""

from __future__ import annotations

import math

from .contracts import AgentProposal, TripSection

# Any overrun is worth negotiating; >10% is the explicit red line that
# escalates to a human. An unresolved conflict after K rounds also escalates,
# even below this line.
NEGOTIATION_OVERRUN_PCT = 0.0
ESCALATION_OVERRUN_PCT = 10.0


def _cents(amount: float) -> int:
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("Costs must be finite, non-negative USD amounts.")
    return round(amount * 100)


def sum_usd(amounts: list[float]) -> float:
    return sum(_cents(a) for a in amounts) / 100


def _safe_cost(value: float | None) -> float:
    """One specialist returning a nonsense estCost must not take down the plan.

    Treat it as 0: the proposal detail still records what was proposed, and the
    section simply shows $0.
    """
    if value is None or not math.isfinite(value) or value < 0:
        return 0.0
    return float(value)


def cost_of(proposal: AgentProposal) -> float:
    return sum_usd([_safe_cost(item.estCost) for item in proposal.items])


def assess_budget(amounts: list[float], budget_total: float) -> tuple[float, float]:
    """Return (estTotal, overrunPct). Percentages are not rounded before policy
    checks, because 10.004% is still over the 10% line."""
    budget_cents = _cents(budget_total)
    if budget_cents <= 0:
        raise ValueError("Total budget must be at least USD 0.01.")
    est_total = sum_usd(amounts)
    overrun_pct = ((_cents(est_total) - budget_cents) / budget_cents) * 100
    return est_total, overrun_pct


def roll_up_cost(sections: list[TripSection], budget_total: float) -> tuple[float, float]:
    return assess_budget([s.estCost for s in sections], budget_total)
