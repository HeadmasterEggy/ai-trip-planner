"""Supervisor delegation, geography conflicts and the rendering helpers."""

from __future__ import annotations

import pytest

from trip_planner.contracts import ProposalItem, RevisionRequest, TripPlan
from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.ports import AgentContext, ToolGateway
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.specialists.itinerary import DraftActivity, ItineraryDraft, travel_conflicts
from trip_planner.supervisor import (
    create_revision_tools,
    create_supervisor_tools,
    dispatch_with_supervisor,
)
from trip_planner.tools.booking import MockBooking
from trip_planner.tools.maps import MapsAdapter
from trip_planner.ui.render import agent_row, budget_block, chip, item_card, proposal_items
from trip_planner.workflow import run_orchestrator


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(
        tripId="t1",
        round=1,
        tools=ToolGateway(maps=MapsAdapter(), booking=MockBooking()),
        mem=InMemoryStore(),
    )


def test_one_delegation_tool_per_specialist_with_a_stable_name(ctx):
    tools = create_supervisor_tools(
        ALL_SPECIALISTS, DEMO_BRIEF, ctx, lambda p: None, lambda *a: None
    )
    names = [t.name for t in tools]
    assert names == [
        "ask_itinerary_specialist",
        "ask_transport_specialist",
        "ask_accommodation_specialist",
        "ask_destination_guide_specialist",
        "ask_dining_specialist",
    ]
    assert all(t.description for t in tools)  # an empty description hides the tool


def test_a_delegation_tool_runs_its_specialist_and_reports_progress(ctx):
    seen, events = [], []
    tools = create_supervisor_tools(
        ALL_SPECIALISTS, DEMO_BRIEF, ctx, seen.append, lambda *a: events.append(a)
    )
    transport = next(t for t in tools if t.name == "ask_transport_specialist")
    transport.invoke({})
    assert [p.agent for p in seen] == ["transport"]
    assert [e[0] for e in events] == ["agent_started", "agent_completed"]


def test_the_delegation_tools_take_no_arguments(ctx):
    """The supervisor chooses *who*, never *what*.

    A tool argument would be somewhere for the model to restate the request, and
    the deterministic rules downstream would then be checking the restatement
    rather than the brief. An empty schema is the constraint, not an oversight.
    """
    request = RevisionRequest(
        tripId="t1", targetAgent="transport", reason="over budget", constraints=["cut 30%"]
    )
    tools = create_supervisor_tools(
        ALL_SPECIALISTS, DEMO_BRIEF, ctx, lambda p: None, lambda *a: None
    ) + create_revision_tools(
        ALL_SPECIALISTS, [request], DEMO_BRIEF, ctx, lambda p: None, lambda *a: None
    )

    assert tools
    for entry in tools:
        assert entry.args == {}, entry.name
        assert entry.tool_call_schema.model_json_schema().get("properties", {}) == {}, entry.name


def test_revision_tools_are_built_only_for_pending_requests(ctx):
    request = RevisionRequest(
        tripId="t1", targetAgent="accommodation", reason="over budget", constraints=["cut 30%"]
    )
    tools = create_revision_tools(
        ALL_SPECIALISTS, [request], DEMO_BRIEF, ctx, lambda p: None, lambda *a: None
    )
    assert [t.name for t in tools] == ["revise_accommodation_specialist"]


def test_dispatch_raises_without_a_model_so_the_caller_can_fall_back(ctx, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="requires a configured routed chat model"):
        dispatch_with_supervisor(ALL_SPECIALISTS, DEMO_BRIEF, ctx, lambda *a: None)


def test_the_graph_still_plans_when_the_supervisor_is_unavailable(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    plan = run_orchestrator(DEMO_BRIEF)  # no injected specialists -> supervisor path
    assert {s.id for s in plan.sections} == {s.name for s in ALL_SPECIALISTS}


def test_an_unreachable_activity_is_reported_not_silently_rescheduled(ctx):
    # The mock route takes 140 minutes; only 30 are available here.
    draft = ItineraryDraft(
        summary="s",
        activities=[
            DraftActivity(
                day=1, startTime="09:00", endTime="10:00", location="A", detail="d", estCost=10
            ),
            DraftActivity(
                day=1, startTime="10:30", endTime="12:00", location="B", detail="d", estCost=10
            ),
        ],
    )
    conflicts = travel_conflicts(draft, ctx)
    assert len(conflicts) == 1
    assert "needs 140 minutes but only 30" in conflicts[0]


def test_two_activities_at_one_location_need_no_travel_time(ctx):
    draft = ItineraryDraft(
        summary="s",
        activities=[
            DraftActivity(
                day=1, startTime="09:00", endTime="10:00", location="A", detail="d", estCost=10
            ),
            DraftActivity(
                day=1, startTime="10:00", endTime="11:00", location="A", detail="d", estCost=10
            ),
        ],
    )
    assert travel_conflicts(draft, ctx) == []


def test_rendered_content_is_escaped():
    card = item_card(ProposalItem(kind="note", detail="<script>x</script>", location="A & B"))
    assert "<script>" not in card
    assert "&lt;script&gt;" in card
    assert "A &amp; B" in card


def test_an_over_budget_plan_renders_differently_from_one_that_fits():
    plan = run_orchestrator(DEMO_BRIEF, None)
    over = plan.model_copy(update={"estTotal": plan.budgetTotal * 2})
    under = plan.model_copy(update={"estTotal": plan.budgetTotal / 2})
    assert "tp-bar--over" in budget_block(over) and "over budget" in budget_block(over)
    assert "tp-bar--over" not in budget_block(under) and "under budget" in budget_block(under)


def test_empty_states_are_distinguished():
    assert "still being prepared" in proposal_items(None)


def test_a_running_agent_is_the_only_row_that_animates():
    assert "tp-dot" in agent_row("Day plan", "agent_started", 1, None)
    assert "tp-dot" not in agent_row("Day plan", "agent_completed", 1, None)
    assert "round 2" in agent_row("Day plan", "agent_completed", 2, None)


def test_status_chips_cover_every_section_status():
    for status in ("planning", "draft", "needs_you", "confirmed"):
        assert f"tp-chip--{status}" in chip(status)


def test_plan_stays_valid_after_render_round_trip():
    plan = run_orchestrator(DEMO_BRIEF, None)
    assert isinstance(plan, TripPlan)
    for section in plan.sections:
        proposal_items(section.proposal)  # must not raise on any real proposal
