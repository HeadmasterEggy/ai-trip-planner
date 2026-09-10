# Programmatic API

There is no HTTP layer. The TypeScript implementation exposed `POST /api/chat` because its UI was a
separate process; here Streamlit imports the planner directly, so the public surface is Python.

Everything below runs without a provider key.

## `run_trip_chat` — one conversational turn

The entry point the UI uses. A message becomes an explicit patch of the brief, the orchestrator
re-plans, and a reply is written from the resulting plan.

```python
from trip_planner.chat import run_trip_chat
from trip_planner.contracts import ChatRequest
from trip_planner.demo import DEMO_BRIEF

response = run_trip_chat(
    ChatRequest(tripId="trip-demo", message="make it 4 people", brief=DEMO_BRIEF)
)
response.reply  # str, in the language of the message
response.plan  # TripPlan
```

`brief` is optional; without it the demo brief is used. Passing the latest brief is what lets a
stateless caller apply an incremental edit.

Two seams exist for testing, both keyword-only:

```python
run_trip_chat(request, options, extractor=FixedExtractor(), reply_generator=lambda p: "ok")
```

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
        on_progress=lambda event: print(event.agent, event.type, event.round),
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
| `on_progress` | Called as each specialist starts, completes or fails. |

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
`ProposalItem`, `AgentProposal`, `RevisionRequest`, `TripSection`, `HitlCheckpoint`,
`NegotiationRound`, `TripPlan`, `ChatRequest`, `ChatResponse`. Ports are protocols in
`trip_planner.ports`.
