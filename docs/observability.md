# Observability

## LangSmith

Tracing is environment-driven; no code changes are needed to turn it on.

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=ai-trip-planner
```

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

Independently of LangSmith, the orchestrator emits a `ProgressEvent` as each specialist starts,
completes or fails:

```python
run_orchestrator(brief, OrchestratorOptions(on_progress=handler))
```

The Streamlit UI uses this for its live per-agent rows. It is also the cheapest way to see round
structure in a script: an event with `round=3` means that specialist was sent back twice.

## What a non-converging run looks like

The demo brief currently runs the full three rounds and escalates. In the trace, this shows up as
the same conflict reason appearing in rounds 2 and 3 with the two costly specialists already at
their floor. That is a real limitation of the budget negotiation, not an instrumentation gap — see
the known limits in the README.
