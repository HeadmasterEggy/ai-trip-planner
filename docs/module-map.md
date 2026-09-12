# Module map

Where each concern lives, and the rule that keeps it there.

```text
streamlit_app.py                  entry point (Streamlit Cloud looks for this name)
assets/                           the logo: as supplied, and the render it is served as
src/trip_planner/
  contracts.py                    Pydantic models crossing every boundary
  ports.py                        MapsPort, BookingPort, MemoryStore protocols
  demo.py                         a complete example trip, for scripts and tests
  models.py                       provider routing and structured-output adaptation
  budget.py                       USD roll-up in cents, negotiation and escalation thresholds
  memory.py                       short-term chat turns, long-term preferences
  chat.py                         message -> trip patch -> ask or plan -> reply
  supervisor.py                   typed delegation tools and the two supervisor loops
  workflow.py                     the LangGraph state machine
  specialists/                    the five agents, one module each
  tools/                          maps and booking adapters, and the gateway that joins them
  ui/                             presentation tokens and rendering helpers
    theme.py                      the palette and the one injected stylesheet
    render.py                     plan -> HTML fragments, no Streamlit
    history.py                    the rail's list and the trip cards, no Streamlit
tests/                            behaviour tests, no network
docs/                             architecture, orchestration, UI, observability, debugging
```

## The rules that hold this shape

**Dependency injection, not imports.** A specialist receives `ctx.tools` and `ctx.mem` through
`AgentContext`. It never imports a concrete adapter, which is why a unit test can pass a fake and
why `USE_MOCK_TOOLS` is a one-line switch rather than a code change.

**Ports live with the contracts.** `ports.py` sits beside `contracts.py`, not beside the adapters
that implement them. The core names an interface; only `tools/` names an HTTP client. An external
API can change without touching a specialist.

**`ui/` imports the planner; the planner never imports `ui/`.** That is what lets `pytest` and a
plain script run the whole system without Streamlit installed at all.

**Specialists never call each other.** The workflow owns *when* anything runs. A specialist that
needed another one's output would make the round structure meaningless — that is what the conflict
and revision cycle is for.

**Anything crossing a model boundary is validated.** Specialists return Pydantic models. A draft
that fails validation is rejected and the specialist falls back, so a bad response degrades one
section rather than corrupting the plan.

## Where to add things

| Change | Goes in |
| --- | --- |
| A new specialist | `specialists/`, then register it in `specialists/__init__.py` |
| A new external service | a protocol in `ports.py`, an adapter in `tools/` |
| A new field on the plan | `contracts.py` first — the UI and workflow both read it from there |
| A new conflict rule | `detect_conflicts` in `workflow.py` |
| A new view | `ui/render.py` as a function returning HTML, so it can be tested |
