"""Model routing: what runs on what, and who is allowed to ask.

`MODEL_ROUTING` describes *roles*, not specialists -- two of the five specialists
never call a model, and the supervisor, brief extraction and reply generation are
not specialists at all -- so the map is asserted rather than trusted. A wrong key
is silent by design: an unknown role resolves to the default provider instead of
raising, so nothing else would catch it.
"""

from __future__ import annotations

import pytest

from trip_planner.chat import BriefPatch, create_model_extractor, run_trip_chat
from trip_planner.contracts import ChatRequest, RevisionRequest
from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.models import MODEL_ROUTING, describe_route
from trip_planner.ports import AgentContext, ToolGateway
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.supervisor import dispatch_with_supervisor, revise_with_supervisor
from trip_planner.tools.booking import MockBooking
from trip_planner.tools.maps import MapsAdapter
from trip_planner.workflow import OrchestratorOptions

# Every role the map is expected to carry: one generation role per model-backed
# specialist, plus the three that are not specialists.
EXPECTED_ROLES = {
    "itinerary",
    "destination-guide",
    "dining",
    "supervisor",
    "brief-extraction",
    "reply",
}


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(
        tripId="t1",
        round=1,
        tools=ToolGateway(maps=MapsAdapter(), booking=MockBooking()),
        mem=InMemoryStore(),
    )


@pytest.fixture
def asked(monkeypatch) -> list[str]:
    """Record every route a role asks for, and answer "no model configured".

    Each module imported the factory by name, so the spy has to be installed in
    each namespace rather than only on `models`.
    """
    tasks: list[str] = []

    def spy(task: str):
        tasks.append(task)  # falling through answers None: "no model configured"

    for module in ("models", "supervisor", "chat"):
        monkeypatch.setattr(f"trip_planner.{module}.create_routed_chat_model", spy)
    return tasks


def test_the_routing_map_describes_roles_not_specialists():
    assert set(MODEL_ROUTING) == EXPECTED_ROLES
    # Transport and accommodation are calculators. A route for either would do
    # nothing but tell the sidebar they run on a model.
    assert "transport" not in MODEL_ROUTING
    assert "accommodation" not in MODEL_ROUTING


def test_every_route_names_a_provider_the_factory_handles():
    """Anything that is not "deepseek" takes the MiniMax branch, so a typo would
    silently send a role to the wrong account system rather than fail."""
    assert set(MODEL_ROUTING.values()) <= {"deepseek", "minimax"}


def test_only_the_model_backed_specialists_ask_for_a_route(asked, ctx):
    for specialist in ALL_SPECIALISTS:
        specialist.invoke(DEMO_BRIEF, ctx)

    assert set(asked) == {"itinerary", "destination-guide", "dining"}
    assert set(asked) <= set(MODEL_ROUTING)


def test_the_supervisor_asks_for_its_own_route(asked, ctx):
    """Both supervisor loops used to borrow the itinerary route."""
    request = RevisionRequest(
        tripId="t1", targetAgent="accommodation", reason="over budget", constraints=["cut 30%"]
    )

    with pytest.raises(RuntimeError, match="requires a configured routed chat model"):
        dispatch_with_supervisor(ALL_SPECIALISTS, DEMO_BRIEF, ctx, lambda *a: None)
    with pytest.raises(RuntimeError, match="requires a configured routed chat model"):
        revise_with_supervisor(ALL_SPECIALISTS, [], [request], DEMO_BRIEF, ctx, lambda *a: None)

    assert asked == ["supervisor", "supervisor"]


def test_brief_extraction_asks_for_its_own_route(asked):
    """Extraction is a small classification, not day planning."""
    assert create_model_extractor() is None
    assert asked == ["brief-extraction"]


def test_a_chat_turn_requests_the_reply_route(asked):
    class FixedExtractor:
        def extract(self, message: str, current: object) -> BriefPatch:
            return BriefPatch()

    run_trip_chat(
        ChatRequest(tripId="t1", message="hello", brief=DEMO_BRIEF),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        extractor=FixedExtractor(),
    )

    assert "brief-extraction" not in asked  # the injected extractor was used
    assert "reply" in asked


def test_the_route_description_follows_the_credentials(monkeypatch):
    """`unconfigured` is a real answer: that path falls back to deterministic output."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    assert describe_route("itinerary") == "unconfigured"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    assert describe_route("itinerary") == "deepseek:deepseek-v4-flash"
