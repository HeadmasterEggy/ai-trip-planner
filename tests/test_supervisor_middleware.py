"""Resilience of the supervisor's own loop.

These drive the real `dispatch_with_supervisor` with a scripted chat model as the
supervisor's route, so the middleware is exercised the way a run exercises it --
no provider key, no network, no mocked `create_agent`.

What each test would have done before the middleware:

- a transient provider failure propagated out of `dispatch_with_supervisor`, so
  the caller fell back to deterministic dispatch for the whole plan;
- a failing specialist tool took the rest of the fan-out with it;
- a model that never stopped delegating ran to LangGraph's recursion limit.
"""

from __future__ import annotations

import pytest
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.messages import AIMessage
from scripted import ScriptedChatModel, tool_call

from trip_planner import supervisor as supervisor_module
from trip_planner.contracts import AgentProposal, RevisionRequest
from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.ports import AgentContext, ToolGateway
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.specialists.base import FunctionSpecialist
from trip_planner.supervisor import (
    MODEL_CALL_LIMIT,
    dispatch_with_supervisor,
    revise_with_supervisor,
    supervisor_middleware,
)
from trip_planner.tools.booking import MockBooking
from trip_planner.tools.maps import MapsAdapter


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(
        tripId="t1",
        round=1,
        tools=ToolGateway(maps=MapsAdapter(), booking=MockBooking()),
        mem=InMemoryStore(),
    )


@pytest.fixture
def scripted(monkeypatch):
    """Install a scripted model as the supervisor's route."""

    def install(responses: list) -> ScriptedChatModel:
        model = ScriptedChatModel(responses=responses)
        monkeypatch.setattr(supervisor_module, "create_routed_chat_model", lambda task: model)
        return model

    return install


def progress(*args) -> None:
    """The supervisor reports progress; these tests do not assert on it."""


def test_a_transient_model_failure_is_retried_rather_than_fallen_back(scripted, ctx):
    model = scripted(
        [
            ConnectionError("provider hiccup"),
            tool_call("ask_itinerary_specialist"),
            AIMessage(content="All done."),
        ]
    )

    outcome = dispatch_with_supervisor(ALL_SPECIALISTS, DEMO_BRIEF, ctx, progress)

    assert [p.agent for p in outcome.proposals] == ["itinerary"]
    # One failure, then the run continued: the retry cost a call, not the plan.
    assert model.index == 3


def test_a_failing_specialist_does_not_take_the_others_with_it(scripted, ctx):
    def boom(brief, context, revision=None) -> AgentProposal:
        raise ValueError("SECRET-INTERNAL-DETAIL")

    specialists = [
        FunctionSpecialist("transport", "Getting around", boom) if s.name == "transport" else s
        for s in ALL_SPECIALISTS
    ]
    model = scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_itinerary_specialist",
                        "args": {},
                        "id": "c1",
                        "type": "tool_call",
                    },
                    {
                        "name": "ask_transport_specialist",
                        "args": {},
                        "id": "c2",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="Carrying on without that section."),
        ]
    )

    outcome = dispatch_with_supervisor(specialists, DEMO_BRIEF, ctx, progress)

    assert [p.agent for p in outcome.proposals] == ["itinerary"]  # the batch survived
    delivered = [m for m in model.seen[1] if m.type == "tool"]
    failure = next(m for m in delivered if m.status == "error")
    assert "ValueError" in failure.content  # the model is told what happened
    assert "SECRET-INTERNAL-DETAIL" not in failure.content  # but not the detail


def test_the_supervisor_loop_is_bounded(scripted, ctx):
    """A model that never stops delegating stops anyway, well below the
    recursion limit that used to be the only bound."""
    model = scripted([tool_call("ask_itinerary_specialist")])

    outcome = dispatch_with_supervisor(ALL_SPECIALISTS, DEMO_BRIEF, ctx, progress)

    assert outcome.proposals
    assert model.index == MODEL_CALL_LIMIT


def test_the_revision_loop_carries_the_same_middleware(scripted, ctx):
    """The revision supervisor is a second agent, and had the same exposure."""
    model = scripted([AIMessage(content="Nothing to route.")])
    request = RevisionRequest(
        tripId="t1", targetAgent="accommodation", reason="over budget", constraints=["cut 30%"]
    )

    with pytest.raises(RuntimeError, match="delegated 0 of 1"):
        revise_with_supervisor(ALL_SPECIALISTS, [], [request], DEMO_BRIEF, ctx, progress)
    assert model.index == 1


def test_a_deterministic_specialist_failure_is_not_retried(scripted, ctx):
    """A ValueError is an answer, not a hiccup.

    Retrying "no eligible stay" under the supervisor would spend model calls to
    get the same failure back, so the tool retry is restricted to failures that
    might not repeat.
    """
    attempts = []

    def boom(brief, context, revision=None) -> AgentProposal:
        attempts.append(1)
        raise ValueError("no eligible stay")

    specialists = [
        FunctionSpecialist("accommodation", "Stay", boom) if s.name == "accommodation" else s
        for s in ALL_SPECIALISTS
    ]
    scripted([tool_call("ask_accommodation_specialist"), AIMessage(content="Noted.")])

    with pytest.raises(RuntimeError, match="without delegating"):
        dispatch_with_supervisor(specialists, DEMO_BRIEF, ctx, lambda event: None)

    assert len(attempts) == 1


def test_the_error_handler_is_outside_the_retry():
    """Order is semantic: the first entry is the outermost wrapper.

    Swapped, the handler would convert a failure before the retry could observe
    an exception, and the retry would be dead code.
    """
    kinds = [type(entry) for entry in supervisor_middleware(tool_count=5)]

    assert kinds[0] is ToolErrorMiddleware
    assert kinds.index(ToolRetryMiddleware) < kinds.index(ToolCallLimitMiddleware)
    assert kinds.index(ModelRetryMiddleware) < kinds.index(ModelCallLimitMiddleware)
    assert ToolErrorMiddleware in kinds and ModelCallLimitMiddleware in kinds
