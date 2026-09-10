# Framework alignment

How this project's orchestration compares with the multi-agent patterns LangChain and LangGraph
document, what the differences cost, and the order to close them.

Measured against the installed versions rather than against memory: `langchain` 1.4.0,
`langchain-core` 1.6.2, `langgraph` 1.2.11. The official guides move quickly, so every
recommendation below names the API it depends on and where it is documented.

## Scope

This is about **how the orchestration is built**, not about what it decides or how it looks.
Product-level rules (budget arithmetic, conflict detection, the fallback chain) are covered by
`docs/cost-rules.md` and `docs/langgraph-orchestration.md`; presentation is a separate list.

## The official taxonomy

LangChain 1.x names five patterns ([Multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent)):

| Pattern | Mechanism |
| --- | --- |
| **Subagents** | A main agent coordinates subagents by calling them as tools; all routing passes through it |
| **Handoffs** | A tool call updates state that switches agent or reconfigures the current one |
| **Skills** | One agent stays in control and loads specialised prompts on demand |
| **Router** | A classification step dispatches to specialised agents and synthesises the results |
| **Custom workflow** | A bespoke LangGraph flow mixing deterministic logic with agentic nodes |

This project is **Subagents plus Custom workflow**: one `create_agent` supervisor whose tools each
invoke a specialist (`supervisor.py`), wrapped in a deterministic `StateGraph` that owns validation,
the round limit and the stopping condition (`workflow.py`).

That combination is not a deviation. The migration guide says so in as many words
([Migrate from langgraph-supervisor](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor)):

> The `langgraph-supervisor` package is no longer actively maintained. Instead use the subagents
> pattern: a main agent coordinates specialised workers by calling them as tools.

> **When to use a custom StateGraph instead** — Use a custom `StateGraph` when you need to mix
> deterministic steps with agentic ones. For example, fixed routing, validation, or external API
> calls alongside `create_agent` nodes.

The guide's target shape (a custom `StateGraph` outer layer, a `create_agent` supervisor, workers
wrapped as tools) is what this repository already is. `langgraph-supervisor` is not installed and
should stay that way.

## How it is built today

The framework surface is five imports:

| Import | Where | Role |
| --- | --- | --- |
| `ChatOpenAI` | `models.py:26` | provider + structured output |
| `create_agent` | `supervisor.py:25` | the one real agent loop |
| `tool`, `ToolRuntime` | `supervisor.py:33` | delegation, and the writer a tool narrates through |
| `get_stream_writer` | `workflow.py:24` | progress from a node |
| `StateGraph`, `START`, `END` | `workflow.py:25` | deterministic orchestration |

`create_agent` itself returns a `CompiledStateGraph` built from `StateGraph` and `ToolNode`
(`langchain/agents/factory.py:26,28`), so the supervisor is a nested graph inside the `dispatch`
node of the outer one. The specialists are **not** LangChain agents: three do a single
structured-output call and two are deterministic calculators, behind a framework-neutral
`Specialist` protocol (`specialists/base.py:19-39`).

## Conformance review

| Dimension | Official | Here | Verdict |
| --- | --- | --- | --- |
| Supervisor | `create_agent` + one tool per worker | `supervisor.py:323` (built once, `ASK_TOOLS`) | Conformant |
| Outer control | custom `StateGraph` for deterministic steps | `workflow.py:547-558` | Conformant, and what the guide recommends |
| Worker state | stateless per invocation (isolated) | pure `invoke(brief, ctx, revision)` | Conformant |
| Parallelism | the main agent may call several subagents in one turn | models batching tool calls, `ToolNode` runs them on a pool | Conformant |
| Structured output | provider-native / json_schema / function calling | `method="function_calling"` (`models.py:117`) | Conformant (required for DeepSeek) |
| Tracing | LangSmith | `@traceable` on `detect_conflicts` | Conformant |
| **Delegation input** | the supervisor decides **what** each worker is told | no input at all, and deliberately so: the worker derives its prompt from the brief | **Deliberate** |
| Worker results | `Command`/`InjectedToolCallId` back into graph state | `SupervisorState` channels with reducers, read out by the node | Conformant |
| Dependency injection | `context_schema` + `ToolRuntime`, agent built once | `DelegationContext` via `runtime.context`, cached agent | Conformant |
| **Human in the loop** | `checkpointer` + `interrupt()` + `Command(resume=...)` | plan records + full re-run | **Gap** |
| Resilience | `ModelRetry` / `ToolRetry` / `ToolError` / `ModelFallback` / call limits | `ModelRetry`, `ToolRetry`, `ToolError` and both call limits in `supervisor_middleware`; no provider fallback yet | **Conformant**, fallback deferred |

## What is deliberately different

These are load-bearing, and the strategy below must not sand them off:

1. **The supervisor cannot alter trip facts.** The brief, the ports and the memory store arrive in a
   `DelegationContext` (`supervisor.py:130-146`) that the graph constructs, never the model, and the
   tools take no arguments at all (`ASK_TOOLS`, `supervisor.py:308`). The deterministic rules
   downstream therefore validate the traveller's request, not a model's paraphrase of it. The
   mechanism moved in item 2.1 -- injected through the runtime instead of captured in a closure --
   and the guarantee did not change.
2. **Revision requests are immutable.** A revision tool looks its request up in the run context
   (`_revise_tool`, `supervisor.py:265`): the model routes a validated constraint to its owner and
   can neither supply nor rewrite one.
3. **Deterministic fallbacks at every layer.** A model that fails schema validation, a supervisor
   that fails or under-delegates, and a graph that runs too long all end in the deterministic path
   (`models.py:127`, `workflow.py:378-420`, `workflow.py:471-475`).
4. **A decision is a preference, not an override.** `apply_decision` writes to long-term memory
   (`decisions.py:49`), which is why a choice survives the next message — something a one-shot
   `interrupt` would not give.

Nothing in the strategy below weakens (1)-(3). Where it touches (4), it does so deliberately and
says what is gained and lost.

## Improvement strategy

Effort is S (hours), M (about a day), L (more than a day) for one developer already familiar with
the code. Waves are ordered by risk, not by value: Wave 1 cannot break the plan, Wave 3 can.

**Progress.** Waves 1 and 2 have shipped. Wave 1: role-based routing, no delegation argument,
retries and declared bounds, and progress as a stream (`ui/live.py` deleted). Wave 2: the
supervisor agent and the graph are each built once with per-run state injected through
`ToolRuntime` / `Runtime`, and worker results travel as graph state rather than through a side
channel. Open: Wave 3 (persistence and human in the loop) and Wave 4 (naming, latency).

### Wave 1 — Non-structural

Low risk, no change to the state model. Each item is independently shippable.

#### 1.1 A route per model role, not per specialist (S) — shipped

**Problem.** Four different workloads shared one route. The supervisor
(`supervisor.py:155,192` at the time `:149,186`) borrowed the itinerary route for its dispatch and
revision decisions, and so did brief extraction and reply generation (`chat.py:207,293`) — a small
classification and a short generation, both riding the day-plan configuration. Meanwhile
`MODEL_ROUTING` carried `transport` and `accommodation`, which never call a model at all, and the
sidebar listed them as routed. The map described "the five specialists"; what the code does is
"six model roles, and two calculators that have none".

**Official mechanism.** None needed — `MODEL_ROUTING` is this project's own seam. The point is the
one the docs make about subagent specs: the model a role runs on is a per-role decision, and
deciding is cheap where generating is not.

**Target.** Key the map by role and route each call site to its own:

```python
MODEL_ROUTING: dict[str, str] = {
    "itinerary": "deepseek",
    "destination-guide": "deepseek",
    "dining": "deepseek",
    "supervisor": "deepseek",
    "brief-extraction": "deepseek",
    "reply": "deepseek",
}
```

`create_routed_chat_model` keeps its lenient default for an unknown role — raising there would break
a run rather than degrade it — so the map's completeness is a test's job, not a runtime check.

**Tests.** The map's key set is asserted exactly; `transport` and `accommodation` are asserted
absent; every value is asserted to be a provider the factory branches on (anything that is not
`deepseek` silently takes the MiniMax branch, so a typo would be routed to the wrong account system);
and a spy over the factory proves which roles ask — the three model-backed specialists during a
deterministic run, and each of supervisor, extraction and reply from the module that owns it.

**Done when.** Changing any role's provider is a one-line edit and the sidebar's routing table only
lists roles that call a model. *Shipped.*

#### 1.2 Decide what `objective` is for (S) — shipped, deleted

**Problem.** The delegation tool declared `objective: str` and never used it, while
`specialist.invoke(brief, ctx, None)` rebuilt its prompt from the brief alone. The dispatch
prompt asked the supervisor to "delegate a bounded objective to each", and the model's answer
was discarded. That is not how the documented pattern
works — in the [Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
pattern the main agent "decides which subagent to invoke, **what input to provide**, and how to
combine results" — and a parameter that does nothing is worse than no parameter, because it reads as
a capability.

**Decision.** Deleted, in favour of the invariant over the capability. The plan is a function of the
brief; the supervisor's job is genuinely "who and when". Both tool factories now build argument-free
tools and say why in the docstring. If a bounded objective is ever wanted, the honest version is a
typed field the specialist validates, not free text.

**Tests.** The dispatch and revision tools are asserted to expose an empty schema (no properties to
fill in, not merely no required ones), and the specialist-running test invokes with `{}`.

**Done when.** Nothing in the delegation surface can carry text from the model into a specialist.
*Shipped.*

#### 1.3 Adopt the official resilience middleware (M) — shipped

**Problem.** Two kinds of failure were handled by hand, and one was not handled at all:

- Schema failures: one corrective retry (`models.py:127-131`) — fine, keep.
- Transient provider failures: **no retry**. A timeout or 429 dropped straight to the deterministic
  fallback, a silent downgrade of the whole section.
- Loop bounds: the round limit is ours (`workflow.py:539`), and the only bound on the supervisor's
  own tool loop was LangGraph's default recursion limit, whose failure surfaced as an exception
  caught by a broad `except` (`workflow.py:420`).
- Provider fallback: the MiniMax branch in `models.py:80-89` is unreachable, because every entry in
  `MODEL_ROUTING` points at DeepSeek.

**Official mechanism.** Provider-agnostic middleware
([Prebuilt middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)):
`ModelRetryMiddleware`, `ToolRetryMiddleware`, `ToolErrorMiddleware`, `ModelFallbackMiddleware`,
`ModelCallLimitMiddleware`, `ToolCallLimitMiddleware` — all importable from
`langchain.agents.middleware` in the installed version. `ToolErrorMiddleware` needs
`langchain>=1.3.14`; this project has 1.4.0.

**Shipped.** `supervisor_middleware(tool_count)` in `supervisor.py` builds one list for both agents:
`ToolErrorMiddleware` (with `on_error` naming the exception type and not echoing its text),
`ToolRetryMiddleware(max_retries=1, on_failure="error")`, `ToolCallLimitMiddleware`,
`ModelRetryMiddleware(max_retries=2, backoff_factor=2.0)` and `ModelCallLimitMiddleware`. The two
bounds are module constants (`MODEL_CALL_LIMIT`, `TOOL_CALLS_PER_SPECIALIST`) rather than numbers
buried in a call.

**The order rule, which the official example gets wrong.** `_chain_tool_call_wrappers` documents
"first = outermost", so the error handler has to come *before* the retry it is meant to catch. The
docs' own example lists the retry first with a comment saying to place it "inner" — following the
example would make the retry dead code, because the handler would convert the failure before the
retry ever saw an exception. The factory's implementation, and the tests here, follow the prose.

**Deliberately not included.** `ModelFallbackMiddleware`. It is the right mechanism, but using it
needs a second *configured* provider and a decision about which role falls back to what, and the
MiniMax branch has no test path without its key. Wiring it blind would add a code path nobody can
exercise. It is a separate item.

**What the counterfactual showed.** Running these tests with `supervisor_middleware` replaced by an
empty list — which is what the code did before — reproduces the old behaviour exactly:

| Scenario | Without middleware | With it |
| --- | --- | --- |
| Transient model failure | `ConnectionError` escapes `dispatch_with_supervisor` | retried, run continues |
| A specialist tool raises | `ValueError` escapes; the whole fan-out dies | sanitized error `ToolMessage`, batch survives |
| Model that never stops delegating | recursion limit, surfacing as a `KeyError` | stops at the declared call limit |

**Risks and how they are held.** A retry policy that is too eager multiplies cost on a hard failure:
retries stay at 1-2 with backoff, and the deterministic fallback remains the final answer. Order is
semantic, so it lives in one factory with the reason written down, not at the call sites.

**Tests.** `tests/test_supervisor_middleware.py` drives the real `dispatch_with_supervisor` against a
scripted chat model — no key, no network, no mocked `create_agent`: the retry, the sanitized
surviving batch, the bounded loop, the revision agent carrying the same list, and the order rule.

**Done when.** No transient provider error reaches the user as a silently downgraded section, and the
supervisor's loop bounds are declared rather than inherited. *Shipped.*

#### 1.4 Replace the custom progress callback with official streaming (M) — shipped

**Problem.** Progress travelled through `OrchestratorOptions.on_progress`, a hand-rolled callback the
specialists called from whatever thread they happened to run on. That design produced the
worker-thread bug: the callback wrote Streamlit widgets with no `ScriptRunContext`, raised inside the
tool, and took the whole delegation with it. The first fix (`ui/live.py`) bound the run context
correctly and was tested, but the fragility was structural: every consumer of `on_progress` had to
know which thread it was called on.

**Official mechanism.** LangGraph streams execution: `stream`/`astream` with `stream_mode`, and
`stream_events`/`astream_events`. A node writes custom events with
`langgraph.config.get_stream_writer`; a tool writes them with `ToolRuntime.stream_writer`, which is
the documented way for a worker to narrate what it is doing.

**Shipped, and simpler than the plan.** No translation layer was needed: the events *are* the
`ProgressEvent` payloads, published where the work happens. `ProgressEvent` moved to `contracts.py`
so a tool can build one without importing the graph.

- `workflow.write_progress` publishes from the node's own thread (the deterministic path).
- The delegation tools publish with `runtime.stream_writer`, and `supervisor._forward_custom_events`
  pumps the nested agent's custom stream into the outer one — a nested agent is not part of the outer
  graph's stream, so that hop has to be explicit, and doing it in the node is what keeps the write off
  the tool pool.
- `run_orchestrator_stream` returns a `PlanStream`: iterate it for events, read `.plan` after. One pass
  produces both, so the plan cannot be a generator return value. `run_orchestrator` is unchanged for
  callers that do not need progress — a non-streamed run makes the writer a no-op.
- `run_trip_chat_stream` is the chat-level equivalent; `run_trip_chat` drains it.
- `ui/live.py` and its test are deleted, and `tests/test_supervisor_streaming.py` asserts the
  replacement invariant: nothing under `trip_planner/` imports Streamlit.

**Verified before building on it.** `runtime.stream_writer` does *not* raise from a tool worker
thread (it silently drops when the nested run is not streamed), and a writer captured from the graph
*does* raise — `Called get_config outside of a runnable context` — because it resolves its config from
a context variable that does not cross threads. That is why the tools use the runtime's writer and the
forwarding happens in the node.

**Risks, realised and handled.** The retry added in 1.3 turned out to retry a specialist's
`ValueError` too, so a deterministic failure ("no eligible stay") cost a second attempt — under the
supervisor, model calls. `ToolRetryMiddleware` is now configured with `retry_on` that excludes
`ValueError`, and a test pins one attempt. The signature change also broke the two tests that invoked
a delegation tool directly (`runtime` is injectable only inside a graph); that coverage moved to the
real `dispatch_with_supervisor` path, which is stronger than what it replaced.

**Done when.** No code outside the LangGraph run touches a worker thread, and `ui/live.py` is
deleted. *Shipped.*

### Wave 2 — Injection and result flow

Medium risk: touches how dependencies and results move, but not what the graph decides.

#### 2.1 Build the supervisor once, inject through `ToolRuntime` (M) — shipped

**Problem.** `create_agent` ran on every dispatch and every revision round, and the graph was
compiled on every request. Both existed only because the tools were closures over one run: the
specialist instances, the brief, the ports and the memory store were all captured at build time.

**Official mechanism.** `create_agent(..., context_schema=...)` plus `runtime: ToolRuntime[Ctx]`
inside the tool, where `runtime.context` is the injected object. Both `invoke` and `stream` accept
`context=`.

**Shipped.** The tools are now module-level and take no run-specific state:

- `DelegationContext` (`supervisor.py:130-146`) carries the brief, the round, the ports, the memory
  store, the run's specialist instances by name, and the two collections the tools fill: `proposals`
  and `requests`.
- `ASK_TOOLS` and `REVISE_TOOLS` (`supervisor.py:308-309`) are one tool per specialist, built once.
  A tool resolves *which* specialist to call from `runtime.context`, which is what lets the same tool
  object serve every run — and what `test_each_run_uses_its_own_specialists` pins.
- The revision path fits the mechanism better than it looked: instead of building one tool per
  *pending request*, all five revision tools exist and each looks its request up in the context,
  returning "nothing pending" for a specialist that has none. The completion check is on the pending
  set, so "delegated 0 of 1" still means what it did.
- `_dispatch_agent()` and `_revision_agent()` are `lru_cache`d (`supervisor.py:312-347`): cached
  rather than module-level because building them needs credentials, and the app must still import and
  run with none. `tests/conftest.py` clears them per test, since the fixtures replace the model route.

**Why the guarantee survives.** The brief still comes from the graph, not from the model's messages:
`DelegationContext` is constructed by the caller. The boundary is the same one the closure drew, now
expressed the documented way — and a test asserts the tools still expose an empty schema, so the
model still cannot restate the request.

**Tests.** `test_the_supervisor_agent_is_built_once` (a spy on `create_agent`: one construction, two
runs, and the second run really ran) and `test_each_run_uses_its_own_specialists` (two runs, two
different instances of the same specialist name, each invoked by its own run). `tests/conftest.py`
gives every test a fresh cached agent, which is the contract the caching added.

**Done when.** Per-run state reaches the tools through the runtime, and the agent is not rebuilt per
round. *Shipped.*

#### 2.2 Put worker results in graph state instead of a side channel (M/L) — shipped

**Problem.** A mutable dict (`shared_extras`, then `TripRun.extras`) carried the traces and the
candidate stays from the specialists to `build_plan`, and the supervisor collected its proposals in
another dict on the run context. The official docs name the consequence of results living outside the
graph ([Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)):

> Because subagents are called inside tool functions, LangGraph cannot statically discover them. This
> means `get_state` with `subgraphs` will not return subagent state.

That is why the trace bug was hard to see: the data that proves which path ran was not in anything a
checkpoint, Studio, or `get_state` could show.

**Official mechanism.** A custom state key on the nested agent (`state_schema=`), written by the tool
through `Command(update=...)` with `InjectedToolCallId`, and the outer node copying the agent's final
state into its own.

**Shipped.** Both halves, with one design change that made it simpler than planned:

- `SupervisorState` (`supervisor.py:152-165`) adds `proposals`, `traces` and `stay_choices` to the
  agent's state, each with a reducer, because `ToolNode` runs a batch of tool calls on a thread pool.
  The delegation tools return a `Command` carrying the proposal, that specialist's traces and its
  candidates, plus their own `ToolMessage` for the call they are answering.
- The node reads the agent's final state rather than a dict: `_run_agent` streams
  `stream_mode=["custom", "values"]` -- custom for progress, values for the result.
- `AgentContext.extras` became **per invocation** instead of per run. Each specialist gets a fresh
  dict and the caller turns what came back into state, so "what did this specialist report" is a
  value rather than a subtraction from a run-long accumulator. No specialist changed: they still
  write to `ctx.extras`, only its lifetime did.
- The outer `State` gained `traces` (`operator.add`) and `stay_choices`
  (`contracts.merge_choices`), and `TripRun.extras` is gone.

**Two details the plan flagged, and how they landed.** A tool returning a `Command` that touches
`messages` must include its own `ToolMessage` for `tool_call_id` -- `_report` does. And **arrival
order is not stable** with parallel appends, so the registration-order step stayed, now deduping as
well: a model that asks for the same specialist twice gets two entries in state and one section in
the plan (`test_a_repeated_tool_call_is_deduped_but_every_run_is_reported`).

**What it buys.** `_GRAPH.stream(..., stream_mode="values")` now returns a state whose `traces` say
which specialists fell back and whose `stay_choices` say what the traveller can pick
(`test_worker_results_live_in_graph_state`). When 3.1 adds a checkpointer, those survive a pause.

**Tests.** The two above, plus `test_a_specialist_sees_only_its_own_extras`. The existing trace,
decision and negotiation suites all still pass unchanged, which is the useful signal: the transport
changed and the observable plan did not.

**Done when.** A run's traces and candidates are in graph state, and no dict on the run context
carries them. *Shipped.*

#### 2.3 Compile the outer graph once (S) — shipped

**Problem.** `run_orchestrator` compiled per request. Nothing about a run was safe to share, because
every node closed over its own options.

**Official mechanism.** LangGraph injects per-run data into a node through `Runtime.context`: a node
declares `runtime: Runtime[TripRun]` and reads `runtime.context`, and both `invoke` and `stream`
accept `context=`.

**Shipped.** `TripRun` (`workflow.py:273-288`) holds everything a run needs — specialists, the name
index, ports, memory, the round limit, the decisions, and the per-run `extras` scratch. Every node
that needs it takes `runtime: Runtime[TripRun]`, and `_GRAPH` is compiled once at import
(`workflow.py:563`). `create_orchestrator_graph()` no longer takes options at all.

Two consequences worth recording:

- The round limit moved into the graph state (`state["max_rounds"]`) because a conditional-edge
  function only receives the state, and routing needs the limit. It is per-run data, so state is a
  fair home for it.
- `extras` had to become per-run for real. If it had stayed where it was, traces would accumulate
  across runs and the UI would show every specialist twice;
  `test_two_runs_do_not_share_their_context` runs the same trip twice and asserts five traces each,
  not ten.

**Risks.** A shared graph means a bug in per-run plumbing leaks between trips rather than between
requests — which is why the isolation test exists. Concurrent runs are safe because each passes its
own `TripRun` through `context=`, and the tools only write to the context they were handed.

**Tests.** `test_the_graph_is_compiled_once_and_shared` (patching the factory proves the entry points
do not call it) and the isolation test above.

**Done when.** The default path compiles once per process. *Shipped.*

### Wave 3 — Persistence and human in the loop

The real capability gap, and the only wave that changes product behaviour. Do it after Wave 2: state
that lives in closures cannot be checkpointed.

#### 3.1 Checkpoint the outermost graph (M)

**Problem.** `graph.compile()` has no checkpointer (`workflow.py:558`), so there is no persistence,
no resume after a crash, no `get_state`, and no way to pause.

**Official mechanism.** Compile the **outermost** graph with a checkpointer and invoke with a
`thread_id`; leave nested agents without one so they inherit the parent's
([Migrate from langgraph-supervisor](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor),
"Requirements for interrupt propagation"). The invoke signature already accepts
`context`, `durability` and `stream_mode` in this version.

**Target.** `graph.compile(checkpointer=InMemorySaver())`, invoked with
`config={"configurable": {"thread_id": brief.tripId}}`. `InMemorySaver` matches the current
in-process memory seam; a durable saver is a later, separate decision. Emit `durability="sync"` while
the resume path is being built, so a crash cannot lose the pause.

**Risks.** Checkpointer + parallel tool calls means concurrent writes to the same thread; LangGraph
handles it, but a plan that half-writes is now possible and needs a test. Memory grows per thread —
the existing `InMemoryStore` has the same unbounded property, so this does not make that worse but
does add a second place to bound.

**Tests.** Two runs with the same `thread_id` see the same checkpointed state; a run with a fresh
`thread_id` does not; state survives an exception between nodes.

**Done when.** `get_state(config)` returns the proposals and traces for a finished trip.

#### 3.2 Interrupt where a human is already required, first (M)

**Problem.** A traveller's decision is applied by re-running the whole graph
(`decisions.py:49-56`): five specialists, up to three rounds, to change one stay. And an unresolved
escalation is only *reported* (`workflow.py:259-268`) — nothing pauses.

**Official mechanism.** `interrupt()` inside a node, resumed with `Command(resume=...)`. It
propagates up from nested `create_agent` layers to the outermost graph.

**Two options, and they are not equally good.**

- **3a (recommended).** Interrupt only where the plan already says a human must decide: the
  `escalation` checkpoint. Blocking is correct there — the plan is over the red line or did not
  converge. On resume, route straight to a targeted revision round instead of re-dispatching, so the
  human's change costs one specialist, not five.
- **3b.** Interrupt on `confirm_choice` (stay choices) as well. This **changes the product
  contract**: `_choice_checkpoints` is explicitly non-blocking today ("a traveller who ignores them
  still gets a complete plan", `workflow.py:199-206`). Making it a pause is a defensible product
  decision, but it is a decision, not a refactor — and it should be argued in `docs/streamlit-ui.md`,
  not smuggled in with a state-model change.

**Implementation notes.** `interrupt()` re-executes its node from the top on resume, so put it in a
dedicated node whose first statement is the interrupt, and keep the expensive work either before or
after that node, never straddling it. The resume value must be validated like any other input
(`decisions.py:41-44` already refuses an option the checkpoint never offered — keep that).

**Tests.** An escalating plan pauses with `__interrupt__` and does not re-dispatch on resume; a
resumed escalation runs exactly one specialist; a plan with no escalation never interrupts; the
non-blocking stay-choice path is unchanged until 3b is decided.

**Done when.** 3a is merged with the "one specialist, not five" property pinned by a test.

#### 3.3 Keep preference promotion on top of the interrupt (S)

`interrupt` and this project's memory seam are complementary, not alternatives: the interrupt pauses
the run, `set_long_term` makes the answer persist into the next conversation. Whichever of 3a/3b is
chosen, `apply_decision` should keep writing the preference — and where both exist, one code path
must own the write, or a choice will be recorded twice with two different sources.

**Done when.** One function records a decision, whether it arrived through the UI form or a resume.

### Wave 4 — Naming, consolidation, and latency

Optional, and worth doing only after Waves 1-3 settle.

#### 4.1 Say "agent" only where a model decides (S)

Three specialists are single structured calls and two are calculators; the docs are clear that a
single agent with the right prompt is often the right answer, and this project's honest description
is "one agent, three generator calls, two calculators, and a deterministic reconciler". Consider
naming them `Worker`/`Section` in code and keeping "agent" for the supervisor, or stating the
distinction once in `docs/agent-architecture.md`. This is a documentation risk, not a runtime one:
the current README's "five specialist agents research it in parallel" is only true on the supervisor
path (see 4.3).

#### 4.2 Do not adopt `SubAgentMiddleware`/Deep Agents yet (no work)

`deepagents` offers `SubAgentMiddleware` and a `task` tool off the shelf
([Prebuilt middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in#subagent)).
It would replace ~150 lines of `supervisor.py`, at the cost of the two invariants that make this
project's output checkable: the supervisor would decide **what** each subagent is told (1.2), and
results would flow through the framework's own convention rather than through validation the
deterministic layer controls. Revisit only if the worker count grows past what hand-written tools
can carry.

#### 4.3 Parallelise the deterministic dispatch (S)

`deterministic_dispatch` is a sequential comprehension (`workflow.py:374`), so with no model the
five independent specialists run one after another. They are pure over `(brief, ctx)`, and after
`db59f0e` the progress sink is thread-safe, so a bounded `ThreadPoolExecutor` is now safe. Watch the
shared `ctx.extras` writes (`specialists/base.py:86`, `specialists/accommodation.py:225`): appends
and `update` calls from several threads need to be reviewed, or replaced by 2.2 first. Also fix the
README claim either way: parallelism on the supervisor path comes from the model batching tool calls,
not from the graph.

## What not to change

- **The deterministic conflict and budget rules** (`workflow.py:104`). They are the reason the
  negotiation converges; the docs' own performance tables show the model-driven patterns costing more
  calls for less control.
- **The fallback chain.** No official middleware replaces "degrade to validated deterministic output
  rather than fail the request"; keep it as the last line under every wave above.
- **The `Specialist` protocol.** Framework-neutral workers are why the same code runs in `pytest`
  with no model, and why two of them are not allowed to price anything.
- **Decision-as-preference** (3.3). It is a product feature the framework does not provide.

## Verification plan

Per wave, in order:

1. `uv run pytest -q` — 87 tests today; every item above names the tests it adds.
2. `uv run ruff check .` and `uv run ruff format --check .` — both clean today.
3. Run the app with a key and without one, and diff the observable plan for the same brief. The
   offline path is the one CI exercises, and this project has already been bitten twice by that
   (`docs/debugging-log.md` entries #10 and #11).
4. After Wave 3, one manual pause/resume through the UI, because no unit test proves the Streamlit
   session survives an interrupt.

## References

- [Multi-agent patterns](https://docs.langchain.com/oss/python/langchain/multi-agent)
- [Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
- [Migrate from langgraph-supervisor](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor)
- [Prebuilt middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
