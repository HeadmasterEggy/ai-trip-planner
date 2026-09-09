"""Behaviour tests for the planning layer.

These run without a model key or network: every specialist falls back to
deterministic output, which is exactly the path a reviewer or CI hits.
"""

from __future__ import annotations

import pytest

from trip_planner.budget import assess_budget, cost_of, sum_usd
from trip_planner.contracts import AgentProposal, ProposalItem, TripBrief
from trip_planner.memory import InMemoryStore
from trip_planner.ports import AgentContext, Place, RouteLeg, ToolGateway
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.specialists.accommodation import split_stay
from trip_planner.tools.booking import MockBooking
from trip_planner.tools.maps import MapsAdapter
from trip_planner.workflow import (
    OrchestratorOptions,
    ProgressEvent,
    detect_conflicts,
    run_orchestrator,
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
    events: list[ProgressEvent] = []
    plan = run_orchestrator(brief, OrchestratorOptions(on_progress=events.append))
    assert {s.id for s in plan.sections} == {s.name for s in ALL_SPECIALISTS}
    assert plan.round >= 1
    assert any(e.type == "agent_started" for e in events)
    assert any(e.type == "agent_completed" for e in events)
    assert any(h.id == "confirm-brief" for h in plan.hitl)


def test_the_graph_stops_at_the_round_limit(brief):
    plan = run_orchestrator(brief, OrchestratorOptions(max_rounds=1))
    assert plan.round == 1


def test_mock_tools_are_deterministic():
    maps, booking = MapsAdapter(), MockBooking()
    assert maps.route(frm="A", to="B") == [RouteLeg("train", 140, 90.0, "mock A -> B")]
    assert maps.places(near="A", category="sight") == [Place("Mock sight near A", "sight", 4.5)]
    flights = booking.search_flights(
        frm="Sydney", to="Tokyo", depart="2026-06-15", ret="2026-06-22", passengers=2
    )
    assert flights[0].priceUsd == 1240.0  # 310 * 2 passengers * 2 legs
