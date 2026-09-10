# Debugging log

Every entry here cost more than it should have, usually because the symptom
pointed somewhere other than the cause. They are recorded with the misleading
signal first, because that is the part worth recognising again.

---

## 1. A provider that answers 401 for a valid key

**Symptom.** Every MiniMax call returned `401 invalid api key (2049)`. The key
was newly issued and correct.

**Why it misled.** A 401 reads as "bad credential", so the instinct is to
regenerate the key — which produces another key that also fails.

**Cause.** MiniMax runs two independent account systems. A mainland-China key is
rejected by the international host and vice versa. The default endpoint pointed
at the wrong one.

**Fix.** Default to `https://api.minimaxi.com/v1`; document that the two are not
interchangeable.

**How it was found.** Probing the same key against four candidate hosts and
building a matrix, rather than assuming the key was at fault:

| Host | Result |
| --- | --- |
| `api.minimaxi.com` | 200 |
| `api.minimax.chat` | 200 (legacy alias) |
| `api.minimaxi.chat` | 401 |
| `api.minimax.io` | 401 |

---

## 2. A model that ignores `tool_choice` instead of refusing it

**Symptom.** Structured output produced nothing. The call succeeded, and
`tool_calls` was `null`.

**Why it misled.** From the call site this is indistinguishable from the 401
above: both end with "no structured result". After fixing the endpoint, the
failure looked unchanged, suggesting the endpoint fix had not worked.

**Cause.** MiniMax silently ignores a forced `tool_choice` and answers in prose.
`response_format: json_schema` is ignored the same way.

**Fix.** Bind the tool manually with `tool_choice: "auto"` and validate the
arguments.

**How it was found.** Sending the same request twice, changing only
`tool_choice`, and diffing the responses:

| `tool_choice` | `finish_reason` | `tool_calls` |
| --- | --- | --- |
| `{type: function, ...}` | `stop` | none |
| `"auto"` | `tool_calls` | well-formed |

---

## 3. A schema whose limits are advisory

**Symptom.** Validation rejected drafts for overrunning `maxLength` and
`maxItems` — fields the JSON schema had already declared.

**Cause.** Both providers treat a schema's numeric constraints as a hint.
Structured-output extraction retries only a few times before giving up, so a
first attempt that overshoots exhausts the budget.

**Fix.** Restate every hard limit in the prompt, and add one corrective retry
that feeds the validation error back.

**Measured.** Before: two fallback warnings on every plan. After: four
consecutive plans over twelve rounds with none.

---

## 4. A response format the provider does not have

**Symptom.** `This response_format type is unavailable now` (HTTP 400) from
DeepSeek. All three model-backed specialists fell back on every round.

**Why it misled.** It only appeared once a real key was configured. Without one,
the same specialists fall back for a legitimate reason, so the output looked
identical to a correct offline run.

**Cause.** LangChain's `with_structured_output` defaults to a `json_schema`
response format that DeepSeek does not implement.

**Fix.** `method="function_calling"`.

---

## 5. A fix that the next layer rejects

**Symptom.** After fixing #4, DeepSeek V4.1 Flash answered
`Thinking mode does not support this tool_choice`.

**Cause.** Tool-calling structured output forces a named tool, and V4's thinking
mode refuses that.

**Fix.** Disable thinking — but through `extra_body`, not `model_kwargs`. The
latter hands the field to the OpenAI SDK as a keyword argument, which fails with
`Completions.create() got an unexpected keyword argument 'thinking'`. That
second error is easy to read as "the fix was wrong" rather than "the fix was
delivered by the wrong mechanism".

---

## 6. A grounding failure that was our own prompt

**Symptom.** `Itinerary returned an ungrounded location: Mock sight near Osaka
& Nara (sight)` — the model appending a category to a place name, exactly what
the prompt forbade.

**Why it misled.** It looks like a model that will not follow instructions, and
the tempting fix is a firmer instruction.

**Cause.** The candidate list was rendered as `- Mock sight near Osaka (sight)`
and the prompt asked for the name "copied character for character". The model
copied the bullet, which is what it was shown. The instruction was not being
disobeyed; it was ambiguous.

**Fix.** Render candidates as labelled fields and point the instruction at the
field:

```
- name: Mock sight near Osaka
  category: sight
```

**Lesson.** When a model "ignores" an instruction, check what it was actually
shown before strengthening the wording.

---

## 7. A docstring that is not a docstring

**Symptom.** None visible. Delegation tools worked; the supervisor's choices
were poor.

**Cause.** LangChain builds a tool's description from its docstring, and the
tools used an f-string. Python evaluates that as an expression, so the
description was empty and the model had nothing to choose between.

**Fix.** Pass `description=` explicitly.

**How it was found.** `ruff`'s `B021`, not by reading the code or the output.
A linter rule caught a semantic defect that produced no error at runtime.

---

## 8. Telemetry that fails silently

**Symptom.** An empty LangSmith project, while the app reported no errors.

**Why it misled.** Tracing failures never stop a run. The plan completes
normally, so a misconfigured region looks like "tracing was never wired up".

**Worse:** the failure *was* being logged, and an earlier verification run had
filtered it out with `grep -v warning`. The conclusion "tracing is verified
working" was drawn from output that had the disproving line removed.

**Cause.** LangSmith keys are regional; a non-US key is rejected by the default
endpoint with a bare `403` on ingest.

**Fix.** Set `LANGSMITH_ENDPOINT` to match the region in the LangSmith URL.

**Verification.** Not "no errors appeared" but querying the project's own API
and counting stored runs — 25 runs across `detect_conflicts`, `build_plan`,
`ChatOpenAI` and the rest.

---

## 9. A negotiation that never converges

**Symptom.** The orchestrator ran its full three rounds every time and still
ended with sections marked `needs_you`. The same day-4 overlap recurred verbatim
in rounds 2 and 3, and the budget overrun oscillated
29.25% → 16.50% → 25.25%.

**Cause.** A revising specialist only ever sees its own proposal. The conflict
constraint gave it the counterpart's clock times and nothing else, so the
itinerary agent had to guess what to avoid — and in one round moved an activity
onto exactly the transport leg it was supposed to clear.

**Fix.** Name the blocked window and its owner:

```
on day 4 keep clear of 09:00-11:20, held by transport (Tokyo → Kyoto)
```

**Result.** Converges in round 2 with zero conflicts, which also removes a full
round of model calls.

---

## 10. A deployment that only fails in the cloud

**Symptom.** None locally. Would have been `ModuleNotFoundError: trip_planner`
on the first cloud run.

**Cause.** Streamlit Community Cloud installs from `requirements.txt` and does
not install the project itself, so a `src/` layout is not on `sys.path`. The
local editable install hides this completely.

**Fix.** A path shim in the entry point, a no-op when the project is installed.

**How it was found.** Building a clean virtualenv with only `requirements.txt`
and running from there. "It works on my machine" was the exact failure mode this
avoided.

---

## 11. A feature that only works without an API key

**Symptom.** With a key configured, the five agent rows sat at "Queued" for the
whole run. Without one, the same rows animated correctly. The plan that came out
was fine either way, which is why nobody had chased it.

**Why it misled.** "Progress display is cosmetic" made it a low priority, and
the offline path — the one CI and a fresh checkout take — worked. The rows were
also only ever checked on that path.

**Cause.** `on_progress` writes Streamlit widgets, and the thread it arrives on
is not ours to choose. On the deterministic path it is the script's own thread.
Under the supervisor it is a LangGraph worker thread: `ToolNode._func` runs a
batch of tool calls through `executor.map`. A worker thread has no
`ScriptRunContext`, so the widget write raised `NoSessionContext` — and
`delegate` emits `agent_started` *before* calling the specialist, so every tool
call died before doing any work. The supervisor collected nothing, raised
"completed without delegating", and the workflow fell back to deterministic
dispatch. The visible cost was a frozen progress panel; the real cost was that
the supervisor never ran at all.

**Fix.** First, a shim: `ui.live.bind_to_script_run` captured the context on the
script thread and re-attached it per call, so worker threads enqueued their deltas
on the session like any other write. Streamlit documents the self-attach case
(`add_script_run_ctx` from inside the worker also seeds `ThreadState`), which kept
the write from raising instead of merely disappearing.

Then the mechanism that made the shim necessary was removed. Progress now travels
on the graph's custom stream — a node writes with `get_stream_writer`, a
delegation tool with `Runtime.stream_writer`, and the UI iterates the stream on
its own thread — so no specialist touches a widget and there is no thread to bind.
`ui/live.py` is deleted, and a test asserts that nothing under `trip_planner/`
imports Streamlit at all (item 1.4 of `docs/framework-alignment.md`).

The other half of the lesson is handled a level up: a specialist that raises used
to take the whole fan-out with it, so the delegation tools are wrapped in
`ToolErrorMiddleware` and a failure reaches the model as a `ToolMessage` it can
work around (`supervisor_middleware`, and item 1.3).

**How it was found.** The server log held `ThreadPoolExecutor-7_0` …
`ThreadPoolExecutor-11_0`, five threads warning three times each — the shape of
one parallel tool batch. Reproducing the two lines `ToolNode` uses
(`get_executor_for_config` + `executor.map`) around the supervisor's own tools
showed the callback landing on five worker threads, and an `AppTest` script with
a real run context showed the write raising `NoSessionContext` off-thread and
succeeding once bound.

**Same shape as entry #10.** The path that was exercised (no key, deterministic
dispatch) was not the path that was shipped (key present, supervisor).

---

## Recurring lessons

**A silent fallback is worse than a crash.** Most of these took time because the
system kept working. Fallbacks are the right design, but they need to say which
path ran — every section records `Planner source: model.` or
`Planner source: deterministic fallback.` for this reason.

**Never verify with a filter applied.** Entry #8 was wrongly declared fixed
because the disproving line had been grepped away. Verify against the source of
truth — query the store, count the rows.

**Reproduce before theorising.** Entries #1 and #2 were both settled by sending
the same request with one variable changed and diffing, which took minutes.
Reasoning about which was more likely would have taken longer and could have
landed on the wrong one.

**Bulk edits need a checker, not a reading.** A regex pass to satisfy a lint rule
silently wrapped list elements in tuples — `MONTHS` became length 1, a
`st.metric` call lost its arguments, a secrets list stopped iterating. None were
visible in a skim of the diff; an AST scan and the test suite found them all.
