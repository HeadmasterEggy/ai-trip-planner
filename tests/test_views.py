"""The cross-section views, and the negotiation record they read from."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import escape

import pytest

from trip_planner.contracts import AgentProposal, ProposalItem, TripSection
from trip_planner.decisions import apply_decision
from trip_planner.demo import DEMO_BRIEF, demo_brief
from trip_planner.memory import InMemoryStore
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.ui.render import (
    budget_breakdown,
    negotiation,
    negotiation_verdict,
    scheduled_days,
    step_states,
    steps,
    timeline,
)
from trip_planner.workflow import OrchestratorOptions, run_orchestrator


@pytest.fixture(scope="module")
def plan():
    return run_orchestrator(DEMO_BRIEF, OrchestratorOptions(specialists=ALL_SPECIALISTS))


def test_the_demo_never_starts_in_the_past():
    # A fixed date would quietly rot and greet every new visitor with a warning.
    assert demo_brief(date(2030, 1, 1)).dates[0] > "2030-01-01"
    assert DEMO_BRIEF.dates[0] > datetime.now(UTC).date().isoformat()


def test_every_round_is_recorded_with_its_conflicts(plan):
    assert plan.negotiation
    assert [r.round for r in plan.negotiation] == list(range(1, len(plan.negotiation) + 1))
    # The final round is the one the plan was built from.
    assert plan.negotiation[-1].round == plan.round


def test_the_record_says_who_was_actually_re_planned(plan):
    for entry in plan.negotiation[:-1]:
        # Every conflict in a non-final round should have been sent back.
        assert set(entry.revised) == {c.targetAgent for c in entry.conflicts}


def test_the_timeline_merges_specialists_onto_one_axis(plan):
    days = scheduled_days(plan)
    assert days
    owners = {agent for entries in days.values() for agent, _ in entries}
    # A day view that only ever showed one specialist would hide every clash.
    assert len(owners) > 1
    for entries in days.values():
        times = [item.startTime or "" for _, item in entries]
        assert times == sorted(times)


def test_the_timeline_survives_a_plan_with_nothing_scheduled():
    empty = run_orchestrator(
        DEMO_BRIEF, OrchestratorOptions(specialists=ALL_SPECIALISTS, max_rounds=1)
    ).model_copy(update={"sections": []})
    assert "No dated items yet" in timeline(empty)


def test_the_breakdown_ranks_sections_and_skips_free_ones(plan):
    html = budget_breakdown(plan)
    # Labels are escaped on the way out, so compare against the escaped form.
    for section in plan.sections:
        label = escape(section.label, quote=True)
        if section.estCost > 0:
            assert f'class="tp-bd__label">{label}<' in html
        else:
            assert f'class="tp-bd__label">{label}<' not in html

    # Ranked by cost, largest first.
    order = re.findall(r'class="tp-bd__label">([^<]+)<', html)
    expected = [
        escape(s.label, quote=True)
        for s in sorted(
            (s for s in plan.sections if s.estCost > 0), key=lambda s: s.estCost, reverse=True
        )
    ]
    assert order == expected


def test_a_converged_plan_reads_differently_from_a_stuck_one(plan):
    verdict, _ = negotiation_verdict(plan)
    assert verdict in {"converged", "stuck", "unresolved"}

    cleared = plan.model_copy(
        update={"negotiation": [plan.negotiation[0].model_copy(update={"conflicts": []})]}
    )
    assert negotiation_verdict(cleared)[0] == "converged"
    assert "no conflicts" in negotiation(cleared)


def test_a_repeated_conflict_is_called_out_as_stuck(plan):
    if len(plan.negotiation) < 2:
        pytest.skip("this plan converged; nothing to detect")
    reasons = {r.reason for entry in plan.negotiation for r in entry.conflicts}
    if len(reasons) == 1:
        assert negotiation_verdict(plan)[0] == "stuck"


def _without_conflicts(plan):
    """The same plan with no section marked needs_you.

    A conflict status outranks a settled choice, so it has to be taken out of
    the way for the choice-to-step join to be what the test measures.
    """
    return plan.model_copy(
        update={"sections": [s.model_copy(update={"status": "draft"}) for s in plan.sections]}
    )


def test_a_choice_only_marks_its_step_done_once_confirmed(plan):
    """A checkpoint and its section are joined only by the preference key.

    Matching the checkpoint id (`choose-stay-Kyoto`) against a section id never
    hit anything, so confirming a stay left the step looking untouched.
    """
    before = {row.sectionId: row.state for row in step_states(_without_conflicts(plan))}
    assert before["accommodation"] == "open"
    assert "tp-step__n--done" not in steps(_without_conflicts(plan))

    checkpoint = next(c for c in plan.hitl if c.type == "confirm_choice")
    settled = apply_decision(
        plan,
        checkpoint.id,
        checkpoint.selected,
        # A private store: a decision must not leak into another test's plan.
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore()),
    )

    after = {row.sectionId: row.state for row in step_states(_without_conflicts(settled))}
    assert after["accommodation"] == "done"
    assert after["itinerary"] == "open"
    assert "tp-step__n--done" in steps(_without_conflicts(settled))


def test_a_needs_you_section_outranks_a_settled_choice(plan):
    """An unresolved conflict is still the traveller's to act on, even if a
    choice elsewhere on the plan has been confirmed."""
    conflicting = next(s.id for s in plan.sections if s.status == "needs_you")
    rows = {row.sectionId: row.state for row in step_states(plan)}
    assert rows[conflicting] == "todo"


def test_every_specialist_is_one_numbered_step(plan):
    rows = step_states(plan)
    assert [row.sectionId for row in rows] == [s.id for s in plan.sections]
    assert steps(plan).count('class="tp-step"') == len(plan.sections)


def test_views_escape_their_input():
    section = TripSection(
        id="itinerary",
        label="Day plan",
        summary="s",
        status="draft",
        estCost=10,
        proposal=AgentProposal(
            agent="itinerary",
            summary="s",
            items=[
                ProposalItem(
                    kind="activity",
                    detail="<img src=x onerror=alert(1)>",
                    day=1,
                    startTime="09:00",
                    endTime="10:00",
                    location="<script>",
                    estCost=10,
                )
            ],
            assumptions=[],
        ),
    )
    fake = run_orchestrator(
        DEMO_BRIEF, OrchestratorOptions(specialists=ALL_SPECIALISTS, max_rounds=1)
    ).model_copy(update={"sections": [section]})
    html = timeline(fake)
    # The payload may survive as text; what must not survive is a real tag.
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


def test_the_timeline_names_the_owner_as_well_as_colouring_it(plan):
    """Colour alone is unreadable in print and to some readers."""
    html = timeline(plan)
    labels = {section.id: section.label for section in plan.sections}
    owners = set(re.findall(r'class="tp-tl__owner">([^<]+)<', html))
    assert owners, "no owner labels rendered"
    assert owners <= {escape(label, quote=True) for label in labels.values()}
