# AI Trip Planner

Five specialist agents negotiate a trip plan under a LangGraph orchestrator, with a Streamlit UI.

**[Live demo](#)** · Python 3.11+ · LangChain · LangGraph · Streamlit

## What it does

A trip brief goes to five specialists in parallel. Each returns a validated proposal. The
orchestrator then looks for conflicts across those proposals — budget overruns, and activities
scheduled on top of a transport leg — and re-runs only the specialists it needs to, up to three
rounds. Anything still unresolved is escalated to the traveller rather than quietly accepted.

```
START -> dispatch_specialists -> detect_conflicts
                                      | (conditional)
                         revise_conflicts <-> detect_conflicts
                                      |
                                 build_plan -> END
```

| Specialist | Role | Reasoning |
| --- | --- | --- |
| `itinerary` | Day plan | Model, grounded in map candidates |
| `transport` | Getting around | Deterministic calculator over booking + maps |
| `accommodation` | Stay | Deterministic calculator over booking |
| `destination-guide` | Customs, safety, packing | Model, grounded in map candidates |
| `dining` | Meal budget and venues | Model, grounded in map candidates |

Transport and accommodation are deliberately deterministic: a model may narrate a stay, but it
never prices one.

## Run it

```bash
uv sync
uv run streamlit run streamlit_app.py
```

It runs with no API keys and no network. Every specialist falls back to deterministic output and
the tools serve fixtures, so the whole loop — including conflict detection and escalation — is
exercised offline. Copy `.env.example` to `.env` to add live models.

```bash
uv run pytest        # 10 tests, no network
uv run ruff check .
```

## Design notes

**Everything crossing a model boundary is validated.** Specialists return Pydantic models, not
free text. A draft that fails validation is rejected and the specialist falls back, so a bad
response degrades one section instead of corrupting the plan.

**Grounding is enforced, not requested.** An activity's location must be a candidate name from the
maps port, copied exactly. The rule is stated in the prompt *and* checked in code, because models
reliably decorate names — appending the category to a place name silently invalidated every draft
during development.

**Schema limits are repeated in the prompt.** Both DeepSeek and MiniMax treat a JSON schema's
`maxLength` and `maxItems` as advisory. Structured-output extraction retries only a few times
before giving up, so the first attempt has to be close.

**MiniMax runs two independent account systems.** Mainland-China keys work against
`api.minimaxi.com` and return `401 invalid api key (2049)` against `api.minimax.io`, and vice
versa. It also ignores a forced `tool_choice`, answering in prose with no tool call — which is
indistinguishable from an auth failure at the call site.

**Conflict constraints name what to avoid and who holds it.** A revising specialist only ever sees
its own proposal. Handed bare clock times, the itinerary agent guessed, and in one round moved an
activity exactly onto the transport leg it was meant to avoid; the budget overrun oscillated
29.25% → 16.50% → 25.25% without settling. The constraint now reads
`on day 4 keep clear of 09:00-11:20, held by transport (Tokyo → Kyoto)`.

## Observability

Set `LANGSMITH_TRACING=true` with an API key and every run produces one trace tree: a span per
specialist per round, the conflict detector, and each LangGraph node. Transport and accommodation
never call LangChain, so they carry explicit spans — otherwise a five-agent system would show up
as three.

## Known limits

Budget negotiation is not yet effective. Once the itinerary contributes real costs, the demo brief
lands slightly over budget and the two costly specialists are already at their floor, so it runs
the full three rounds and escalates. The maps and booking adapters ship deterministic fixtures;
`USE_MOCK_TOOLS=false` switches maps to OpenStreetMap, and booking has no live provider.
