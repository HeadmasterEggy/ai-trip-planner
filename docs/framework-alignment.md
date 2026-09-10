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

Four imports carry the whole framework surface:

| Import | Where | Role |
| --- | --- | --- |
| `ChatOpenAI` | `models.py:26` | provider + structured output |
| `create_agent` | `supervisor.py:22` | the one real agent loop |
| `tool` | `supervisor.py:23` | specialist delegation |
| `StateGraph`, `START`, `END` | `workflow.py:23` | deterministic orchestration |

`create_agent` itself returns a `CompiledStateGraph` built from `StateGraph` and `ToolNode`
(`langchain/agents/factory.py:26,28`), so the supervisor is a nested graph inside the `dispatch`
node of the outer one. The specialists are **not** LangChain agents: three do a single
structured-output call and two are deterministic calculators, behind a framework-neutral
`Specialist` protocol (`specialists/base.py:19-39`).

## Conformance review

| Dimension | Official | Here | Verdict |
| --- | --- | --- | --- |
| Supervisor | `create_agent` + one tool per worker | `supervisor.py:163` | Conformant |
| Outer control | custom `StateGraph` for deterministic steps | `workflow.py:451-462` | Conformant, and what the guide recommends |
| Worker state | stateless per invocation (isolated) | pure `invoke(brief, ctx, revision)` | Conformant |
| Parallelism | the main agent may call several subagents in one turn | models batching tool calls, `ToolNode` runs them on a pool | Conformant |
| Structured output | provider-native / json_schema / function calling | `method="function_calling"` (`models.py:117`) | Conformant (required for DeepSeek) |
| Tracing | LangSmith | `@traceable` on `detect_conflicts` | Conformant |
| **Delegation input** | the supervisor decides **what** each worker is told | no input at all, and deliberately so: the worker derives its prompt from the brief | **Deliberate** |
| **Worker results** | final message, or `Command`/`InjectedToolCallId` back into graph state | closure side channel, `shared_extras` | **Deviation** |
| **Dependency injection** | `context_schema` + `ToolRuntime`, agent built once | closures, agent rebuilt per round | **Deviation** |
| **Human in the loop** | `checkpointer` + `interrupt()` + `Command(resume=...)` | plan records + full re-run | **Gap** |
| Resilience | `ModelRetry` / `ToolRetry` / `ToolError` / `ModelFallback` / call limits | one corrective retry, round limit, deterministic fallback | **Partial** |

## What is deliberately different

These are load-bearing, and the strategy below must not sand them off:

1. **The supervisor cannot alter trip facts.** `brief`, `ctx`, `mem` and `tools` are captured when the
   tools are built (`supervisor.py:69-90`), so the deterministic rules downstream validate the
   traveller's request rather than a model's paraphrase of it.
2. **Revision requests are immutable.** One tool per validated `RevisionRequest`
   (`supervisor.py:94-141`): the supervisor routes a constraint, it cannot rewrite one.
3. **Deterministic fallbacks at every layer.** A model that fails schema validation, a supervisor
   that fails or under-delegates, and a graph that runs too long all end in the deterministic path
   (`models.py:127`, `workflow.py:321-337`, `workflow.py:382-386`).
4. **A decision is a preference, not an override.** `apply_decision` writes to long-term memory
   (`decisions.py:49`), which is why a choice survives the next message — something a one-shot
   `interrupt` would not give.

Nothing in the strategy below weakens (1)-(3). Where it touches (4), it does so deliberately and
says what is gained and lost.

## Improvement strategy

Effort is S (hours), M (about a day), L (more than a day) for one developer already familiar with
the code. Waves are ordered by risk, not by value: Wave 1 cannot break the plan, Wave 3 can.

**Progress.** 1.1 and 1.2 shipped together: the routing map now names the six roles that actually
call a model, and the delegation tools no longer take an argument. The rest is open.

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

#### 1.3 Adopt the official resilience middleware (M)

**Problem.** Two kinds of failure are handled by hand, and one is not handled at all:

- Schema failures: one corrective retry (`models.py:127-131`) — fine, keep.
- Transient provider failures: **no retry**. A timeout or 429 drops straight to the deterministic
  fallback, which is a silent downgrade of the whole section.
- Loop bounds: the round limit is ours (`workflow.py:443`), and the only bound on the supervisor's
  own tool loop is LangGraph's default recursion limit, whose failure surfaces as an exception
  caught by a broad `except` (`workflow.py:335`).
- Provider fallback: the MiniMax branch in `models.py:80-89` is unreachable, because every entry in
  `MODEL_ROUTING` points at DeepSeek.

**Official mechanism.** Provider-agnostic middleware
([Prebuilt middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)):
`ModelRetryMiddleware`, `ToolRetryMiddleware`, `ToolErrorMiddleware`, `ModelFallbackMiddleware`,
`ModelCallLimitMiddleware`, `ToolCallLimitMiddleware` — all importable from
`langchain.agents.middleware` in the installed version.

**Target.** Pass middleware to the supervisor's `create_agent`:

```python
create_agent(
    model=model,
    tools=tools,
    system_prompt=DISPATCH_PROMPT,
    middleware=[
        ModelRetryMiddleware(max_retries=2, backoff_factor=2.0),
        ToolRetryMiddleware(max_retries=1, on_failure="error"),
        # Keep the delegation failure visible rather than killing the batch:
        ToolErrorMiddleware(
            on_error=lambda exc, req: f"{req.tool_call['name']} failed: {type(exc).__name__}"
        ),
        ToolCallLimitMiddleware(run_limit=len(tools) * 2),
        ModelCallLimitMiddleware(run_limit=6),
    ],
)
```

Two notes from the docs that matter here: `ToolRetryMiddleware` must sit **inner** (earlier) and be
configured `on_failure="error"` for exceptions to reach `ToolErrorMiddleware`; and the
`on_error` handler should name the exception type rather than echo its message, to avoid leaking
internals to the model. `ToolErrorMiddleware` needs `langchain>=1.3.14`; this project has 1.4.0.

**Why this is more than tidiness.** The frozen progress panel fixed in `db59f0e` was a tool exception
escaping delegation and collapsing the run to the deterministic path. `ToolErrorMiddleware` is the
framework-level version of that fix: a failing tool becomes a `ToolMessage` the model can react to,
instead of taking the batch with it.

**Risks.** A retry policy that is too eager multiplies cost on a hard failure; keep retries at 1-2
with backoff, and keep the existing deterministic fallback as the final answer. Middleware order is
semantic — document it in a comment, because it is not obvious from the call site.

**Tests.** A fake model that fails once and then succeeds must produce a model-backed section (not a
fallback). A tool that raises must leave the supervisor able to finish with the remaining
specialists. A model that exceeds the call limit must still yield a complete plan via the
deterministic path.

**Done when.** No transient provider error reaches the user as a silently downgraded section, and the
supervisor's loop bounds are declared rather than inherited.

#### 1.4 Replace the custom progress callback with official streaming (M)

**Problem.** Progress travels through `OrchestratorOptions.on_progress`, a hand-rolled callback that
the specialists call from whatever thread they happen to run on (`workflow.py:308-316`,
`supervisor.py:76,118`). That design is what produced the worker-thread bug: the callback wrote
Streamlit widgets with no `ScriptRunContext`, raised inside the tool, and took the whole delegation
with it. The shipped fix (`ui/live.py`) binds the run context correctly and is tested, but the
fragility is structural: any consumer of `on_progress` must know which thread it is called on.

**Official mechanism.** LangGraph streams execution: `stream`/`astream` with `stream_mode`, and
`stream_events`/`astream_events`, both present on the compiled graph in this version. A tool can also
push structured events with `runtime.stream_writer` (`ToolRuntime.stream_writer`), which is the
supported way for a worker to narrate what it is doing.

**Target.** Keep the public shape (`ProgressEvent`) and change the producer: expose
`run_orchestrator_events(brief, options) -> Iterator[ProgressEvent]` that drives
`graph.stream(..., stream_mode=["updates", "custom"])` and translates node/tool events into the
existing `ProgressEvent` type. The UI consumes the iterator on the script thread; `ui/live.py` and
`on_progress` then have no reason to exist for the supervisor path.

**Risks.** The translation layer is new surface: map each specialist to its tool name
(`ask_<agent>_specialist`/`revise_<agent>_specialist`), preserve the round suffix the UI shows, and
keep `tests/test_planner.py::test_the_graph_runs_every_specialist_and_emits_progress` passing. The
deterministic path emits no tool events, so it needs an explicit shim (run the node and yield
synthetic events) or `stream_mode="updates"` on the outer graph, which reports node completion.

**Order.** Do (1.3) first: with `ToolErrorMiddleware` in place, a translation bug degrades progress
rather than the plan.

**Tests.** A test that the iterator yields exactly one `agent_started`/`agent_completed` pair per
specialist per round, on both paths, with no thread-context requirement.

**Done when.** No code outside the LangGraph run touches a worker thread, and `ui/live.py` is deleted.

### Wave 2 — Injection and result flow

Medium risk: touches how dependencies and results move, but not what the graph decides.

#### 2.1 Build the supervisor once, inject through `ToolRuntime` (M)

**Problem.** `create_agent` is called on every dispatch and every revision round
(`supervisor.py:157,197`), and the outer graph is compiled on every request
(`workflow.py:471`). Both exist only because the tools are closures over per-run state. Compiling a
graph is not free, and the pattern inverts the documented one, where the agent is built once and
reads what it needs from the runtime.

**Official mechanism.** `create_agent(..., context_schema=...)` plus `runtime: ToolRuntime[Ctx]`
inside the tool, where `runtime.context` is the injected object (verified fields: `state`, `context`,
`config`, `store`, `tool_call_id`, `stream_writer`, ...). The outer `invoke` accepts `context=`.

**Target.**

```python
@dataclass
class TripContext:  # passed per run, never written by the model
    brief: TripBrief
    round: int
    tools: ToolGateway
    mem: Any


@tool(_tool_name("ask", specialist.name), args_schema=_Delegation, description=...)
def delegate(runtime: ToolRuntime[TripContext]) -> str:
    ctx = runtime.context
    proposal = specialist.invoke(ctx.brief, _agent_context(ctx), None)
    ...
```

```python
supervisor = create_agent(
    model=model, tools=DELEGATION_TOOLS, system_prompt=DISPATCH_PROMPT, context_schema=TripContext
)
...
supervisor.invoke({"messages": [...]}, context=TripContext(brief, 1, tools, mem))
```

The per-specialist tool list still varies by run, so keep a small factory that builds the tool list
per run but reuse a compiled agent when the tool set is identical — or accept building only the
tools per run and compile once per tool-set shape.

**Why the guarantee survives.** The brief still comes from the graph, not from the model's messages;
`TripContext` is constructed in `dispatch`, which the model cannot reach. This is the same boundary
as the closure, expressed the documented way.

**Risks.** `context_schema` is per-`create_agent`, so a run-varying tool list limits how much can be
cached; measure before optimising. The revision path needs a different immutable request per target,
which fits `context_schema` poorly — keep those tools closure-built per round and say why in a
comment.

**Tests.** The delegation tools report identical behaviour for identical briefs before and after;
`create_agent` is constructed at most once per run (count with a spy).

**Done when.** Per-run state reaches the tools through the runtime, and the supervisor graph is not
rebuilt on every revision round.

#### 2.2 Put worker results in graph state instead of a side channel (M/L)

**Problem.** `shared_extras` (`workflow.py:301`) is a mutable dict captured by the closures. Traces
and stay choices are written into it by specialists (`specialists/base.py:86`,
`specialists/accommodation.py:225`) and read out in `build_plan` (`workflow.py:436-439`). The
official docs call out the consequence of results living outside the graph
([Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)):

> Because subagents are called inside tool functions, LangGraph cannot statically discover them. This
> means `get_state` with `subgraphs` will not return subagent state.

That is why the trace bug was hard to see: the data that proves which path ran is not in anything a
checkpoint, Studio, or `get_state` can show.

**Official mechanism.** A custom state key on the nested agent (`state_schema=`), written by the tool
via `Command(update=...)` with `InjectedToolCallId`, and the outer node copying the agent's final
state into its own.

**Target.**

```python
class SupervisorState(AgentState):
    proposals: Annotated[list[AgentProposal], operator.add]
    traces: Annotated[list[SpecialistTrace], operator.add]


@tool(_tool_name("ask", specialist.name), args_schema=_Delegation)
def delegate(
    tool_call_id: Annotated[str, InjectedToolCallId], runtime: ToolRuntime[TripContext]
) -> Command:
    proposal = specialist.invoke(...)
    return Command(
        update={
            "proposals": [proposal],
            "messages": [ToolMessage(content=proposal.summary, tool_call_id=tool_call_id)],
        }
    )
```

Two details that will otherwise cost an afternoon:

- The `Command` updates the **nested agent's** state, not the outer graph's. The `dispatch` node must
  read the returned state and return `{"proposals": ...}` for the outer graph to see it.
- A tool returning a `Command` that touches `messages` must include its own `ToolMessage` for
  `tool_call_id`, or the agent's message history is left inconsistent.

**Reducer requirement.** The outer `State` is a plain `TypedDict` with overwrite semantics
(`workflow.py:87-94`). Parallel tool calls appending to the same key need a reducer
(`Annotated[list[...], operator.add]`), and the existing "restore registration order" step
(`supervisor.py:180`) must stay — with concurrent appends the arrival order is not stable, and
section order in the plan must not become nondeterministic.

**Risks.** Thread-safety of the reducer under parallel tools (LangGraph handles this, but the tests
must prove it); the trace tests (`tests/test_traces.py`) assert ordering and count, so they are the
safety net.

**Tests.** After a run, `plan.traces` and `plan.hitl` choices are identical to the side-channel
version; the section order matches `ALL_SPECIALISTS` under a run that delegates in a different order;
`shared_extras` no longer exists.

**Done when.** A checkpointed run exposes proposals and traces in graph state, and removing
`shared_extras` changes no test outcome.

#### 2.3 Compile the outer graph once (S)

**Problem.** `run_orchestrator` compiles per request (`workflow.py:471`). That is a consequence of
per-run options (injected specialists, decisions, callbacks).

**Target.** Split per-run inputs from construction: compile with a static config (checkpointer,
`context_schema`) at module or session level, and pass brief/decisions/context per `invoke`
(`invoke(..., context=...)`). Keep a compile-per-run escape hatch for tests that inject specialists,
since `OrchestratorOptions.specialists` is the deterministic seam.

**Tests.** An existing test that injects specialists still passes; a test that the module-level graph
is reused across two runs (identity, not timing).

**Done when.** The default path compiles once per process.

### Wave 3 — Persistence and human in the loop

The real capability gap, and the only wave that changes product behaviour. Do it after Wave 2: state
that lives in closures cannot be checkpointed.

#### 3.1 Checkpoint the outermost graph (M)

**Problem.** `graph.compile()` has no checkpointer (`workflow.py:462`), so there is no persistence,
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
escalation is only *reported* (`workflow.py:258-267`) — nothing pauses.

**Official mechanism.** `interrupt()` inside a node, resumed with `Command(resume=...)`. It
propagates up from nested `create_agent` layers to the outermost graph.

**Two options, and they are not equally good.**

- **3a (recommended).** Interrupt only where the plan already says a human must decide: the
  `escalation` checkpoint. Blocking is correct there — the plan is over the red line or did not
  converge. On resume, route straight to a targeted revision round instead of re-dispatching, so the
  human's change costs one specialist, not five.
- **3b.** Interrupt on `confirm_choice` (stay choices) as well. This **changes the product
  contract**: `_choice_checkpoints` is explicitly non-blocking today ("a traveller who ignores them
  still gets a complete plan", `workflow.py:198-204`). Making it a pause is a defensible product
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

`deterministic_dispatch` is a sequential comprehension (`workflow.py:318`), so with no model the
five independent specialists run one after another. They are pure over `(brief, ctx)`, and after
`db59f0e` the progress sink is thread-safe, so a bounded `ThreadPoolExecutor` is now safe. Watch the
shared `ctx.extras` writes (`specialists/base.py:86`, `specialists/accommodation.py:225`): appends
and `update` calls from several threads need to be reviewed, or replaced by 2.2 first. Also fix the
README claim either way: parallelism on the supervisor path comes from the model batching tool calls,
not from the graph.

## What not to change

- **The deterministic conflict and budget rules** (`workflow.py:103`). They are the reason the
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
