"""The Streamlit entry point, driven headlessly.

`streamlit_app.py` is ~550 lines and had no test, which is how a dead join
(`section.id in checkpoint_id`, fixed in the first wave) survived in it. `AppTest`
runs the real script, so this covers the wiring the module tests cannot: the
opening screen, the rails that arrive with the first plan, the pause for an
escalation, and the session state that carries the paused thread.

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
    return f"Tokyo & Kyoto, {start} to {end}, 2 people, budget $20000"


def test_the_app_opens_on_a_greeting_and_nothing_else(offline):
    """The first screen is a greeting and a chat box. Nothing else.

    It used to open on `demo_brief()` -- Tokyo & Kyoto, seven days, $4,000, already
    filled in -- so a visitor's first act was to delete someone else's trip. Then it
    opened on three example trips and a sidebar describing a trip that did not exist
    yet. Neither rail is rendered now: the plan rail because there is no plan, the
    sidebar because nothing in it describes anything yet.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()

    assert not at.exception
    assert at.session_state["plan"] is None
    assert at.session_state["draft"] == BriefPatch()
    assert at.session_state["messages"] == []
    assert at.session_state["pending_escalation"] is None
    assert any("tp-hero" in element.value for element in at.markdown)
    # No example chips, no plan toggle, no form: no buttons at all.
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
    # Half a trip is not a trip: the rails stay away until one can be planned.
    assert at.button == []
    assert at.sidebar.markdown == []


def test_the_rails_arrive_with_the_first_plan(offline):
    """Both rails appear together, and the form in the sidebar starts empty.

    A form prefilled with four values is a trip somebody else chose, and a
    traveller who submits it without reading plans a trip they never asked for.
    Submitting the empty form has to be refused with the same words the chat would
    use, so the two entry points cannot drift into accepting different trips.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()

    assert not at.exception
    assert at.session_state["plan"] is not None
    assert any(button.key == "toggle-plan" for button in at.button)
    assert at.sidebar.markdown, "the sidebar should describe the trip that now exists"
    assert [(widget.label, widget.value) for widget in at.sidebar.date_input] == [
        ("Start", None),
        ("End", None),
    ]
    assert [widget.value for widget in at.sidebar.number_input] == [None, None]
    assert [widget.value for widget in at.sidebar.text_input] == ["", ""]

    at.sidebar.button[0].set_value(True).run()

    assert not at.exception
    expected = ", ".join(FIELD_NAMES[field] for field in missing_fields(BriefPatch()))
    assert [error.value for error in at.sidebar.error] == [f"Still needed: {expected}."]
    # Nothing was planned from an empty form.
    assert at.session_state["plan"].brief.destination == "Tokyo & Kyoto"


def test_the_form_plans_the_trip_it_was_given(offline):
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(one_message_trip()).run()

    at.sidebar.text_input[0].set_value("Lisbon")
    at.sidebar.date_input[0].set_value(date(2026, 12, 1))
    at.sidebar.date_input[1].set_value(date(2026, 12, 6))
    at.sidebar.number_input[0].set_value(3)
    at.sidebar.number_input[1].set_value(2500)
    at.sidebar.button[0].set_value(True).run()

    assert not at.exception
    assert at.session_state["plan"].brief.destination == "Lisbon"
    assert at.session_state["plan"].brief.budgetTotal == 2500


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
