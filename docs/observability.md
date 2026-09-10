# Observability

## LangSmith

Tracing is environment-driven; no code changes are needed to turn it on.

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=ai-trip-planner
LANGSMITH_ENDPOINT=https://apac.api.smith.langchain.com
```

**The endpoint is not optional outside the US.** LangSmith keys are regional, and a key issued in
one region is rejected by the others with a bare `403 Forbidden` on ingest. Tracing failures do not
stop a run — the plan still completes — so a misconfigured region looks like silence rather than an
error, and an empty project is the only symptom. Match the endpoint to the host in your LangSmith
URL:

| Region | Endpoint |
| --- | --- |
| US (default) | `https://api.smith.langchain.com` |
| EU | `https://eu.api.smith.langchain.com` |
| APAC | `https://apac.api.smith.langchain.com` |

Each run produces one trace tree: every LangGraph node, every specialist per round, and every model
call with its inputs, outputs and token counts.

### Why two specialists carry explicit spans

Transport and accommodation are deterministic calculators and never call LangChain, so they would
be absent from an automatically instrumented trace — a five-agent system would appear as three.
`workflow.run_one` wraps each specialist in a `@traceable` span so the trace matches the
architecture.

`detect_conflicts` is traced for a different reason: when the negotiation fails to converge, the
question is always *which* conflict recurred and *what* constraint each round actually sent. That
is invisible from the model calls alone, because the interesting decision happens between them.

## Progress events

Independently of LangSmith, a run reports a `ProgressEvent` as each specialist starts, completes or
fails. It travels on the graph's custom stream:

```python
stream = run_orchestrator_stream(brief, OrchestratorOptions())
for event in stream:
    print(event.agent, event.type, event.round, event.error)
```

The Streamlit UI uses this for its live per-agent rows, iterating on its own thread — which is why no
specialist needs to know anything about the UI, or about which thread it was run on. It is also the
cheapest way to see round structure in a script: an event with `round=3` means that specialist was
sent back twice.

## What a non-converging run looks like

The demo brief currently runs the full three rounds and escalates. In the trace, this shows up as
the same conflict reason appearing in rounds 2 and 3 with the two costly specialists already at
their floor. That is a real limitation of the budget negotiation, not an instrumentation gap — see
the known limits in the README.
