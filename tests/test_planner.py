"""Behaviour tests for the planning layer.

These run without a model key or network: every specialist falls back to
deterministic output, which is exactly the path a reviewer or CI hits.
"""

from __future__ import annotations

import logging
import threading

import pytest

from trip_planner import workflow as workflow_module
from trip_planner.budget import assess_budget, cost_of, sum_usd
from trip_planner.contracts import (
    AgentProposal,
    ProposalItem,
    SpecialistTrace,
    TripBrief,
    brief_problem,
)
from trip_planner.memory import InMemoryStore
from trip_planner.ports import AgentContext, Place, RouteLeg, ToolGateway
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.specialists.accommodation import split_stay
from trip_planner.specialists.base import FunctionSpecialist
from trip_planner.specialists.itinerary import ACTIVITY_BUDGET_SHARE, DEFAULT_ACTIVITY_COST_USD
from trip_planner.tools import create_tool_gateway
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
    # The specialists now run concurrently, so trace order must not depend on who
    # finishes first: the node collects in registration order.
    assert [t.agent for t in first.traces] == [t.agent for t in second.traces]

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


def test_worker_results_live_in_graph_state(brief, monkeypatch):
    """Traces and candidates are state, not a dict the nodes passed around.

    This is what item 2.2 buys: a checkpoint (or Studio, or `get_state`) can show
    which specialist fell back and which candidates were weighed up, because the
    data is in a channel rather than in a closure.
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    final: dict = {}
    # A single stream mode yields payloads directly, not (mode, payload) pairs.
    for payload in workflow_module._GRAPH.stream(
        {"brief": brief},
        context=workflow_module._run_context(
            OrchestratorOptions(specialists=ALL_SPECIALISTS, max_rounds=1)
        ),
        # The graph is checkpointed now, so a run needs a thread to write to.
        config=workflow_module._thread_config("state-channel-test"),
        stream_mode="values",
    ):
        final = payload

    assert len(final["traces"]) == len(ALL_SPECIALISTS)
    assert {t.agent for t in final["traces"]} == {s.name for s in ALL_SPECIALISTS}
    assert final["stay_choices"]  # the accommodation candidates the traveller can pick
    assert "plan" in final


def test_a_specialist_sees_only_its_own_extras(brief):
    """`extras` is per invocation, so nothing has to be diffed out of it.

    A run-long accumulator was the side channel: it made "what did this
    specialist report" a subtraction rather than a value.
    """
    seen: dict[str, dict] = {}

    def watch(name: str):
        def run(brief_, context, revision=None) -> AgentProposal:
            seen[name] = dict(context.extras)  # empty on entry, every time
            context.extras.setdefault("traces", []).append(
                SpecialistTrace(agent=name, round=1, source="calculator")
            )
            return AgentProposal(agent=name, summary="s", items=[], assumptions=["stub"])

        return run

    specialists = [FunctionSpecialist(s.name, s.label, watch(s.name)) for s in ALL_SPECIALISTS]
    plan = run_orchestrator(brief, OrchestratorOptions(specialists=specialists, max_rounds=1))

    assert all(entry == {} for entry in seen.values())
    assert {t.agent for t in plan.traces} == {s.name for s in ALL_SPECIALISTS}


def test_the_deterministic_dispatch_runs_specialists_concurrently(brief):
    """Without a key this is the path every deployment runs, so its latency is the
    plan's. The specialists are independent, so they overlap.

    A barrier, not a stopwatch: it can only be passed if they are inside their work
    at the same time, so it also fails loudly rather than flakily.
    """
    barrier = threading.Barrier(len(ALL_SPECIALISTS), timeout=5)

    def wait(name: str):
        def run(brief_, context, revision=None) -> AgentProposal:
            barrier.wait()
            return AgentProposal(agent=name, summary="s", items=[], assumptions=["stub"])

        return run

    specialists = [FunctionSpecialist(s.name, s.label, wait(s.name)) for s in ALL_SPECIALISTS]
    plan = run_orchestrator(brief, OrchestratorOptions(specialists=specialists, max_rounds=1))

    assert {s.id for s in plan.sections} == {s.name for s in ALL_SPECIALISTS}


def test_progress_is_always_written_on_the_node_thread(brief, monkeypatch):
    """The workers are pooled; the reporting is not.

    A stream writer resolves its config from a context variable that does not cross
    threads, so a worker that reported its own progress would raise. That is why
    the node writes `agent_started` up front and `agent_completed` as each future
    resolves.
    """
    threads: list[str] = []
    real = workflow_module._write_progress

    def watch(event) -> None:
        threads.append(threading.current_thread().name)
        real(event)

    monkeypatch.setattr(workflow_module, "_write_progress", watch)
    run_orchestrator(brief, OrchestratorOptions(specialists=ALL_SPECIALISTS, max_rounds=1))

    assert threads, "the run should report something"
    assert set(threads) == {"MainThread"}


def _tight(brief):
    """A brief whose plan must escalate: far over the 10% red line."""
    return brief.model_copy(update={"budgetTotal": 1500})


def test_a_finished_run_leaves_no_checkpoint(brief):
    """The saver is in-process, so a run that needed no answer drops its thread."""
    monkeypatch_free = brief.model_copy(update={"budgetTotal": 100_000})
    stream = run_orchestrator_stream(monkeypatch_free, OrchestratorOptions(max_rounds=1))
    list(stream)

    assert stream.interrupt is None
    assert (
        workflow_module._GRAPH.get_state(workflow_module._thread_config(stream.thread_id)).values
        == {}
    )


def test_an_escalation_pauses_with_the_plan_already_built(brief):
    """The pause comes after `build_plan`, so the traveller answers with the plan in
    front of them -- and the thread survives so the answer can resume it."""
    stream = run_orchestrator_stream(_tight(brief), OrchestratorOptions(max_rounds=1))
    list(stream)

    assert stream.plan is not None  # the plan the traveller is deciding about
    assert stream.interrupt is not None
    assert stream.interrupt["kind"] == "escalation"
    assert [option["value"] for option in stream.interrupt["options"]] == ["accept"]
    # No rounds left, so "revise" is not offered: the only honest answer is to keep it.
    state = workflow_module._GRAPH.get_state(workflow_module._thread_config(stream.thread_id))
    assert state.values["plan"].tripId == brief.tripId


def test_forgetting_a_paused_thread_drops_its_checkpoint(brief):
    """A pause that is superseded must not leave a thread behind.

    The saver is in-process, so a traveller who asks something else rather than
    answering would otherwise accumulate threads nobody resumes.
    """
    stream = run_orchestrator_stream(_tight(brief), OrchestratorOptions(max_rounds=1))
    list(stream)
    assert stream.interrupt is not None
    thread = workflow_module._thread_config(stream.thread_id)
    assert workflow_module._GRAPH.get_state(thread).values  # there to be resumed

    workflow_module.forget_thread(stream.thread_id)

    assert workflow_module._GRAPH.get_state(thread).values == {}


def test_resuming_with_accept_marks_the_escalation_answered(brief):
    stream = run_orchestrator_stream(_tight(brief), OrchestratorOptions(max_rounds=1))
    list(stream)
    assert stream.interrupt is not None

    resumed = run_orchestrator_stream(
        _tight(brief),
        OrchestratorOptions(max_rounds=1),
        resume="accept",
        thread_id=stream.thread_id,
    )
    list(resumed)

    assert resumed.interrupt is None
    escalation = next(h for h in resumed.plan.hitl if h.type == "escalation")
    assert escalation.status == "approved"
    assert resumed.plan.overrunPct > 10  # still over: the bar shows it, the stop asking


def test_the_pause_never_offers_a_revision_that_cannot_help(brief):
    """Why the answer set is just "keep it".

    The pause is reached only when another round is impossible or known to be
    futile: `route_after_detection` sends the run back to `revise_conflicts`
    whenever conflicts remain and there is room, and stops when there is no room,
    nothing conflicted, or the last revision moved no money. So a "revise" button
    here would do nothing -- which is a finding, not an omission.
    """
    options = OrchestratorOptions(max_rounds=3)
    stream = run_orchestrator_stream(_tight(brief), options)
    list(stream)

    assert stream.interrupt is not None
    assert [option["value"] for option in stream.interrupt["options"]] == ["accept"]

    plan = stream.plan
    assert plan is not None
    negotiated = len(plan.negotiation) >= 2  # it did use the rounds it had
    assert negotiated
    no_room = plan.round >= plan.negotiation[0].round + options.max_rounds - 1
    stalled = any(entry.stalled for entry in plan.negotiation)
    unresolved = bool(plan.negotiation[-1].conflicts)
    assert stalled or not unresolved or no_room


def test_a_degraded_path_is_logged_rather_than_printed(brief, caplog):
    """`print` cannot be levelled, filtered or captured; a logger can.

    Every offline run takes the degraded path, so this is the diagnostic a deployment
    actually sees.
    """
    with caplog.at_level(logging.WARNING):
        run_orchestrator(brief, OrchestratorOptions(max_rounds=1))

    assert any("Delegation unavailable" in record.getMessage() for record in caplog.records), (
        caplog.text
    )


def test_booking_is_always_the_fixture(monkeypatch):
    """There is no live booking provider; the roadmap says one has to exist first."""
    monkeypatch.setenv("USE_MOCK_TOOLS", "false")

    gateway = create_tool_gateway()

    assert isinstance(gateway.booking, MockBooking)
    assert isinstance(gateway.maps, MapsAdapter)
