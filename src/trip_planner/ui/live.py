"""Live progress for a Streamlit script run.

`on_progress` is called from inside the specialists, and the specialist the
callback belongs to decides which thread it arrives on. On the deterministic
path that is the script's own thread. Under the supervisor it is not: the
supervisor delegates through typed tools, and LangGraph's `ToolNode` runs a
batch of tool calls on a thread pool, so the callback arrives on a worker
thread with no `ScriptRunContext`. Streamlit logs

    Thread 'ThreadPoolExecutor-7_0': missing ScriptRunContext!

and drops the update, which is why the five agent rows sat at "Queued" for the
whole run whenever a model was configured -- the one view whose job is to make
the fan-out visible.

Re-attaching the run context is the fix. Streamlit's `add_script_run_ctx`
documents the self-attach case for exactly this: called from inside the worker
thread, it seeds `ThreadState` as well, so the widget write enqueues on the
session's message queue instead of raising. That queue is drained while the
planning call is still blocking the script thread, so the rows update live
rather than only at the end.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

Event = TypeVar("Event")


def bind_to_script_run(handler: Callable[[Event], None]) -> Callable[[Event], None]:
    """Return `handler` bound to this script run, callable from any thread.

    The context is captured here, on the script's own thread, because a worker
    thread has nothing to look it up from -- that is precisely the state it is
    missing. It is re-attached on every call rather than once per thread: a
    pooled thread is not ours to keep state on, and the attachment is
    last-wins, so each call stamps the run it was raised by.

    With no script run in progress (a bare `python script.py`, or a test) this
    is a pass-through, because there is nothing to attach and nothing to write
    to.
    """
    run_ctx = get_script_run_ctx(suppress_warning=True)

    def bound(event: Event) -> None:
        if run_ctx is not None and get_script_run_ctx(suppress_warning=True) is not run_ctx:
            add_script_run_ctx(threading.current_thread(), run_ctx)
        handler(event)

    return bound
