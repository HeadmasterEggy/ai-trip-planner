"""The Streamlit entry point, driven headlessly.

`streamlit_app.py` is ~500 lines and had no test, which is how a dead join
(`section.id in checkpoint_id`, fixed in the first wave) survived in it. `AppTest`
runs the real script, so this covers the wiring the module tests cannot: the
opening screen, the pause for an escalation, the button that answers it, and the
session state that carries the paused thread.

Kept offline by blanking the credentials before the script runs: `load_dotenv` does
not overwrite variables that already exist, so an empty value keeps the run on the
deterministic path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from trip_planner.chat import fallback_question_for
from trip_planner.contracts import BriefPatch
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


def test_the_app_opens_on_a_greeting_and_nothing_else(offline):
    """Nothing about the trip is assumed before the traveller says anything.

    The first screen used to open on `demo_brief()` -- Tokyo & Kyoto, seven days,
    $4,000 -- so a visitor's first act was to delete someone else's trip. It now
    opens on a greeting and a chat box, with no plan rail beside it: with nothing
    planned there is nothing to collapse, so the conversation gets the full width.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()

    assert not at.exception
    assert at.session_state["plan"] is None
    assert at.session_state["draft"] == BriefPatch()
    assert at.session_state["messages"] == []
    assert at.session_state["pending_escalation"] is None
    assert any("tp-hero" in element.value for element in at.markdown)
    assert not any(button.key == "toggle-plan" for button in at.button)


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
    # Nothing was planned, so there is still no plan rail to collapse.
    assert not any(button.key == "toggle-plan" for button in at.button)


def test_an_escalation_pauses_and_the_button_resumes_it(offline):
    """The whole human-in-the-loop loop, through the entry point.

    A budget this far under the plan's cost must escalate, which means the run
    pauses with a plan already built. Clicking the offered answer has to resume the
    thread it paused on -- clearing the paused id before the resume sends the answer
    to a fresh thread, and the run simply pauses again.
    """
    start, end = demo_dates()
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value(f"Tokyo & Kyoto, {start} to {end}, 2 people, budget $20000").run()

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
