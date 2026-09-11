"""The Streamlit entry point, driven headlessly.

`streamlit_app.py` is ~600 lines and had no test, which is how a dead join
(`section.id in checkpoint_id`, fixed in the first wave) survived in it. `AppTest`
runs the real script, so this covers the wiring the module tests cannot: the
opening screen, the rail and its conversation list, the plan rail that arrives
with the first plan, the pause for an escalation, and the session state that
carries the paused thread.

Kept offline by blanking the credentials before the script runs: `load_dotenv` does
not overwrite variables that already exist, so an empty value keeps the run on the
deterministic path.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from trip_planner.chat import fallback_question_for
from trip_planner.contracts import FIELD_NAMES, BriefPatch, missing_fields
from trip_planner.demo import TRIP_LENGTH_DAYS, TRIP_START_OFFSET_DAYS
from trip_planner.workflow import ESCALATION_OVERRUN_PCT

APP = Path(__file__).resolve().parent.parent / "streamlit_app.py"
TIMEOUT = 120


@pytest.fixture
def offline(monkeypatch):
    for key in ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "LANGSMITH_TRACING"):
        monkeypatch.setenv(key, "")


def demo_dates() -> tuple[str, str]:
    start = datetime.now(UTC).date() + timedelta(days=TRIP_START_OFFSET_DAYS)
    return start.isoformat(), (start + timedelta(days=TRIP_LENGTH_DAYS)).isoformat()


def one_message_trip() -> str:
    start, end = demo_dates()
    return f"Tokyo, {start} to {end}, 2 people, budget $20000"


def rail_text(at: AppTest) -> str:
    """Everything the rail draws as HTML, for asserting on rows and sections."""
    return " ".join(element.value for element in at.sidebar.markdown)


def test_the_app_opens_on_a_greeting_and_nothing_else(offline):
    """The first screen assumes nothing: a greeting and a chat box.

    It used to open on `demo_brief()` -- Tokyo & Kyoto, seven days, $4,000, already
    filled in -- so a visitor's first act was to delete someone else's trip. Both
    rails wait: the plan rail because there is no plan to read, and the nav rail
    because there is nothing to navigate to yet.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()

    assert not at.exception
    assert at.session_state["plan"] is None
    assert at.session_state["draft"] == BriefPatch()
    assert at.session_state["messages"] == []
    assert at.session_state["history"] == []
    assert at.session_state["pending_escalation"] is None
    assert any("tp-hero" in element.value for element in at.markdown)

    # No example chips, no plan toggle, no rail: no buttons at all.
    assert at.button == []
    assert at.sidebar.markdown == []
    assert at.sidebar.button == []
    assert at.chat_input[0].placeholder == "Where would you like to go?"


def test_the_opening_message_is_read_as_the_destination(offline):
    """The whole opening flow: say where, and the planner asks for the rest.

    "Tokyo" matches none of the extraction patterns, so before the draft existed
    this turn planned the demo trip instead of the traveller's.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value("Tokyo").run()

    assert not at.exception
    assert at.session_state["plan"] is None
    assert at.session_state["draft"].destination == "Tokyo"
    assert len(at.session_state["messages"]) == 2
    # Offline, so the question is the deterministic one and can be asserted whole.
    assert at.session_state["messages"][1]["content"] == fallback_question_for(
        ["dates", "groupSize", "budgetTotal"]
    )
    # Half a trip is a chat with no plan, so there is still nothing to navigate to.
    assert at.button == []
    assert at.sidebar.markdown == []


def test_the_rails_arrive_with_the_first_plan(offline):
    """Both rails arrive together, and the nav rail keeps the conversation.

    Then it stays: `New chat` clears the plan, and a rail that left with the plan
    would strand the trip it had just parked -- see the test below.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()

    assert not at.exception
    assert at.session_state["plan"] is not None
    assert any(button.key == "toggle-plan" for button in at.button)
    assert any(button.key == "new-chat" for button in at.button)
    # The open conversation is a highlighted row, not a second clickable copy.
    assert "Trips" in rail_text(at)
    assert 'class="tp-rail__active"' in rail_text(at)
    assert not any(button.key.startswith("open-") for button in at.button)


def test_a_new_chat_parks_the_trip_in_the_rail(offline):
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()
    parked = at.session_state["trip_id"]

    at.button(key="new-chat").set_value(True).run()

    assert not at.exception
    # The screen is empty again and the trip is a row in the rail.
    assert at.session_state["messages"] == []
    assert at.session_state["plan"] is None
    assert at.session_state["draft"] == BriefPatch()
    assert at.session_state["trip_id"] != parked
    assert [c.tripId for c in at.session_state["history"]] == [parked]
    assert at.button(key=f"open-{parked}").label == "🧳 Tokyo"
    assert (
        '<div class="tp-rail__section">Trips<span class="tp-rail__badge">1</span></div>'
        in rail_text(at)
    )
    # And the rail is still there to go back with: it is the plan that cleared,
    # not the navigation. A rail gated on the plan alone would strand this trip.
    assert any(button.key == "new-chat" for button in at.button)
    assert not any(button.key == "toggle-plan" for button in at.button)


def test_reopening_a_conversation_restores_it(offline):
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()
    parked = at.session_state["trip_id"]
    transcript = list(at.session_state["messages"])

    at.button(key="new-chat").set_value(True).run()
    at.button(key=f"open-{parked}").set_value(True).run()

    assert not at.exception
    assert at.session_state["trip_id"] == parked
    assert at.session_state["messages"] == transcript
    assert at.session_state["plan"] is not None
    assert at.session_state["draft"].destination == "Tokyo"
    # It is open, so it is the highlighted row rather than a row you can click.
    assert at.session_state["history"] == []
    assert 'class="tp-rail__active"' in rail_text(at)


def test_search_filters_the_rail(offline):
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()
    parked = at.session_state["trip_id"]
    at.button(key="new-chat").set_value(True).run()

    at.sidebar.text_input(key="rail-search").set_value("lisbon").run()
    assert "No matches." in rail_text(at)
    assert not any(button.key == f"open-{parked}" for button in at.button)

    # Case-insensitive, and it matches the row the rail actually draws.
    at.sidebar.text_input(key="rail-search").set_value("tokyo").run()
    assert any(button.key == f"open-{parked}" for button in at.button)


def test_the_form_starts_empty_and_is_refused_in_the_same_words_as_the_chat(offline):
    """A prefilled form is a trip somebody else chose.

    An empty submission has to be refused through the same contract the chat uses,
    so the two entry points cannot drift into accepting different trips. The form
    lives in the rail, so there has to be a plan before it is on screen at all.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()

    assert [(widget.label, widget.value) for widget in at.sidebar.date_input] == [
        ("Start", None),
        ("End", None),
    ]
    assert [widget.value for widget in at.sidebar.number_input] == [None, None]
    assert at.sidebar.text_input(key="form-destination").value == ""

    at.button(key="form-submit").set_value(True).run()

    assert not at.exception
    expected = ", ".join(FIELD_NAMES[field] for field in missing_fields(BriefPatch()))
    assert [error.value for error in at.sidebar.error] == [f"Still needed: {expected}."]
    # Nothing was planned from an empty form: the trip is still the first one.
    assert at.session_state["plan"].brief.destination == "Tokyo"


def test_the_form_plans_the_trip_it_was_given(offline):
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()

    at.sidebar.text_input(key="form-destination").set_value("Lisbon")
    at.sidebar.date_input(key="form-start").set_value(date(2026, 12, 1))
    at.sidebar.date_input(key="form-end").set_value(date(2026, 12, 6))
    at.sidebar.number_input(key="form-group").set_value(3)
    at.sidebar.number_input(key="form-budget").set_value(2500)
    at.button(key="form-submit").set_value(True).run()

    assert not at.exception
    assert at.session_state["plan"].brief.destination == "Lisbon"
    assert at.session_state["plan"].brief.budgetTotal == 2500


def test_leaving_a_paused_run_does_not_leave_its_thread_behind(offline):
    """A paused escalation's thread belongs to the conversation being left.

    Answering it later would resume a run for a trip that is no longer on screen,
    and the checkpoint would sit in the process-wide store forever.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()
    at.chat_input[0].set_value("set the budget to $1500").run()
    assert at.session_state["pending_escalation"], "expected the run to pause"

    at.button(key="new-chat").set_value(True).run()

    assert not at.exception
    assert at.session_state["pending_escalation"] is None
    assert not any(button.key.startswith("escalation-") for button in at.button)


def test_an_escalation_pauses_and_the_button_resumes_it(offline):
    """The whole human-in-the-loop loop, through the entry point.

    A budget this far under the plan's cost must escalate, which means the run
    pauses with a plan already built. Clicking the offered answer has to resume the
    thread it paused on -- clearing the paused id before the resume sends the answer
    to a fresh thread, and the run simply pauses again.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()

    assert not at.exception
    assert at.session_state["pending_escalation"] is None, "a generous budget should not escalate"
    assert at.session_state["plan"].brief.groupSize == 2
    # The identity the session minted reaches the brief the specialists plan
    # against, which is what keeps one visitor's preferences out of another's plan.
    assert at.session_state["plan"].brief.userId == at.session_state["user_id"]
    assert at.session_state["draft"].budgetTotal == 20000

    at.chat_input[0].set_value("set the budget to $1500").run()

    assert not at.exception
    paused = at.session_state["pending_escalation"]
    assert paused, "expected the run to pause for a decision"
    assert paused["kind"] == "escalation"
    assert at.session_state["plan"] is not None  # the plan is there to decide about
    assert at.session_state["plan"].overrunPct > ESCALATION_OVERRUN_PCT
    assert [option["value"] for option in paused["options"]] == ["accept"]

    at.button(key="escalation-accept").set_value(True).run()

    assert not at.exception
    assert at.session_state["pending_escalation"] is None
    escalation = next(
        checkpoint
        for checkpoint in at.session_state["plan"].hitl
        if checkpoint.type == "escalation"
    )
    assert escalation.status == "approved"
    # Two planning turns and the resume, both sides of each, and no more.
    assert len(at.session_state["messages"]) == 6


def test_each_session_gets_its_own_identity(offline):
    """Two visitors must not share memory buckets.

    The ids used to be a fixed `trip-demo`/`demo-user` pair, so on a shared
    deployment one traveller's confirmed stay would show up in the next one's plan:
    the memory store keys by these ids and nothing else separates sessions.
    """
    first = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    second = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()

    assert first.session_state["user_id"] != second.session_state["user_id"]
    assert first.session_state["trip_id"] != second.session_state["trip_id"]
    # Neither session starts from the other's trip, or from any trip at all.
    assert first.session_state["draft"] == BriefPatch()
    assert second.session_state["draft"] == BriefPatch()
