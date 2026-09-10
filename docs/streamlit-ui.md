# Streamlit UI

`streamlit_app.py` sits at the repository root because Streamlit Community Cloud looks for that
name — the entry point is a naming convention rather than configuration.

The file is presentation only. It imports `trip_planner` and calls `run_orchestrator`; no planning
logic lives in it, which is why the same code runs from `pytest` or a script without importing
Streamlit at all.

## The opening screen

The first screen is a greeting and a chat box, and nothing else. It used to open on `demo_brief()` —
Tokyo & Kyoto, seven days, $4,000, already filled in — so a visitor's first act was to delete
someone else's trip before saying where they actually wanted to go.

Nothing is assumed until the traveller says it. The session holds a `BriefPatch` (a draft, every
field optional) rather than a brief, and it starts empty; the plan rail is not rendered at all,
because with nothing planned there is nothing to collapse and the conversation can have the full
width. The only suggestion on screen is three example one-liners, dated from today so a
long-running deployment never opens by proposing a trip that has already happened.

A turn now has two shapes. If the draft is complete the orchestrator runs and the reply describes
the plan. If something required is still missing, nothing is planned at all: the reply asks for the
first missing field — by name, in the traveller's language — and the answer is merged into the draft
for the next turn. "Tokyo" alone is enough to start, because the local parser reads a short opening
message that named no other field as the destination; a greeting is not, and mid-conversation words
like "cheaper" are never read as a place.

```text
Where to today?
[ Tokyo & Kyoto, 2026-11-10 to 2026-11-17, 2 people, budget $4000 ]
> Tokyo
  When would you like to travel? Dates as YYYY-MM-DD … I'll also need how many people
  are travelling and your total budget in USD.
> 2026-11-10 to 2026-11-17, 2 people, budget $4000
  … five specialists run …
```

**Why a greeting rather than a form.** Four fields is a form; a sentence is a conversation. The
structured path still exists, but it sits behind a collapsed expander in the sidebar, and filling it
in *replaces* the draft rather than editing it — prefilling it from the conversation would invite a
half-edit that is neither the draft nor what is on screen.

## Per-agent progress

A planning run takes tens of seconds and involves five specialists across up to three rounds.
Showing one spinner for all of that hides the part worth watching, so a run reports an event as each
specialist starts, finishes or fails:

```python
ProgressEvent(type="agent_started", agent="itinerary", round=1)
```

The events travel on the graph's custom stream, so the UI iterates `run_trip_chat_stream` on the
script's own thread. It opens one `st.empty()` placeholder per specialist before the run and repaints
just that row as events arrive:

```python
stream = run_trip_chat_stream(request, OrchestratorOptions())
for event in stream:
    slots[event.agent].markdown(agent_row(...))
response = stream.response
```

```
✅ Day plan — Complete
✅ Getting around — Complete · round 3
✅ Stay — Complete · round 3
✅ Destination guide — Complete
✅ Food & dining — Complete
```

The round suffix is what makes the negotiation visible: it shows which specialists were sent back
to re-plan and which settled on the first pass.

**Why a stream and not a callback.** This was a callback (`OrchestratorOptions.on_progress`) until it
failed in a way worth remembering: which thread an event arrives on is decided by the specialist, and
under the supervisor the answer is "a LangGraph tool worker", because `ToolNode` runs a batch of tool
calls on a thread pool. A worker thread has no `ScriptRunContext`, so the widget write raised
`NoSessionContext` *inside* the delegation tool — before the specialist had done anything — and the
whole supervisor fan-out collapsed to the deterministic fallback. The first fix bound the run context
to the worker thread (`ui/live.py`); that is gone, because streaming removes the question: the
specialists publish, and only the consumer's thread touches a widget. `tests/test_supervisor_streaming.py`
pins both halves, including that no module under `trip_planner/` imports Streamlit.

## Whose session is it

Each session derives its own `trip_id` and `user_id` (`_SESSION` in the entry point) and sends them
on every chat request, so they reach the brief the specialists plan against. That matters because
the memory store and the checkpointer are process-wide and key by those ids and nothing else: the
ids used to be a fixed `trip-demo`/`demo-user` pair, which meant that on a shared deployment one
traveller's confirmed stay could appear in the next one's plan.

Both stores are bounded as well — the memory store evicts its oldest trip and user at a cap, and a
paused run's checkpoint is dropped when the pause is answered or superseded, including by
`Start a new trip`.

## Three views of one plan

The plan is stored per specialist, but nobody reads it that way. Two of the
three tabs reassemble it.

**Day by day** puts every timed item from every specialist on one axis, labelled and
coloured by owner: colour alone is unreadable in print and to a reader who cannot
separate four hues. A transport leg and an activity only look like a clash when they share
a column — which is exactly what the orchestrator's conflict detection is
looking at, so this is the view that makes its work legible.

**By specialist** keeps the per-agent proposals, and leads with a breakdown of
which section is driving the total. A single budget bar says a plan is over; it
does not say who to argue with, which is the only actionable question.

**Steps** numbers the plan in the order a traveller settles it, marking what still needs them and
what a confirmed choice has already settled. The state is computed by `ui.render.step_states`, not
in the entry point, so the choice-to-step join is covered by tests: a checkpoint reaches its section
through `preferenceKey`, which is the only field the two share.

**Negotiation** is round by round: what was found, who was sent back, and the
exact constraint they received. The orchestrator discards this once it has a
plan, so `TripPlan.negotiation` records it. It is also where a plan that never
converged explains itself — the same conflict recurring in every round means the
specialists involved had nothing further to give, which is a different problem
from a plan that simply ran out of rounds.

## The download

`Download plan as Markdown` writes the document a traveller would act on, not just the
sections: the pending decisions, each section's assumptions and caveats, the line saying how
that section was produced (which path, which route, how long), and the negotiation round by
round. Anything on screen that changes what someone should do is in the file, because an
export that drops the caveats is a different document from the one being reviewed.

## Layout

Two rails around a conversation.

The **left rail** is the sidebar, which Streamlit collapses natively: visible while you are
adjusting the trip, out of the way while you are reading the plan. Nothing in it is required —
every field can simply be said to the planner, which is why the form is a collapsed expander rather
than the front of the page. The brand sits at the top of it, because that is already the page's
top-left corner and a full-width title was spending vertical space the conversation needed.

The **right rail** holds the plan and collapses the same way, through a chevron on its edge. It does
not exist until there is a plan. Streamlit has no second sidebar, so it is two column ratios and a
session flag: `[1, 0.85]` open, `[1, 0.045]` closed.

The **conversation** sits between them and takes the width the plan gives back — all of it, before
the first plan.

Two details that were wrong first time:

- The handle is styled through Streamlit's own `st-key-toggle-plan` class rather than by DOM
  adjacency. The button sits several wrappers deep and gains another when it has a tooltip, so a
  sibling selector matched during development and silently stopped matching after.
- It is `position: sticky`. A rail control that scrolls away with the content cannot bring the rail
  back — collapsed, it was the only way to reopen the panel and it sat 240px above the viewport.

## Seeing how each specialist decided

The plan says what was decided. Without more, a section a model wrote is indistinguishable from one
that fell back, and there is no way to tell whether a place came from the maps port or was invented.

Every specialist now reports a `SpecialistTrace` per round: the evidence it was given, which path
it took, the constraint it was re-planning against, and — on a fallback — why the model output was
rejected. `How the team decided` opens one panel per specialist:

```
🧠 Day plan — model
   Round 2  [model]
     re-planning against   time overlap on day 4: 09:00-11:30 conflicts with 09:00-11:20
                           → on day 4 keep clear of 09:00-11:20, held by transport (Tokyo → Kyoto)
     grounded candidates   Mock sight near Tokyo & Kyoto, Mock neighborhood near Tokyo & Kyoto
     activity budget cap   USD 1,600.00
```

The badge distinguishes the three paths, because they carry different weight: `model` was reasoned,
`calculator` was computed from a port and no model could have invented it, `fallback` means the
model output was rejected and a safe local plan was used instead.

Traces live on the plan rather than in the proposals: they are diagnostic, so a caller that ignores
them still gets everything it needs and the contract stays about the trip.

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
