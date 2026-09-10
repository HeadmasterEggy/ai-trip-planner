"""Live progress: an event that arrives on someone else's thread.

`on_progress` is called from inside the specialists. Under the supervisor those
run on LangGraph's tool executor, so the callback lands on a worker thread with
no `ScriptRunContext`, and a widget write from there raises `NoSessionContext` --
inside the tool, before the specialist has done anything. The first test below
pins the wrapper that fixes it; the `AppTest` one pins the observable result.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
from streamlit.testing.v1 import AppTest

from trip_planner.ui.live import bind_to_script_run


class _FakeRun:
    """Enough of a `ScriptRunContext` for the thread API.

    `add_script_run_ctx` seeds `ThreadState` from `pages_manager.main_script_hash`
    when it attaches from inside the worker thread, which is the branch the fix
    depends on.
    """

    def __init__(self, name: str = "script-run") -> None:
        self.name = name
        self.pages_manager = SimpleNamespace(main_script_hash="hash")


def _in_fresh_thread(work, *args, **kwargs):
    """Run `work` on a thread of its own.

    Every test here needs a thread that either has a script run context or
    provably has none, without disturbing whatever the test runner's own thread
    happens to be carrying.
    """
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["value"] = work(*args, **kwargs)
        except BaseException as error:  # noqa: BLE001 - re-raised below
            box["error"] = error

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")


def test_a_worker_thread_is_given_the_script_run_context():
    ctx = _FakeRun()
    seen: list[object] = []

    def script_thread() -> None:
        # What Streamlit does for the thread running the script.
        add_script_run_ctx(threading.current_thread(), ctx)
        bound = bind_to_script_run(
            lambda event: seen.append(get_script_run_ctx(suppress_warning=True))
        )
        worker = threading.Thread(target=bound, args=(None,))
        worker.start()
        worker.join()

    _in_fresh_thread(script_thread)
    assert seen == [ctx]


def test_without_the_binding_the_same_handler_sees_nothing():
    """The condition that made every row update a no-op, and killed the tool.

    This is the old behaviour kept as a control: it is what the supervisor's
    worker threads looked like before `bind_to_script_run`.
    """
    ctx = _FakeRun()
    seen: list[object] = []

    def script_thread() -> None:
        add_script_run_ctx(threading.current_thread(), ctx)
        worker = threading.Thread(
            target=lambda: seen.append(get_script_run_ctx(suppress_warning=True))
        )
        worker.start()
        worker.join()

    _in_fresh_thread(script_thread)
    assert seen == [None]


def test_a_pooled_thread_carries_the_current_run_not_the_last_one():
    """Re-attaching per call is what makes a reused thread safe.

    LangGraph builds a fresh executor per tool batch, but nothing promises it
    will, and a pooled thread outliving one script run would otherwise write
    into the previous session.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:

        def run_seen_by_worker(ctx: _FakeRun) -> str:
            result: dict[str, _FakeRun] = {}

            def script_thread() -> None:
                add_script_run_ctx(threading.current_thread(), ctx)
                bound = bind_to_script_run(
                    lambda event: result.update(ctx=get_script_run_ctx(suppress_warning=True))
                )
                pool.submit(bound, None).result()

            runner = threading.Thread(target=script_thread)
            runner.start()
            runner.join()
            return result["ctx"].name

        first = run_seen_by_worker(_FakeRun("run-1"))
        second = run_seen_by_worker(_FakeRun("run-2"))

    assert [first, second] == ["run-1", "run-2"]


def test_a_bare_run_is_a_pass_through():
    """`python script.py` has no context to attach, and must not crash."""
    seen: list[str] = []
    bound = _in_fresh_thread(lambda: bind_to_script_run(seen.append))
    _in_fresh_thread(bound, "event")
    assert seen == ["event"]


APP_SCRIPT = """
import threading

import streamlit as st

from trip_planner.ui.live import bind_to_script_run

slot = st.empty()
slot.markdown("queued")
errors = []


def render(event=None):
    try:
        slot.markdown("running")
    except Exception as error:  # noqa: BLE001 - recorded for the assertion
        errors.append(type(error).__name__)


target = bind_to_script_run(render) if BIND else render
worker = threading.Thread(target=target, args=(None,))
worker.start()
worker.join()

st.session_state["errors"] = errors
"""


def test_a_worker_thread_can_actually_update_the_widget():
    """End to end, with a real script run context.

    Before the fix the row stayed "queued" and the worker raised
    `NoSessionContext`; in the app that exception came out of `on_progress`
    inside the delegation tool, so the supervisor lost every specialist it
    asked for and the run quietly fell back to deterministic dispatch.
    """
    before = AppTest.from_string(APP_SCRIPT.replace("BIND", "False")).run()
    assert [m.value for m in before.markdown] == ["queued"]
    assert before.session_state["errors"] == ["NoSessionContext"]

    after = AppTest.from_string(APP_SCRIPT.replace("BIND", "True")).run()
    assert [m.value for m in after.markdown] == ["running"]
    assert after.session_state["errors"] == []
