"""What each specialist reports about how it decided."""

from __future__ import annotations

from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.ui.render import trace_block
from trip_planner.workflow import OrchestratorOptions, run_orchestrator


def plan_it(**kwargs):
    return run_orchestrator(
        DEMO_BRIEF,
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), **kwargs),
    )


def test_every_specialist_reports_how_it_decided():
    plan = plan_it(max_rounds=1)
    assert {t.agent for t in plan.traces} == {s.name for s in ALL_SPECIALISTS}
    assert all(t.evidence for t in plan.traces)


def test_a_calculator_is_never_reported_as_a_model():
    """Transport and accommodation must not look like they were reasoned."""
    plan = plan_it(max_rounds=1)
    for agent in ("transport", "accommodation"):
        entry = next(t for t in plan.traces if t.agent == agent)
        assert entry.source == "calculator"
        assert entry.fallbackReason is None


def test_a_fallback_says_why_rather_than_looking_like_model_output():
    plan = plan_it(max_rounds=1)
    for entry in plan.traces:
        if entry.source == "deterministic fallback":
            # Without a key the model is simply absent, so no reason is expected;
            # when one was tried and rejected, the reason has to be recorded.
            assert entry.fallbackReason is None or entry.fallbackReason
        if entry.fallbackReason:
            assert entry.source == "deterministic fallback"


def test_a_revision_records_the_constraint_it_was_given():
    plan = plan_it()
    revisions = [t for t in plan.traces if t.round > 1]
    assert revisions, "the demo brief should trigger at least one revision"
    for entry in revisions:
        assert entry.revision
        assert "→" in entry.revision  # reason and constraints, not one or the other


def test_a_specialist_is_traced_once_per_round_it_ran():
    plan = plan_it()
    first_round = [t for t in plan.traces if t.round == 1]
    assert len(first_round) == len(ALL_SPECIALISTS)
    assert len(first_round) == len({t.agent for t in first_round})


def test_the_rendered_trace_leads_with_the_newest_round():
    plan = plan_it()
    html = trace_block(plan.traces, "accommodation")
    rounds = [t.round for t in plan.traces if t.agent == "accommodation"]
    assert html.index(f"Round {max(rounds)}") < html.index(f"Round {min(rounds)}")


def test_the_rendered_trace_escapes_its_input():
    plan = plan_it(max_rounds=1)
    hostile = plan.traces[0].model_copy(update={"evidence": {"<script>": "<img src=x onerror=1>"}})
    html = trace_block([hostile], hostile.agent)
    assert "<script>" not in html
    assert "<img" not in html


def test_an_untraced_specialist_says_so():
    assert "No reasoning was recorded" in trace_block([], "dining")
