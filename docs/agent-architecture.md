# Agent architecture

## Runtime model

```text
user message
  -> chat intake: extract an explicit brief patch, re-validate the brief
     -> LangGraph workflow
        -> supervisor: choose which specialist tools to call
           -> itinerary, transport, accommodation, destination guide, dining
        -> validated proposals
     -> conflict detection, targeted revision, HITL and the aggregated plan
  -> a reply written from the plan, in the traveller's language
```

A specialist owns a durable role: its model routing, its tools, its output schema and the rules it
must not break. The workflow owns everything about *when* a specialist runs. Specialists never call
each other.

## One agent, three generations, two calculators

"Multi-agent" is a loose word for this system, so it is worth saying exactly where the agents are:

| Who | What it is | Decides |
| --- | --- | --- |
| `supervisor` | a real agent loop (`create_agent`) with one tool per specialist | **who** works, and when |
| `itinerary`, `destination-guide`, `dining` | one structured-output generation each, grounded in maps candidates | the content of their section, inside a schema |
| `transport`, `accommodation` | deterministic calculators over the booking and maps ports | nothing: a model never prices a leg or a stay |
| `workflow` | a deterministic state machine | conflicts, budget red lines, the round limit, escalation |

The name `Specialist` is kept for all five because that is the contract the orchestrator sees --
`invoke(brief, ctx, revision) -> AgentProposal` -- and because the five are interchangeable from
outside. It is not a claim that five models deliberate: two of them never call a model at all, and the
reconciler between them is arithmetic and clock comparisons rather than a negotiation.

## The Specialist protocol

One immutable entry point serves both the initial plan and every revision:

```python
def invoke(brief: TripBrief, ctx: AgentContext, revision: RevisionRequest | None) -> AgentProposal
```

The orchestrator therefore does not need to know whether a specialist reasons with a model or
computes deterministically, which is what lets transport and accommodation stay as calculators
while the other three call a model.

## The supervisor boundary

A named supervisor agent decides *which* specialists to call, through one typed tool per
specialist. Those tools capture this run's brief, memory store and tool gateway when they are
built, so the supervisor's only freedom is delegation.

That boundary is the whole point. A supervisor free to rewrite trip facts would make the plan
depend on a model's paraphrase of the request, and the deterministic budget and conflict rules
downstream would then be validating the paraphrase rather than what the traveller asked for.

Two things guard the result:

- A supervisor may legitimately skip a specialist. Any it skipped is run directly afterwards, so
  the plan always has all five sections rather than silently losing one.
- Revision tools are built one per pending request and are immutable: the request already names
  its target, and a tool that returns a proposal from a different specialist is rejected.

Both loops fall back to running every specialist directly when no model is configured or the loop
fails, which is why the graph still produces a plan offline. Injecting `specialists` explicitly
takes the same deterministic path, so a test never depends on a model choosing to call every tool.

## Chat intake

A message is turned into an explicit patch of the trip brief, then the orchestrator re-plans and a
reply is written from the resulting plan.

Extraction only changes fields the traveller actually stated. Inferring a date, a budget or a
destination they did not give is worse than asking, because the plan then drifts from the request
and nothing in the output says so. The prompt says as much, and every field in the model's wire
schema is nullable so "not mentioned" is expressible.

A bilingual local parser sits behind the model and handles the phrasings the UI suggests, in
English and Chinese. It runs when no extraction model is configured and when one fails, so the
conversation works with no API key.

Date ordering is checked once, when the patch is applied. Leaving it to the specialists would mean
a reversed date range fails five times with five different messages.

## Two kinds of specialist

**Deterministic calculators** — transport and accommodation. Fares, nightly rates, room counts and
multi-city segment splits are computed from the booking and maps ports. A model is never in a
position to invent a price, a property or a route.

**Grounded model agents** — itinerary, destination guide and dining. These reason with a model but
every place they name must come from the maps port, and the draft is validated before it is
accepted.

## Rules that hold at every model boundary

1. **Validate, do not trust.** Specialists return Pydantic models. A draft that fails validation is
   rejected and the specialist falls back, so a bad response degrades one section instead of
   corrupting the plan.
2. **Enforce grounding in code, not only in the prompt.** A location must be a candidate name
   copied exactly. Models reliably decorate names — appending the category, so
   `Mock attraction near Tokyo & Kyoto` came back as `Mock attraction near Tokyo & Kyoto (sight)`
   — and the validator discards the whole draft when they do. The rule is stated in both places
   because either alone is insufficient: the prompt alone is not binding, and the validator alone
   produces a silent fallback on every round.

   How the candidates are *rendered* matters as much as the instruction. Listing them as
   `- Mock sight near Osaka (sight)` and then asking for the name "copied exactly" is ambiguous,
   and the model copies the whole bullet. They are listed as labelled fields instead:

   ```
   - name: Mock sight near Osaka
     category: sight
   ```
3. **Restate schema limits in the prompt.** Both DeepSeek and MiniMax treat `maxLength` and
   `maxItems` as advisory. Structured-output extraction retries only a few times before giving up,
   so the first attempt has to be close.
4. **Keep the contracts stable.** `TripBrief`, `AgentProposal`, `ProposalItem` and `TripPlan` are
   the boundary the UI, the workflow and the specialists all agree on.
5. **Report what you cannot safely fix.** The itinerary checks map travel time between consecutive
   activities and reports what does not fit as a conflict, rather than shifting the times itself.
   It cannot see the transport legs on the same day, so a silent reschedule risks moving an
   activity onto one. A revision that introduces a new geography conflict falls back to the
   conservative plan and re-checks it.

## Provider quirks worth knowing

- **DeepSeek rejects LangChain's default json_schema response format** with
  `This response_format type is unavailable now`. Structured output goes through tool calling
  (`method="function_calling"`).
- **DeepSeek V4.1 Flash's thinking mode then rejects that tool call** with
  `Thinking mode does not support this tool_choice`, because tool-calling structured output forces
  a named tool. Thinking is disabled via `extra_body`, not `model_kwargs` — the latter hands the
  field to the OpenAI SDK as a keyword argument, which it rejects with
  `Completions.create() got an unexpected keyword argument 'thinking'`.
- **MiniMax runs two independent account systems.** Mainland-China keys work against
  `api.minimaxi.com` and return `401 invalid api key (2049)` against `api.minimax.io`, and vice
  versa. No GroupId is required.
- **MiniMax ignores a forced `tool_choice`.** It answers in prose with no tool call, which is
  indistinguishable from an auth failure at the call site. Only `tool_choice: "auto"` produces a
  well-formed call.
- **LangSmith keys are regional.** A key issued outside the US is rejected by the default endpoint
  with a bare `403` on ingest. Tracing failures never stop a run, so the only symptom is an empty
  project. See [observability](observability.md).

## A note on tool descriptions

LangChain builds a tool's description from its docstring. An f-string docstring is not a docstring
at all — Python evaluates it as an expression — so the description silently becomes empty and the
model has nothing to choose between. The delegation tools pass `description=` explicitly, and
ruff's `B021` catches the mistake if it comes back.

## Fallbacks

Every specialist has a deterministic fallback, and the app runs end to end with no API key at all.
That is not only an offline convenience: it means a provider outage degrades the plan rather than
failing the request, and it keeps the conflict and escalation paths testable without network.

Which path produced a section is recorded in its assumptions (`Planner source: model.` or
`Planner source: deterministic fallback.`), so a fallback is visible rather than silent.
