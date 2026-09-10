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

## Three views of one plan

The plan is stored per specialist, but nobody reads it that way. Two of the
three tabs reassemble it.

**Day by day** puts every timed item from every specialist on one axis, coloured
by owner. A transport leg and an activity only look like a clash when they share
a column — which is exactly what the orchestrator's conflict detection is
looking at, so this is the view that makes its work legible.

**By specialist** keeps the per-agent proposals, and leads with a breakdown of
which section is driving the total. A single budget bar says a plan is over; it
does not say who to argue with, which is the only actionable question.

**Steps** numbers the plan in the order a traveller settles it, marking what still needs them.

**Negotiation** is round by round: what was found, who was sent back, and the
exact constraint they received. The orchestrator discards this once it has a
plan, so `TripPlan.negotiation` records it. It is also where a plan that never
converged explains itself — the same conflict recurring in every round means the
specialists involved had nothing further to give, which is a different problem
from a plan that simply ran out of rounds.

## Decisions in the conversation

The specialists already compare candidates internally — accommodation weighs four stays per city
and keeps one. Those candidates are now exposed as a `confirm_choice` checkpoint, and the choice is
offered where it is explained rather than on a separate screen.

Confirming one writes a `UserPreference` into long-term memory and re-plans the same brief. The
specialist honours it through the seam it already reads, so nothing special-cases a decision after
the fact, and the choice survives the next message rather than being re-decided.

Nothing here blocks. A specialist has always pre-selected a sensible option, so a traveller who
ignores every checkpoint still gets a complete plan.

A confirmed choice also outranks a budget revision. A revision normally takes the cheapest eligible
option; once the traveller has decided, a cost cut must not quietly undo it.

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
