# Agent architecture

## Runtime model

```text
trip brief
  -> LangGraph workflow
     -> five specialists, each with a role definition and an output schema
        -> itinerary, transport, accommodation, destination guide, dining
     -> validated proposals
  -> conflict detection, targeted revision, HITL and the aggregated plan
```

A specialist owns a durable role: its model routing, its tools, its output schema and the rules it
must not break. The workflow owns everything about *when* a specialist runs. Specialists never call
each other.

## The Specialist protocol

One immutable entry point serves both the initial plan and every revision:

```python
def invoke(brief: TripBrief, ctx: AgentContext, revision: RevisionRequest | None) -> AgentProposal
```

The orchestrator therefore does not need to know whether a specialist reasons with a model or
computes deterministically, which is what lets transport and accommodation stay as calculators
while the other three call a model.

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

## Provider quirks worth knowing

- **DeepSeek rejects LangChain's default json_schema response format** with
  `This response_format type is unavailable now`. Structured output goes through tool calling
  (`method="function_calling"`).
- **DeepSeek V4's thinking mode then rejects that tool call** with
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

## Fallbacks

Every specialist has a deterministic fallback, and the app runs end to end with no API key at all.
That is not only an offline convenience: it means a provider outage degrades the plan rather than
failing the request, and it keeps the conflict and escalation paths testable without network.

Which path produced a section is recorded in its assumptions (`Planner source: model.` or
`Planner source: deterministic fallback.`), so a fallback is visible rather than silent.
