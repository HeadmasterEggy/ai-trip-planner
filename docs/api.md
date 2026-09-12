# Programmatic API

There is no HTTP layer. The TypeScript implementation exposed `POST /api/chat` because its UI was a
separate process; here Streamlit imports the planner directly, so the public surface is Python.

Everything below runs without a provider key.

## `run_trip_chat` — one conversational turn

The entry point the UI uses. A message becomes an explicit patch of the trip, the patch is merged
into whatever the request already carries, and — once nothing required is missing — the orchestrator
re-plans and a reply is written from the resulting plan. While something is still missing the turn
asks for it instead and returns no plan.

```python
from trip_planner.chat import run_trip_chat
from trip_planner.contracts import ChatRequest
from trip_planner.demo import DEMO_BRIEF

response = run_trip_chat(
    ChatRequest(tripId="trip-demo", message="make it 4 people", brief=DEMO_BRIEF)
)
response.reply  # str, in the language of the message
response.plan  # TripPlan, or None when the turn had to ask for something
response.draft  # BriefPatch: what the trip looks like after this turn
```

A trip reaches the planner one of two ways, and a request may carry both — a complete `brief` wins:

| Field | Meaning |
| --- | --- |
| `brief` | A complete, validated `TripBrief`. The shortest route for a script, and for a caller that already holds one; the UI itself now sends only a `draft`. |
| `draft` | A `BriefPatch`: the fields stated so far, any of them optional. Send the previous response's `draft` back and the conversation accumulates. |
| `userId` | Who is talking. Read only when no complete `brief` carries an identity; long-term preferences are stored per user. |

Nothing is assumed when both are absent. An empty request is a legitimate first turn, and the reply
asks for the first missing field rather than planning a trip the caller never described:

```python
first = run_trip_chat(ChatRequest(tripId="t", message="Tokyo"))
first.plan  # None
first.draft  # BriefPatch(destination="Tokyo", ...)
first.reply  # "When would you like to travel? ... I'll also need ..."

second = run_trip_chat(
    ChatRequest(
        tripId="t", message="2026-10-01 to 2026-10-05, 2 people, budget $3000", draft=first.draft
    )
)
second.plan.brief.destination  # "Tokyo"
```

Only `destination`, `dates`, `groupSize` and `budgetTotal` are ever asked for; `nationality` changes
a visa note rather than the plan, so it is optional throughout. `contracts.missing_fields(draft)`
returns what is still open, and `contracts.draft_problem(draft)` returns the traveller-facing reason
a partial draft already cannot be planned — an end before its start, or more cities than nights — so
a bad span is reported the moment it is said instead of after three more questions.

Three seams exist for testing, all keyword-only:

```python
run_trip_chat(
    request,
    options,
    extractor=FixedExtractor(),
    reply_generator=lambda p: "ok",
    question_generator=lambda p: "ok",
)
```

`reply_generator` and `question_generator` are separate because the turns have opposite jobs; both
default to the routed `reply` model, and both fall back to deterministic local text when it fails.

## `run_orchestrator` — plan a brief directly

Skips intake and reply generation.

```python
from trip_planner.workflow import OrchestratorOptions, run_orchestrator
from trip_planner.specialists import ALL_SPECIALISTS

plan = run_orchestrator(
    brief,
    OrchestratorOptions(
        specialists=ALL_SPECIALISTS,  # explicit list = deterministic dispatch, no supervisor
        max_rounds=3,
    ),
)
```

`OrchestratorOptions` is the injection point for everything external:

| Field | Purpose |
| --- | --- |
| `specialists` | Passing a list skips supervisor delegation. This is the deterministic seam tests use, so a test never depends on a model choosing to call every tool. |
| `tools` | A `ToolGateway` of maps and booking ports. Pass fakes to avoid the network. |
| `mem` | A `MemoryStore`. |
| `max_rounds` | Negotiation round limit; default 3. |
| `decisions` | Decisions already made by the traveller, as preference key -> chosen id. |

## `run_orchestrator_stream` — the same plan, with progress

A caller that wants to watch the specialists work iterates the stream instead. The generator body runs
on the consumer's thread, so nothing has to know which thread a specialist ran on — under the
supervisor those are LangGraph's tool-pool threads:

```python
from trip_planner.workflow import OrchestratorOptions, run_orchestrator_stream

stream = run_orchestrator_stream(brief, OrchestratorOptions())
for event in stream:  # ProgressEvent(type, agent, round, error)
    print(event.agent, event.type, event.round)

plan = stream.plan  # None until the iterator is exhausted; one pass, one consumer
```

`ProgressEvent` lives in `trip_planner.contracts` and travels on the graph's custom stream: a node
writes it with `langgraph.config.get_stream_writer`, and a delegation tool with
`Runtime.stream_writer`. `run_orchestrator` is the same work with progress discarded, so a caller
that does not need it pays nothing.

`run_trip_chat_stream` is the chat-level equivalent, and `run_trip_chat` drains it:

```python
stream = run_trip_chat_stream(request, options)
for event in stream:
    ...
response = stream.response  # set once the iterator is exhausted
```

A turn that had nothing to plan is the same object with an empty event stream — no specialist ran —
so a caller never has to guess which of two response types it is holding. `stream.interrupt` is set
when the run paused for a human, and `stream.thread_id` is the thread to resume.

Feasibility is checked before any of that runs. `contracts.draft_problem(draft)` and
`contracts.brief_problem(brief)` return a traveller-facing reason, or `None`, and the entry points
raise `ValueError` with it rather than letting a specialist discover the problem four agents deep.
Chat intake calls them on every turn, so an impossible trip is refused with one message — an end
before its start, or more cities in the `&`-separated destination than the trip has nights — rather
than failing four agents deep.

## What comes back

`TripPlan` is the aggregate the UI renders:

```python
plan.sections  # one TripSection per specialist, with its AgentProposal
plan.estTotal  # USD, whole trip
plan.overrunPct  # (est - budget) / budget * 100; negative when under
plan.hitl  # pending human decisions
plan.negotiation  # per round: conflicts found, specialists re-planned
plan.round  # the round the plan was built from
```

`negotiation` is the only record of *why* a plan looks the way it does. The orchestrator discards
its intermediate state otherwise.

## Contracts

Everything crossing a boundary is a Pydantic model in `trip_planner.contracts`: `TripBrief`,
`BriefPatch`, `ProposalItem`, `AgentProposal`, `RevisionRequest`, `TripSection`, `HitlCheckpoint`,
`NegotiationRound`, `TripPlan`, `ChatRequest`, `ChatResponse`. Ports are protocols in
`trip_planner.ports`.

`TripBrief` is strict — every specialist depends on all of it — while `BriefPatch` is the same
fields with every one optional. That split is the conversational layer's: a dialogue arrives one
field at a time, so the draft it accumulates cannot be a `TripBrief` until nothing is missing.
`demo.py` still holds a complete example trip (relative to today) for scripts and tests; the UI no
longer starts from it.
