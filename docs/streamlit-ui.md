# Streamlit UI

`streamlit_app.py` sits at the repository root because Streamlit Community Cloud looks for that
name — the entry point is a naming convention rather than configuration.

The file is presentation only. It imports `trip_planner` and calls `run_orchestrator`; no planning
logic lives in it, which is why the same code runs from `pytest` or a script without importing
Streamlit at all.

## The opening screen

The first screen is a greeting, a chat box, and a rail that says it has nothing to list. It used to
open on `demo_brief()` — Tokyo & Kyoto, seven days, $4,000, already filled in — so a visitor's first
act was to delete someone else's trip before saying where they actually wanted to go.

Nothing is assumed until the traveller says it. The session holds a `BriefPatch` (a draft, every
field optional) rather than a brief, and it starts empty. The plan rail is not rendered at all,
because there is no plan to read. No example chips either — a suggestion that fills the page is one
more thing to read before saying where you want to go, and the chat box already says what it wants.

A turn has two shapes. If the draft is complete the orchestrator runs, the reply describes the plan,
and the plan rail arrives with it. If something required is still missing, nothing is planned at all:
the reply asks for the first missing field — by name, in the traveller's language — and the answer is
merged into the draft for the next turn. "Tokyo" alone is enough to start, because the local parser
reads a short opening message that named no other field as the destination; a greeting is not, and
mid-conversation words like "cheaper" are never read as a place.

```text
  ┌ rail ────────────┐
  │ ✈️ AI Trip Planner│        ✈️
  │ [ Search… ]      │   Where to today?
  │ 🧳 Trip details   │   Tell me where you want to go and roughly when. …
  │ 🤖 Planning team  │   [ Where would you like to go? ]
  │ ⚙️ Setup          │
  │ No chats yet.    │  > Tokyo
  │ [ ＋ New chat ]   │    When would you like to travel? Dates as YYYY-MM-DD …
  └──────────────────┘  > 2026-11-10 to 2026-11-17, 2 people, budget $4000
                          … five specialists run, and the plan rail appears …
```

**Why a greeting rather than a form.** Four fields is a form; a sentence is a conversation. The
structured path still exists, behind `🧳 Trip details` in the rail — and every one of its fields
starts empty. A prefilled form is a trip somebody else chose, and a traveller who submits it without
reading plans a trip they never asked for. Submitting it incomplete is refused with the same words
the chat asks in, because both go through `contracts.missing_fields`.

**Why the plan rail waits.** A rail is only worth its space once it describes something. Before the
first plan the conversation takes the whole width, which is what makes the first screen read as a
chat rather than as an empty dashboard. The left rail is navigation instead of trip content, so it
is there from the start: hiding it would hide the history, and "go back to that conversation" has to
be reachable from the landing screen.

## The rail

The left rail is the navigation, and every row in it does something. There is deliberately no
Explore, Saved, Updates or Inspiration: this app has no such features, and a row that cannot be
clicked is a lie about what the product does.

```text
✈️ AI Trip Planner   ☀️     brand, and the theme switch (see below)
[ 🔍 Search… ]            filters the two lists below, by title and by transcript
🧳 Trip details ▸          the structured form
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

## Theme

Light and dark, switched from the rail, in two places that are not allowed to disagree:

- **`.streamlit/config.toml`** owns every colour Streamlit draws itself — the chat input, tabs,
  expanders, buttons, code blocks, the sidebar background. It pins `base = "dark"` and one accent,
  and deliberately **nothing else**.
- **`ui/theme.py`** owns everything we draw: two palettes, `DARK` and `LIGHT`, and the `:root` block
  is *generated* from whichever one is live. No rule may write a colour as a literal — a literal is
  a colour the next re-theme misses, which is exactly how eight light-theme values survived the
  first pass of the dark rewrite.

Three things about this arrangement are load-bearing, and none of them are visible in a diff:

1. **Pinning a neutral breaks the toggle.** Streamlit applies `backgroundColor`,
   `secondaryBackgroundColor` and `textColor` to *both* bases, so the moment one is set, switching
   the base changes nothing at all — which reads as a broken button rather than a broken palette.
2. **The neutrals are therefore Streamlit's own**, per base. That is a real dependency on Streamlit's
   built-in palette, so `tests/test_theme.py` pins the values and fails if an upgrade moves them.
3. **The live base comes from `theme.base`**, not from `st.context.theme`. The latter reports the
   *browser's* preference, and in this configuration it reports the opposite of what is on screen —
   in both directions, which would make the palette wrong every time.

The switch is the ☀️/🌙 button beside the brand. It writes Streamlit's theme config and reruns —
`st._config` is private, so a release that removes it degrades to a warning rather than a dead
button — and that makes the theme a property of the **server process, not the session**: on a shared
deployment one visitor's click changes it for everyone until someone changes it back.

### Who decides the theme

Streamlit resolves a theme from two things — the app's `[theme]` config and the *user's* per-browser
preference (the System / Light / Dark row in its ⋮ menu, kept as `stActiveTheme-/-v2`) — and which
one wins turns on a single question: **does the app pin a theme of its own?** Pinning anything at the
top level of `[theme]`, even just `primaryColor`, hides the picker and makes the app's config
authoritative. Declaring `[theme.light]` and `[theme.dark]` says "I support both" instead: the picker
appears and the user's preference decides.

That is why it cannot be both. An in-app button *is* the app deciding, so it needs the first mode; a
per-user picker is the user deciding, and in the second mode changing the base has no visible effect
at all. Measured four ways, by reading the computed background of `.stApp`:

| `.streamlit/config.toml` | picker in ⋮ | the base flip | who decides |
| --- | --- | --- | --- |
| nothing at all | present | no effect | the user (System follows the OS) |
| `primaryColor` only | hidden | works | the app |
| top-level neutral colours | hidden | **no effect** | the app, frozen to one look |
| `[theme.light]` + `[theme.dark]` | present | no effect | the user |

This app is the second row. The third is the trap: pinning a neutral makes the app authoritative
*and* immune to the base, so the button looks broken. The fourth is where to move if per-user themes
matter more than the button — our palettes survive it, because the per-base tables take our accent
and neutrals for each mode; what is lost is the button, and `theme.base` stops being the truth about
what is on screen. Our CSS would then need the live palette from `st.context.theme.type` (the user's
choice) instead, which is only as good as the situation: it reports the *browser's* preference,
which is the opposite of what is drawn whenever the app overrides it.

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

The same trick scopes the rail's own rows: `st.container(key="rail-nav")`, `rail-history` and
`rail-new-chat` each become a `st-key-*` class, so "button" in the rail can be styled without also
styling the trip form's submit button.

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

`.streamlit/config.toml` is tracked and is read by the server rather than by the script, so changing
which base a deployment *starts* in needs a restart. Which base it is currently on does not: that is
what the rail's switch changes at runtime.
