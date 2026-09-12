# Streamlit UI

`streamlit_app.py` sits at the repository root because Streamlit Community Cloud looks for that
name — the entry point is a naming convention rather than configuration.

The file is presentation only. It imports `trip_planner` and calls `run_orchestrator`; no planning
logic lives in it, which is why the same code runs from `pytest` or a script without importing
Streamlit at all.

## The opening screen

The first screen is a greeting, a chat box, and the logo in the top-left corner. It used to open on
`demo_brief()` — Tokyo & Kyoto, seven days, $4,000, already filled in — so a visitor's first act was
to delete someone else's trip before saying where they actually wanted to go.

Nothing is assumed until the traveller says it. The session holds a `BriefPatch` (a draft, every
field optional) rather than a brief, and it starts empty. Neither rail is rendered yet: the plan rail
because there is no plan to read, the nav rail because there is nothing to navigate to. No example
chips either — a suggestion that fills the page is one more thing to read before saying where you
want to go, and the chat box already says what it wants.

A turn has two shapes. If the draft is complete the orchestrator runs, the reply describes the plan,
and both rails arrive with it. If something required is still missing, nothing is planned at all:
the reply asks for the first missing field — by name, in the traveller's language — and the answer is
merged into the draft for the next turn. "Tokyo" alone is enough to start, because the local parser
reads a short opening message that named no other field as the destination; a greeting is not, and
mid-conversation words like "cheaper" are never read as a place.

```text
[logo]                            ← the top-left corner, on every screen
                            ✈️
                      Where to today?
                      Tell me where you want to go and roughly when. …
                      [ Where would you like to go? ]

> Tokyo
  When would you like to travel? Dates as YYYY-MM-DD … I'll also need how many
  people are travelling and your total budget in USD.
> 2026-11-10 to 2026-11-17, 2 people, budget $4000
  … five specialists run, and both rails appear …
```

**Why a greeting rather than a form.** Four fields is a form; a sentence is a conversation. There
used to be a structured form behind the rail as well, and it is gone: it was a second way to describe
a trip, which meant a second place for the completeness rules to live and a second thing to keep in
step with the chat. Everything the orchestrator needs is now said in one place.

**Why the plan rail waits.** A rail is only worth its space once it describes something. Before the
first plan the conversation takes the whole width, which is what makes the first screen read as a
chat rather than as an empty dashboard. The left rail is navigation instead of trip content, so it
is there from the start: hiding it would hide the history, and "go back to that conversation" has to
be reachable from the landing screen.

## The rail

The left rail is the navigation, and every row in it does something. There is deliberately no
Explore, Saved, Updates or Inspiration: this app has no such features, and a row that cannot be
clicked is a lie about what the product does.

It arrives with the first plan, like the plan rail — the opening screen is still only the
conversation. Unlike the plan rail it then **stays**: `New chat` clears the plan, and a rail that
left with it would strand the trip it had just parked. So the gate is "a plan, or something to
navigate to".

```text
[logo] AI Trip Planner    the mark (`st.logo`) and the wordmark beside it
[ 🔍 Search… ]            filters the two lists below, by title and by transcript
💬 Chats             1    the conversation page: the last one, plus the rail's list
🧳 Trips                  the trip page: every conversation that became a plan
🤖 Planning team ▸         the five specialists
⚙️ Setup ▸                 model routing, tools, tracing
TRIPS            1        a conversation that produced a plan
  🧳 Tokyo                 ← the open one, highlighted, not a button
     2026-11-10 – 2026-11-17
CHATS            2        a conversation still being collected
  💬 five days in Lisbon
     In progress
[ ＋ New chat ]            pinned to the bottom
```

**Chats and Trips are the two pages, and they are the only two rows.** The badge on `Chats` is the
count of conversations; `Trips` opens the grid below. Both are drawn the way the list draws the open
conversation — an accent edge on a raised row — so "which page am I on" reads the same way as "which
conversation am I in".

**The mark and the name are one lockup, in two elements.** `st.logo` pins the mark to the top-left
corner of the app — inside the rail when it is open, in the header when it is not, so it is there
from the first screen and does not arrive with the first plan. The API takes an image and nothing
else, so the wordmark is drawn by a `::after` rule on the logo's own wrapper (`theme.py`), which is
the one place that knows where the mark ended up. It is decoration rather than text: it is not
selectable, not translatable, and a screen reader reads the image's alt text and not this. The
favicon is the same file.

The file it serves is a 128px render, not the one supplied: that one is **837KB** — a 1279×1230 PNG
inside an SVG wrapper, so not vector art — and `st.logo` inlines an SVG's bytes into every rerun,
which measured at **1.49MB per rerun**. A PNG goes through Streamlit's media endpoint instead: one
14.6KB fetch that the browser caches. `assets/README.md` has the command that regenerates it, and
`tests/test_assets.py` keeps it from growing back.

A row's title is derived, never stored: the destination once there is a plan, otherwise the first
thing the traveller said, otherwise `Untitled`. The split into `Trips` and `Chats` falls out of the
same rule — a conversation with a plan *is* a trip — so there is no state to keep in sync, and
`ui/history.py` stays a set of pure functions over plain data.

**One conversation is open at a time.** Session state holds the open one; `history` holds the rest,
newest first. Starting a new chat or opening an old one first copies the open conversation into
`history` — the copy is what makes it safe, because `messages` is the one container the turn loop
keeps appending to. The open conversation is drawn as the highlighted row rather than as a button,
so it never appears twice.

**Leaving a paused run drops its thread.** A paused escalation's checkpoint belongs to the
conversation being left; keeping it would let an answer resume a run for a trip that is no longer on
screen, and would leave the checkpoint in the process-wide store for the life of the process.

`history` is bounded at 50 like the memory store, and it lives in session state, so a refresh loses
it. That is the same limitation as everything else here — see **Durable memory** in the roadmap,
which is what the rail would eventually read from instead.

## Your trips

The `Trips` row opens a page rather than a conversation: a title, `＋ New trip`, and one card per
conversation that produced a plan. It is the same set the rail's `Trips` section lists, drawn as a
grid — the rail is for switching, this is for looking.

```text
Your trips                                        [ ＋ New trip ]
All trips
┌───────────────────────┐ ┌───────────────────────┐
│ 🧳                    │ │                       │
│ Tokyo                 │ │                       │
│ 2026-11-10 – … · 8 days│ │                       │
├───────────────────────┤ └───────────────────────┘
│      Open trip        │
└───────────────────────┘
```

**A card has no photograph, and says so by not pretending to have one.** There is no image source in
this project, so a cover is a gradient from one of six palette slots into `cover-deep`, chosen by
`history.cover_index` — a CRC of the destination rather than `hash()`, which is salted per process
and would repaint Sydney a different colour on every restart.

**No Calendar, Receipts, or "Booked only".** This app books nothing and holds no receipts, and a
control that cannot do anything is a lie about what the product does. The card's one action is the
one that exists: open the trip.

The page stops before the chat input — a page, not a conversation, has nothing to type into — and the
plan rail is not drawn beside it, so the cards get the width.

## Theme

One palette, light, and the app pins it. Two files, and neither may drift from the other:

- **`.streamlit/config.toml`** owns every colour Streamlit draws itself — the chat input, tabs,
  expanders, buttons, code blocks, the sidebar background.
- **`ui/theme.py`** owns everything we draw: the palette is `PALETTE`, and the `:root` block is
  *generated* from it. No rule may write a colour as a literal — a literal is a colour the next
  re-theme misses, which is how eight values once survived a re-theme in the other direction.

`tests/test_theme.py` parses the TOML and fails when the two drift. The five values they share
(page, rail, border, text, accent) are written down twice because neither file can import the other;
the test is what makes that safe.

**There is no dark mode, and no switch.** Pinning a theme at the top level of `[theme]` is what makes
the app's config win over the user's own preference — and it is also what hides Streamlit's
System / Light / Dark picker, which is why this app has neither. Entry 13 of
[the debugging log](debugging-log.md) records what each arrangement actually does, including the one
where a switch is wired up and silently is not.

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
paused run's checkpoint is dropped when the pause is answered or superseded, including by leaving
the conversation (`New chat`, or opening another one).

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

Two rails around a conversation; only one of them waits.

The **left rail** is the sidebar, which Streamlit collapses natively. It is navigation — history,
search, `New chat`, and the trip's own panels behind it — so it is there from the first render. The
brand sits at the top of it, because that is already the page's top-left corner and a full-width
title was spending vertical space the conversation needed.

The **right rail** holds the plan, and it arrives with the first plan: before that there is nothing
to put beside the conversation. It collapses through a chevron on its edge, and Streamlit has no
second sidebar, so it is two column ratios and a session flag: `[1, 0.85]` open, `[1, 0.045]` closed.

The **conversation** sits between them and takes the width the plan gives back — all of it, before
the first plan. `New chat` returns to that state while keeping the conversation it left in the rail.

Two details that were wrong first time:

- The handle is styled through Streamlit's own `st-key-toggle-plan` class rather than by DOM
  adjacency. The button sits several wrappers deep and gains another when it has a tooltip, so a
  sibling selector matched during development and silently stopped matching after.
- It is `position: sticky`. A rail control that scrolls away with the content cannot bring the rail
  back — collapsed, it was the only way to reopen the panel and it sat 240px above the viewport.

The same trick scopes the rail's own rows: `st.container(key="rail-nav")`, `rail-history`,
`rail-new-chat` and the nav rows each become a `st-key-*` class, so "button in the rail" can be
styled without also styling every other button on the page — which is what the nav rows, the trip
cards and the plan rail's own handle all rely on.

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

The rail's history is session state, so a refresh loses it — and the nav rows are buttons rather
than links, so a conversation cannot be bookmarked or opened in a second tab.

## Deploying

Streamlit Community Cloud deploys from the repository root: point it at `streamlit_app.py`, and add
`DEEPSEEK_API_KEY` and any other provider keys under the app's Secrets. With no keys set the app
still runs — every specialist falls back to deterministic output.

`.streamlit/config.toml` is tracked and is read by the server rather than by the script, so editing
the palette needs a restart and not just a rerun.
