# Streamlit UI

`streamlit_app.py` sits at the repository root because Streamlit Community Cloud looks for that
name — the entry point is a naming convention rather than configuration.

The file is presentation only. It imports `trip_planner` and calls `run_orchestrator`; no planning
logic lives in it, which is why the same code runs from `pytest` or a script without importing
Streamlit at all.

## Per-agent progress

A planning run takes tens of seconds and involves five specialists across up to three rounds.
Showing one spinner for all of that hides the part worth watching, so the orchestrator accepts an
`on_progress` callback and emits an event as each specialist starts, finishes or fails:

```python
ProgressEvent(type="agent_started", agent="itinerary", round=1)
```

The UI opens one `st.empty()` placeholder per specialist before the run and repaints just that row
as events arrive. Because the stream is consumed inside a single script run, each row updates in
place:

```
✅ Day plan — Complete
✅ Getting around — Complete · round 3
✅ Stay — Complete · round 3
✅ Destination guide — Complete
✅ Food & dining — Complete
```

The round suffix is what makes the negotiation visible: it shows which specialists were sent back
to re-plan and which settled on the first pass.

## Secrets

`_load_cloud_secrets()` copies supported keys from `st.secrets` into the environment when a
`secrets.toml` exists, and does nothing when it does not. The planning layer only ever reads
`os.environ`, so it never learns whether it is running on Streamlit Cloud or from a local `.env`.

## What the UI does not do

Streamlit reruns the whole script on each interaction and blocks during a run, so the page is
frozen while the team plans and a refresh mid-run loses the stream. Section details are
`st.expander`; there is no incremental re-render of a single card.

## Deploying

Streamlit Community Cloud deploys from the repository root: point it at `streamlit_app.py`, and add
`DEEPSEEK_API_KEY` and any other provider keys under the app's Secrets. With no keys set the app
still runs — every specialist falls back to deterministic output.
