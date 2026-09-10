"""Choices offered at a checkpoint, and what happens when one is taken."""

from __future__ import annotations

import pytest

from trip_planner.decisions import apply_decision, collect_decisions
from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.specialists.accommodation import stay_choice_key
from trip_planner.workflow import OrchestratorOptions, run_orchestrator


@pytest.fixture
def mem() -> InMemoryStore:
    return InMemoryStore()


def options_for(mem: InMemoryStore) -> OrchestratorOptions:
    return OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=mem)


@pytest.fixture
def plan(mem):
    return run_orchestrator(DEMO_BRIEF, options_for(mem))


def stay_checkpoint(plan, city="Tokyo"):
    return next(c for c in plan.hitl if c.id == f"choose-stay-{city}")


def stay_cost_of(plan):
    return next(s for s in plan.sections if s.id == "accommodation").estCost


def test_a_choice_checkpoint_offers_what_the_specialist_compared(plan):
    checkpoint = stay_checkpoint(plan)
    assert checkpoint.type == "confirm_choice"
    assert len(checkpoint.options) > 1
    assert checkpoint.preferenceKey == stay_choice_key("Tokyo")
    # The specialist's own pick is pre-selected, so ignoring the checkpoint
    # still leaves a complete plan.
    assert checkpoint.selected in {o.id for o in checkpoint.options}
    assert sum(o.recommended for o in checkpoint.options) == 1


def test_a_single_candidate_is_not_a_decision(mem):
    class OneOption:
        def search_stays(self, *, city, check_in, check_out, guests):
            from trip_planner.tools.booking import MockBooking

            return MockBooking().search_stays(
                city=city, check_in=check_in, check_out=check_out, guests=guests
            )[:1]

        def search_flights(self, **kwargs):
            from trip_planner.tools.booking import MockBooking

            return MockBooking().search_flights(**kwargs)

    from trip_planner.ports import ToolGateway
    from trip_planner.tools.maps import MapsAdapter

    plan = run_orchestrator(
        DEMO_BRIEF,
        OrchestratorOptions(
            specialists=ALL_SPECIALISTS,
            mem=mem,
            tools=ToolGateway(maps=MapsAdapter(), booking=OneOption()),
        ),
    )
    assert not [c for c in plan.hitl if c.type == "confirm_choice"]


def test_taking_a_choice_changes_the_plan_and_says_why(plan, mem):
    checkpoint = stay_checkpoint(plan)
    before = stay_cost_of(plan)
    dearest = max(checkpoint.options, key=lambda o: o.estCost or 0)

    after = apply_decision(plan, checkpoint.id, dearest.id, options_for(mem))

    assert stay_cost_of(after) != before
    assert stay_checkpoint(after).status == "approved"
    assert stay_checkpoint(after).selected == dearest.id
    proposal = next(s for s in after.sections if s.id == "accommodation").proposal
    assert any("Honoured the stay you confirmed" in a for a in proposal.assumptions)


def test_a_confirmed_choice_survives_a_later_replan(plan, mem):
    checkpoint = stay_checkpoint(plan)
    dearest = max(checkpoint.options, key=lambda o: o.estCost or 0)
    apply_decision(plan, checkpoint.id, dearest.id, options_for(mem))

    # A decision is a stated preference, not a one-off override, so planning the
    # same brief again from the same memory keeps it.
    replanned = run_orchestrator(DEMO_BRIEF, options_for(mem))
    assert stay_checkpoint(replanned).selected == dearest.id


def test_a_confirmed_choice_outranks_a_budget_revision(plan, mem):
    """The traveller already decided; a cost cut must not quietly undo it."""
    checkpoint = stay_checkpoint(plan)
    dearest = max(checkpoint.options, key=lambda o: o.estCost or 0)
    after = apply_decision(plan, checkpoint.id, dearest.id, options_for(mem))

    # This plan went through budget revisions, which normally take the cheapest.
    assert after.round > 1
    detail = next(
        item.detail
        for item in next(s for s in after.sections if s.id == "accommodation").proposal.items
        if "Tokyo" in item.detail
    )
    assert dearest.id in detail


def test_an_option_the_checkpoint_never_offered_is_refused(plan, mem):
    checkpoint = stay_checkpoint(plan)
    with pytest.raises(ValueError, match="is not an option"):
        apply_decision(plan, checkpoint.id, "Somewhere Invented", options_for(mem))


def test_an_unknown_checkpoint_is_refused(plan, mem):
    with pytest.raises(KeyError):
        apply_decision(plan, "no-such-checkpoint", "x", options_for(mem))


def test_a_checkpoint_without_a_decision_is_refused(plan, mem):
    basics = next(c for c in plan.hitl if c.id == "confirm-brief")
    with pytest.raises(ValueError, match="carries no decision"):
        apply_decision(plan, basics.id, "x", options_for(mem))


def test_collect_decisions_only_reports_settled_ones(plan, mem):
    assert collect_decisions(plan) == {}
    checkpoint = stay_checkpoint(plan)
    after = apply_decision(plan, checkpoint.id, checkpoint.options[-1].id, options_for(mem))
    assert collect_decisions(after) == {stay_choice_key("Tokyo"): checkpoint.options[-1].id}
