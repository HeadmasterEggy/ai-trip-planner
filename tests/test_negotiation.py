"""Budget negotiation: asking for the right amount, and knowing when to stop."""

from __future__ import annotations

import pytest

from trip_planner.contracts import AgentProposal, ProposalItem
from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.ui.render import negotiation_verdict
from trip_planner.workflow import OrchestratorOptions, detect_conflicts, run_orchestrator


def priced(agent: str, cost: float) -> AgentProposal:
    return AgentProposal(
        agent=agent,
        summary="s",
        items=[ProposalItem(kind="x", detail="d", estCost=cost)],
        assumptions=[],
    )


def options(**kwargs) -> OrchestratorOptions:
    return OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), **kwargs)


def test_the_ask_is_sized_to_the_actual_shortfall():
    """A plan barely over budget must not be told to find a third of itself."""
    brief = DEMO_BRIEF.model_copy(update={"budgetTotal": 1000.0})
    requests = detect_conflicts([priced("accommodation", 1030.0)], brief)
    constraint = requests[0].constraints[0]
    assert "USD 30.00" in constraint
    assert "30%" not in constraint


def test_the_shortfall_is_split_by_share_of_the_spend():
    brief = DEMO_BRIEF.model_copy(update={"budgetTotal": 1000.0})
    requests = detect_conflicts([priced("accommodation", 900.0), priced("transport", 300.0)], brief)
    asks = {r.targetAgent: r.constraints[0] for r in requests}
    # 200 over, split 900:300 -> 150 and 50.
    assert "USD 150.00" in asks["accommodation"]
    assert "USD 50.00" in asks["transport"]


def test_a_specialist_with_no_cost_is_never_asked_to_cut():
    brief = DEMO_BRIEF.model_copy(update={"budgetTotal": 100.0})
    requests = detect_conflicts([priced("accommodation", 200.0), priced("dining", 0.0)], brief)
    assert {r.targetAgent for r in requests} == {"accommodation"}


def test_a_revision_that_changes_nothing_stops_the_negotiation():
    plan = run_orchestrator(DEMO_BRIEF, options())
    assert any(entry.stalled for entry in plan.negotiation)


@pytest.mark.parametrize("limit", [3, 5, 8])
def test_stalling_stops_early_no_matter_how_many_rounds_remain(limit):
    """The point of detecting a stall is not spending the rest of the budget on
    rounds that produce an identical plan."""
    plan = run_orchestrator(DEMO_BRIEF, options(max_rounds=limit))
    assert plan.round == 3


def test_a_stalled_plan_says_so_rather_than_blaming_the_round_limit():
    plan = run_orchestrator(DEMO_BRIEF, options())
    escalation = next(h for h in plan.hitl if h.type == "escalation")
    assert "nothing further to give" in escalation.detail
    assert "did not converge within" not in escalation.detail

    verdict, explanation = negotiation_verdict(plan)
    assert verdict == "stuck"
    assert "needs a change to the brief" in explanation


def test_a_plan_within_budget_raises_no_budget_conflict():
    brief = DEMO_BRIEF.model_copy(update={"budgetTotal": 100_000.0})
    requests = detect_conflicts([priced("accommodation", 500.0)], brief)
    assert not [r for r in requests if "over budget" in r.reason]
