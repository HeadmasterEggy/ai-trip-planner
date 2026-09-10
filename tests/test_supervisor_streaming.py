"""Progress as a stream, and the boundary that keeps Streamlit out of the planner.

`on_progress` used to hand a UI callback to the specialists, which ran it on
whatever thread they happened to be on -- a thread pool, under the supervisor.
The fix for that (`ui/live.py`) is gone: progress now travels on the graph's
custom stream, the consumer iterates it on its own thread, and no module in the
package imports Streamlit. These tests pin both halves.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from scripted import ScriptedChatModel, tool_call, tool_calls

from trip_planner import supervisor as supervisor_module
from trip_planner.contracts import AgentProposal, ProgressEvent
from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.ports import AgentContext, ToolGateway
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.specialists.base import FunctionSpecialist
from trip_planner.tools.booking import MockBooking
from trip_planner.tools.maps import MapsAdapter
from trip_planner.workflow import (
    OrchestratorOptions,
    run_orchestrator,
    run_orchestrator_stream,
)

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "trip_planner"


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(
        tripId="t1",
        round=1,
        tools=ToolGateway(maps=MapsAdapter(), booking=MockBooking()),
        mem=InMemoryStore(),
    )


def test_no_planning_module_imports_streamlit():
    """The invariant that replaced `ui/live.py`.

    A UI framework reaching into the planner is what made a worker thread write a
    widget, and the shim that repaired it existed only to work around the
    coupling. The presentation layer is the entry point and `ui/`'s pure strings;
    nothing else may import Streamlit.
    """
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] == "streamlit" for name in names):
                offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")

    assert offenders == []


def test_the_deterministic_path_reports_each_specialist(monkeypatch):
    """With no model, `dispatch` runs the specialists in its own node."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    stream = run_orchestrator_stream(DEMO_BRIEF, OrchestratorOptions(max_rounds=1))
    events = list(stream)

    assert stream.plan is not None
    for specialist in ALL_SPECIALISTS:
        kinds = [e.type for e in events if e.agent == specialist.name]
        assert kinds == ["agent_started", "agent_completed"]
    assert all(isinstance(e, ProgressEvent) for e in events)


def test_the_supervisor_path_reports_from_the_consumer_thread(monkeypatch, ctx):
    """The regression that started this: the tools run on a thread pool.

    Every event must reach the consumer on the thread that is iterating, because
    that is where a UI writes. Before, the callback ran on
    `ThreadPoolExecutor-*` and took the delegation down with it.
    """
    model = ScriptedChatModel(
        responses=[
            tool_calls("ask_itinerary_specialist", "ask_transport_specialist"),
            AIMessage(content="Done."),
        ]
    )
    monkeypatch.setattr(supervisor_module, "create_routed_chat_model", lambda task: model)

    forwarded: list[ProgressEvent] = []
    consumer_thread = threading.current_thread().name

    def forward(event: ProgressEvent) -> None:
        forwarded.append(event)
        assert threading.current_thread().name == consumer_thread

    outcome = supervisor_module.dispatch_with_supervisor(ALL_SPECIALISTS, DEMO_BRIEF, ctx, forward)

    assert [p.agent for p in outcome.proposals] == ["itinerary", "transport"]
    # The two tools run in parallel, so only each specialist's own pair is ordered.
    by_agent: dict[str, list[str]] = {}
    for event in forwarded:
        by_agent.setdefault(event.agent, []).append(event.type)
    assert by_agent == {
        "itinerary": ["agent_started", "agent_completed"],
        "transport": ["agent_started", "agent_completed"],
    }


def test_a_failing_specialist_is_reported_as_failed(monkeypatch, ctx):
    """`agent_failed` still carries the specialist's error, from the tool."""
    from trip_planner.specialists.base import FunctionSpecialist

    def boom(brief, context, revision=None):
        raise ValueError("no stay available")

    specialists = [
        FunctionSpecialist("accommodation", "Stay", boom) if s.name == "accommodation" else s
        for s in ALL_SPECIALISTS
    ]
    model = ScriptedChatModel(
        responses=[tool_call("ask_accommodation_specialist"), AIMessage(content="Noted.")]
    )
    monkeypatch.setattr(supervisor_module, "create_routed_chat_model", lambda task: model)

    events: list[ProgressEvent] = []
    # Nothing was collected, so the caller falls back -- but the failure itself is
    # reported, with the specialist's own message.
    with pytest.raises(RuntimeError, match="without delegating"):
        supervisor_module.dispatch_with_supervisor(specialists, DEMO_BRIEF, ctx, events.append)

    assert [(e.agent, e.type) for e in events] == [
        ("accommodation", "agent_started"),
        ("accommodation", "agent_failed"),
    ]
    assert "no stay available" in (events[-1].error or "")


def test_a_run_without_a_stream_still_returns_a_plan():
    """`run_orchestrator` invokes rather than streams: the writer no-ops."""
    plan = run_orchestrator(DEMO_BRIEF, OrchestratorOptions(max_rounds=1))
    assert plan.sections


def test_a_stream_refuses_to_be_consumed_twice():
    """Iterating again would run five specialists and three rounds a second time."""
    stream = run_orchestrator_stream(DEMO_BRIEF, OrchestratorOptions(max_rounds=1))
    list(stream)

    with pytest.raises(RuntimeError, match="consumed once"):
        list(stream)


def test_the_supervisor_agent_is_built_once(monkeypatch, ctx):
    """Static tools plus a runtime context mean there is nothing to rebuild.

    `create_agent` used to run on every dispatch and every revision round, only
    because the tools were closures over one run.
    """
    model = ScriptedChatModel(
        responses=[
            tool_call("ask_itinerary_specialist"),
            AIMessage(content="done"),
            tool_call("ask_itinerary_specialist"),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(supervisor_module, "create_routed_chat_model", lambda task: model)

    built = []
    real = supervisor_module.create_agent

    def counting(*args, **kwargs):
        built.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(supervisor_module, "create_agent", counting)

    supervisor_module.dispatch_with_supervisor(ALL_SPECIALISTS, DEMO_BRIEF, ctx, lambda e: None)
    supervisor_module.dispatch_with_supervisor(ALL_SPECIALISTS, DEMO_BRIEF, ctx, lambda e: None)

    assert len(built) == 1  # built on first use, then reused
    assert model.index == 4  # ...and the second run really did run


def test_each_run_uses_its_own_specialists(monkeypatch, ctx):
    """The tool resolves its specialist from the run, not from a closure.

    The tools are shared for the process now, so this is the property that keeps
    a run's injected specialists from leaking into the next one.
    """
    used: list[str] = []

    def tagged(tag: str) -> list:
        def run(brief, context, revision=None) -> AgentProposal:
            used.append(tag)
            return AgentProposal(agent="itinerary", summary=tag, items=[], assumptions=["stub"])

        # A stub specialist cannot raise for pricing; only the tag matters here.
        return [
            FunctionSpecialist("itinerary", "Day plan", run) if s.name == "itinerary" else s
            for s in ALL_SPECIALISTS
        ]

    model = ScriptedChatModel(
        responses=[
            tool_call("ask_itinerary_specialist"),
            AIMessage(content="done"),
            tool_call("ask_itinerary_specialist"),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(supervisor_module, "create_routed_chat_model", lambda task: model)

    first = supervisor_module.dispatch_with_supervisor(
        tagged("first"), DEMO_BRIEF, ctx, lambda e: None
    )
    second = supervisor_module.dispatch_with_supervisor(
        tagged("second"), DEMO_BRIEF, ctx, lambda e: None
    )

    assert used == ["first", "second"]
    assert [p.summary for p in first.proposals] == ["first"]
    assert [p.summary for p in second.proposals] == ["second"]


def test_a_repeated_tool_call_is_deduped_but_every_run_is_reported(monkeypatch, ctx):
    """Arrival order is not stable, so the caller restores order and dedupes.

    The nested agent's state appends under a reducer -- parallel calls land as
    they finish, and a model may ask for the same specialist twice -- while the
    outcome keeps one proposal per specialist so the plan has one section each.
    """
    model = ScriptedChatModel(
        responses=[
            tool_calls("ask_itinerary_specialist", "ask_itinerary_specialist"),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(supervisor_module, "create_routed_chat_model", lambda task: model)

    outcome = supervisor_module.dispatch_with_supervisor(
        ALL_SPECIALISTS, DEMO_BRIEF, ctx, lambda event: None
    )

    assert [p.agent for p in outcome.proposals] == ["itinerary"]  # one section, not two
    assert len(outcome.traces) == 2  # ...but both runs were reported
