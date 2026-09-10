"""The path no other test covers: a real provider.

Every other test runs offline or against a scripted model. A scripted model agrees
with any prompt and any schema, so it cannot tell whether the provider still does --
which is why three of this project's defects lived here: progress rows frozen only
with a key configured, a supervisor that had never actually run, and a retry that
re-ran deterministic failures.

Opt-in, so the default run stays offline and free:

    DEEPSEEK_API_KEY=... RUN_LIVE_TESTS=1 uv run pytest -m live

Deliberately cheap: a handful of calls, no full five-specialist fan-out.
"""

from __future__ import annotations

import os

import pytest

from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.models import create_structured_invoker
from trip_planner.ports import AgentContext, ToolGateway
from trip_planner.specialists.itinerary import ItineraryDraft
from trip_planner.specialists.transport import transport_specialist
from trip_planner.supervisor import dispatch_with_supervisor
from trip_planner.tools.booking import MockBooking
from trip_planner.tools.maps import MapsAdapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_TESTS") != "1" or not os.getenv("DEEPSEEK_API_KEY"),
        reason="live provider call: opt in with RUN_LIVE_TESTS=1 and a key",
    ),
]


def test_structured_output_still_satisfies_the_schema():
    """The provider quirks `models.py` documents are only real if they still hold.

    DeepSeek rejects the json_schema response format and rejects a forced
    tool_choice in thinking mode, so structured output goes through tool calling
    with thinking disabled. This is the cheapest call that proves the whole
    arrangement still works: credentials, model id, that `extra_body`, and the
    schema.
    """
    invoke = create_structured_invoker("itinerary", ItineraryDraft, "ItineraryDraft")
    assert invoke is not None, "expected a configured provider"

    draft = invoke(
        "Return one activity for a trip to Kyoto on day 1, 09:00 to 11:00, "
        "location 'Kinkaku-ji', cost 10 USD, under 200 characters."
    )

    assert isinstance(draft, ItineraryDraft)
    assert draft.summary
    assert len(draft.activities) == 1
    activity = draft.activities[0]
    assert activity.day == 1
    assert activity.location
    assert activity.startTime < activity.endTime  # HH:MM ordering the schema demands


def test_the_supervisor_still_calls_a_zero_argument_tool():
    """Item 1.2 removed the delegation argument, leaving tools the model calls with
    empty arguments. A real model is the only thing that can say whether that is
    still callable, and this is where a silently dead supervisor hid before.
    """
    ctx = AgentContext(
        tripId="live",
        round=1,
        tools=ToolGateway(maps=MapsAdapter(), booking=MockBooking()),
        mem=InMemoryStore(),
    )
    events: list = []

    outcome = dispatch_with_supervisor([transport_specialist], DEMO_BRIEF, ctx, events.append)

    assert outcome.proposals, "the supervisor delegated to nobody"
    assert [p.agent for p in outcome.proposals] == ["transport"]
    started = [e for e in events if e.type == "agent_started"]
    assert started, "the delegation reported no progress"
