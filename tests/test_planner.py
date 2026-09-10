"""Behaviour tests for the planning layer.

These run without a model key or network: every specialist falls back to
deterministic output, which is exactly the path a reviewer or CI hits.
"""

from __future__ import annotations

import pytest

from trip_planner import workflow as workflow_module
from trip_planner.budget import assess_budget, cost_of, sum_usd
from trip_planner.contracts import AgentProposal, ProposalItem, TripBrief, brief_problem
from trip_planner.memory import InMemoryStore
from trip_planner.ports import AgentContext, Place, RouteLeg, ToolGateway
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.specialists.accommodation import split_stay
from trip_planner.specialists.itinerary import ACTIVITY_BUDGET_SHARE, DEFAULT_ACTIVITY_COST_USD
from trip_planner.tools.booking import MockBooking
from trip_planner.tools.maps import MapsAdapter
from trip_planner.workflow import (
    OrchestratorOptions,
    detect_conflicts,
    run_orchestrator,
    run_orchestrator_stream,
)


@pytest.fixture
def brief() -> TripBrief:
    return TripBrief(
        tripId="t1",
        destination="Tokyo & Kyoto",
        dates=("2026-06-15", "2026-06-22"),
        groupSize=2,
        budgetTotal=4000,
        nationality="Australian",
    )


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(
        tripId="t1",
        round=1,
        tools=ToolGateway(maps=MapsAdapter(), booking=MockBooking()),
        mem=InMemoryStore(),
    )


def test_money_is_summed_in_cents_not_floats():
    # 0.1 + 0.2 must not drift a policy threshold.
    assert sum_usd([0.1, 0.2]) == 0.3
    _, overrun = assess_budget([1100.0], 1000.0)
    assert overrun == pytest.approx(10.0)


def test_a_nonsense_cost_is_treated_as_zero_rather_than_failing_the_plan():
    proposal = AgentProposal(
        agent="dining",
        summary="s",
        items=[ProposalItem(kind="meal", detail="d", estCost=None)],
        assumptions=[],
    )
    assert cost_of(proposal) == 0.0


def test_schedule_fields_must_be_coherent():
    with pytest.raises(ValueError):
        ProposalItem(kind="a", detail="d", day=1, startTime="11:00", endTime="09:00")
    with pytest.raises(ValueError):
        ProposalItem(kind="a", detail="d", startTime="09:00", endTime="11:00")  # no day


def test_multi_city_stay_splits_into_contiguous_segments(brief):
    segments = split_stay(brief)
    assert [(s.city, s.nights) for s in segments] == [("Tokyo", 4), ("Kyoto", 3)]
    assert segments[0].checkOut == segments[1].checkIn


def test_an_unplannable_brief_is_refused_before_any_specialist_runs(brief):
    """The orchestrator checks feasibility up front, so a caller gets one
    reason rather than a failure four agents deep."""
    one_night = brief.model_copy(update={"dates": ("2026-06-15", "2026-06-16")})

    with pytest.raises(ValueError, match="at least 2 nights"):
        run_orchestrator(one_night)
    # The streaming entry point refuses before it even builds a graph, so a caller
    # that was going to watch the run gets the same single reason.
    with pytest.raises(ValueError, match="at least 2 nights"):
        run_orchestrator_stream(one_night)


def test_brief_problem_reports_rather_than_raises(brief):
    """Callers treat it as a check, so it must answer for every input."""
    assert brief_problem(brief) is None
    assert brief_problem(brief.model_copy(update={"destination": ""}))
    assert brief_problem(brief.model_copy(update={"destination": "   "}))
    assert "real dates" in brief_problem(
        brief.model_copy(update={"dates": ("2026-6-15", "2026-06-22")})
    )


def test_the_offline_itinerary_respects_the_activity_cap(brief):
    """The deterministic fallback is the path CI and a key-less deployment
    always take, so it has to obey the cap the model prompt is held to.

    A flat USD 60 per day ignored it: a three-day trip on USD 300 produced USD
    180 of activities against a USD 120 cap.
    """
    tight = brief.model_copy(update={"budgetTotal": 300, "dates": ("2026-06-15", "2026-06-18")})
    plan = run_orchestrator(tight, OrchestratorOptions(specialists=ALL_SPECIALISTS, max_rounds=1))
    activities = next(s.estCost for s in plan.sections if s.id == "itinerary")

    assert activities > 0
    assert activities <= 300 * ACTIVITY_BUDGET_SHARE
    assert activities == round(300 * ACTIVITY_BUDGET_SHARE, 2)  # the cap, not a flat guess


def test_a_comfortable_budget_still_gets_the_plain_daily_estimate(brief):
    """Clamping must not turn the fallback into a budget-spending target."""
    plan = run_orchestrator(brief, OrchestratorOptions(specialists=ALL_SPECIALISTS, max_rounds=1))
    activities = next(s.estCost for s in plan.sections if s.id == "itinerary")
    assert activities == 7 * DEFAULT_ACTIVITY_COST_USD


def test_every_specialist_produces_a_valid_proposal_without_a_model_key(brief, ctx):
    for specialist in ALL_SPECIALISTS:
        proposal = specialist.invoke(brief, ctx)
        assert proposal.agent == specialist.name
        assert proposal.summary
        assert proposal.assumptions


def test_a_time_overlap_names_the_blocked_window_and_its_owner(brief):
    """The convergence fix: a revising specialist only sees its own proposal,
    so the constraint has to say what to avoid and who holds it."""
    transport = AgentProposal(
        agent="transport",
        summary="s",
        items=[
            ProposalItem(
                kind="transport",
                detail="d",
                day=4,
                startTime="09:00",
                endTime="11:20",
                location="Tokyo → Kyoto",
                estCost=100,
            )
        ],
        assumptions=[],
    )
    itinerary = AgentProposal(
        agent="itinerary",
        summary="s",
        items=[
            ProposalItem(
                kind="activity",
                detail="d",
                day=4,
                startTime="09:30",
                endTime="12:00",
                location="Somewhere",
                estCost=50,
            )
        ],
        assumptions=[],
    )
    requests = detect_conflicts([transport, itinerary], brief)
    overlap = next(r for r in requests if r.targetAgent == "itinerary")
    constraint = next(c for c in overlap.constraints if "keep clear" in c)
    assert "09:00-11:20" in constraint
    assert "transport" in constraint
    assert "Tokyo → Kyoto" in constraint


def test_an_over_budget_plan_asks_only_the_costly_specialists(brief):
    proposals = [
        AgentProposal(
            agent="accommodation",
            summary="s",
            items=[ProposalItem(kind="hotel", detail="d", estCost=5000)],
            assumptions=[],
        ),
        AgentProposal(agent="dining", summary="s", items=[], assumptions=[]),
    ]
    targets = {r.targetAgent for r in detect_conflicts(proposals, brief)}
    assert targets == {"accommodation"}  # a zero-cost specialist has nothing to give back


def test_the_graph_runs_every_specialist_and_emits_progress(brief):
    stream = run_orchestrator_stream(brief, OrchestratorOptions())
    events = list(stream)
    plan = stream.plan

    assert plan is not None  # reading the plan is what the drain is for
    assert {s.id for s in plan.sections} == {s.name for s in ALL_SPECIALISTS}
    assert plan.round >= 1
    assert any(e.type == "agent_started" for e in events)
    assert any(e.type == "agent_completed" for e in events)
    assert any(h.id == "confirm-brief" for h in plan.hitl)


def test_the_graph_stops_at_the_round_limit(brief):
    plan = run_orchestrator(brief, OrchestratorOptions(max_rounds=1))
    assert plan.round == 1


def test_the_graph_is_compiled_once_and_shared(brief, monkeypatch):
    """A run's dependencies are injected, so the graph is built at import.

    Rebuilding it per request was the reason the graph factory took options at
    all; now a test that patches the factory proves the entry points do not call
    it.
    """
    calls = []

    def factory():
        calls.append(1)
        return workflow_module._GRAPH

    monkeypatch.setattr(workflow_module, "create_orchestrator_graph", factory)
    run_orchestrator(brief, OrchestratorOptions(specialists=ALL_SPECIALISTS, max_rounds=1))
    run_orchestrator(brief, OrchestratorOptions(specialists=ALL_SPECIALISTS, max_rounds=1))

    assert calls == []


def test_two_runs_do_not_share_their_context(brief):
    """Round limits and per-run scratch must not leak between invocations.

    `extras` is the one to watch: if it lived on the graph rather than on the
    run, traces would accumulate and the UI would show every specialist twice.
    """
    one_round = OrchestratorOptions(specialists=ALL_SPECIALISTS, max_rounds=1)
    first = run_orchestrator(brief, one_round)
    second = run_orchestrator(brief, one_round)

    assert first.round == 1
    assert len(first.traces) == len(ALL_SPECIALISTS)  # one round, one trace each
    assert len(second.traces) == len(ALL_SPECIALISTS)  # not ten: extras are per run

    # And a later run is not capped by the earlier run's limit.
    multi = run_orchestrator(brief, OrchestratorOptions(specialists=ALL_SPECIALISTS))
    assert multi.round > 1


def test_mock_tools_are_deterministic():
    maps, booking = MapsAdapter(), MockBooking()
    assert maps.route(frm="A", to="B") == [RouteLeg("train", 140, 90.0, "mock A -> B")]
    assert maps.places(near="A", category="sight") == [Place("Mock sight near A", "sight", 4.5)]
    flights = booking.search_flights(
        frm="Sydney", to="Tokyo", depart="2026-06-15", ret="2026-06-22", passengers=2
    )
    assert flights[0].priceUsd == 1240.0  # 310 * 2 passengers * 2 legs
