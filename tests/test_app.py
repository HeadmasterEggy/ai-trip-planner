"""The Streamlit entry point, driven headlessly.

`streamlit_app.py` is ~450 lines and had no test, which is how a dead join
(`section.id in checkpoint_id`, fixed in the first wave) survived in it. `AppTest`
runs the real script, so this covers the wiring the module tests cannot: the pause
for an escalation, the button that answers it, and the session state that carries
the paused thread.

Kept offline by blanking the credentials before the script runs: `load_dotenv` does
not overwrite variables that already exist, so an empty value keeps the run on the
deterministic path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from trip_planner.workflow import ESCALATION_OVERRUN_PCT

APP = Path(__file__).resolve().parent.parent / "streamlit_app.py"
TIMEOUT = 120


@pytest.fixture
def offline(monkeypatch):
    for key in ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "LANGSMITH_TRACING"):
        monkeypatch.setenv(key, "")


def test_the_app_starts_without_a_model_key(offline):
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()

    assert not at.exception
    assert at.session_state["plan"] is None
    assert at.session_state["pending_escalation"] is None


def test_an_escalation_pauses_and_the_button_resumes_it(offline):
    """The whole human-in-the-loop loop, through the entry point.

    A budget this far under the plan's cost must escalate, which means the run
    pauses with a plan already built. Clicking the offered answer has to resume the
    thread it paused on -- clearing the paused id before the resume sends the answer
    to a fresh thread, and the run simply pauses again.
    """
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
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
    # Both sides of the resumed turn are in the transcript, and no more.
    assert len(at.session_state["messages"]) == 4
