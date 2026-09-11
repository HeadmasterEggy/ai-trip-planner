"""Streamlit interface for the multi-agent trip planner.

Streamlit Community Cloud looks for this file at the repository root, so the
entry point is a naming convention rather than configuration. Everything here
is presentation: the planning logic lives in `trip_planner`, which also runs
from a test or a script without importing Streamlit at all.
"""

from __future__ import annotations

import html
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

# Streamlit Community Cloud installs from requirements.txt and does not install
# this project itself, so the src layout is not on the path there. Locally the
# editable install already provides it and this is a no-op.
_SRC = Path(__file__).parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from trip_planner.chat import run_trip_chat_stream
from trip_planner.contracts import (
    FIELD_NAMES,
    BriefPatch,
    ChatRequest,
    TripBrief,
    TripPlan,
    brief_from_draft,
    brief_problem,
    missing_fields,
)
from trip_planner.decisions import apply_decision
from trip_planner.models import MODEL_ROUTING
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.ui.history import (
    Conversation,
    forget,
    matching,
    remember,
    snapshot,
    split,
)
from trip_planner.ui.render import (
    agent_row,
    budget_block,
    budget_breakdown,
    chip,
    choice_option,
    failure_message,
    negotiation,
    negotiation_verdict,
    plan_markdown,
    proposal_items,
    steps,
    timeline,
    trace_block,
)
from trip_planner.ui.theme import CSS
from trip_planner.workflow import OrchestratorOptions, forget_thread

st.set_page_config(page_title="AI Trip Planner", page_icon="✈️", layout="wide")
load_dotenv()
st.markdown(CSS, unsafe_allow_html=True)

logger = logging.getLogger(__name__)

LABELS = {s.name: s.label for s in ALL_SPECIALISTS}


def _load_cloud_secrets() -> None:
    """Copy supported Streamlit secrets into the environment when present.

    The planning layer only ever reads os.environ, so it does not know whether
    it is running on Streamlit Cloud or from a local .env file.
    """
    keys = (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_BASE_URL",
        "MINIMAX_API_KEY",
        "MINIMAX_MODEL",
        "MINIMAX_BASE_URL",
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
        "USE_MOCK_TOOLS",
        "OSM_USER_AGENT",
    )
    try:
        for key in keys:
            if key in st.secrets and not os.getenv(key):
                os.environ[key] = str(st.secrets[key])
    except FileNotFoundError:
        # Local development uses .env and has no secrets.toml.
        pass


_load_cloud_secrets()

# One identity per session. It used to be a fixed "trip-demo"/"demo-user" pair, which
# meant that on a shared deployment every visitor wrote into the same memory buckets:
# one traveller's confirmed stay would appear in the next one's plan. A session-scoped
# id costs nothing locally and is the difference between a demo and a deployment.
_SESSION = uuid4().hex[:12]
st.session_state.setdefault("user_id", f"user-{_SESSION}")


def _trip_id(seq: int) -> str:
    """One id per conversation in this session.

    Memory and the checkpointer key by `tripId` and nothing else, so two
    conversations inside one browser tab must not share one.
    """
    return f"trip-{_SESSION}-{seq}"


st.session_state.setdefault("plan", None)
# What the conversation has collected so far. Empty on purpose: nothing is
# assumed about the trip until the traveller says it. This used to open on
# `demo_brief()` -- Tokyo & Kyoto, seven days, $4,000 -- so the first thing a
# visitor had to do was delete someone else's trip.
st.session_state.setdefault("draft", BriefPatch())
st.session_state.setdefault("messages", [])
st.session_state.setdefault("chat_seq", 0)
st.session_state.setdefault("trip_id", _trip_id(0))
# The conversations that are not on screen. Session state holds one at a time;
# this is the rest of the rail, newest first.
st.session_state.setdefault("history", [])
st.session_state.setdefault("pending_escalation", None)


# --------------------------------------------------------------------------
# The rail. Navigation rather than a form: the conversation list, a way to start
# a new one, and the trip's own details behind it.
# --------------------------------------------------------------------------
def render_brief_form() -> TripBrief | None:
    """The structured path, for someone who would rather fill in four fields.

    Every field starts empty. A prefilled form is a trip somebody else chose --
    the dates, the party size, the budget -- and a traveller who submits it
    without reading is planning a trip they never asked for. The empty widgets
    also mean this form and the chat refuse the same things, with the same words.
    """
    today = datetime.now(ZoneInfo("Australia/Sydney")).date()
    with st.form("trip"):
        destination = st.text_input(
            "Destination",
            key="form-destination",
            value="",
            placeholder="e.g. Tokyo & Kyoto",
            help="Separate cities with &",
        )
        left, right = st.columns(2)
        with left:
            start = st.date_input("Start", key="form-start", value=None)
        with right:
            end = st.date_input("End", key="form-end", value=None)
        # Two to a row at most: the rail is ~250px, and three inputs in it wrap
        # their own labels ("Travell / ers").
        left, right = st.columns(2)
        with left:
            group = st.number_input(
                "Travellers", 1, 20, key="form-group", value=None, placeholder="2"
            )
        with right:
            total = st.number_input(
                "Budget (USD)", 100, key="form-budget", value=None, step=100, placeholder="3000"
            )
        passport = st.text_input(
            "Passport", key="form-passport", value="", placeholder="e.g. Australian"
        )
        submitted = st.form_submit_button(
            "Plan this trip", key="form-submit", type="primary", width="stretch"
        )
    if not submitted:
        return None

    draft = BriefPatch(
        destination=destination.strip() or None,
        dates=(start.isoformat(), end.isoformat()) if start and end else None,
        groupSize=int(group) if group else None,
        budgetTotal=float(total) if total else None,
        nationality=passport.strip() or None,
    )
    # The same completeness and feasibility rules the chat path runs, so the two
    # entry points cannot drift into accepting different trips.
    missing = missing_fields(draft)
    if missing:
        st.error("Still needed: " + ", ".join(FIELD_NAMES[field] for field in missing) + ".")
        return None
    brief = brief_from_draft(
        draft, trip_id=st.session_state.trip_id, user_id=st.session_state.user_id
    )
    problem = brief_problem(brief)
    if problem:
        st.error(problem)
        return None
    if start < today:
        st.warning("That start date is in the past; planning it anyway.")
    return brief


def remember_active() -> None:
    """Copy the conversation on screen into the rail, if it has anything in it.

    Called before the screen is replaced rather than after, because the state it
    copies is about to be overwritten. A conversation nobody has said anything in
    is not worth a row, so it is dropped instead of listed as "Untitled".
    """
    if not st.session_state.messages:
        return
    st.session_state.history = remember(
        st.session_state.history,
        snapshot(
            st.session_state.trip_id,
            st.session_state.messages,
            st.session_state.draft,
            st.session_state.plan,
        ),
    )


def drop_pause() -> None:
    """Forget a paused run whose thread belongs to the conversation being left.

    Keeping it would let the next escalation answer resume a thread for a trip
    that is no longer on screen.
    """
    paused = st.session_state.get("pending_escalation")
    if paused:
        forget_thread(paused["threadId"])
    st.session_state.pending_escalation = None


def new_chat() -> None:
    """Park the current conversation in the rail and start an empty one."""
    remember_active()
    drop_pause()
    st.session_state.plan = None
    st.session_state.draft = BriefPatch()
    st.session_state.messages = []
    st.session_state.chat_seq += 1
    st.session_state.trip_id = _trip_id(st.session_state.chat_seq)
    st.rerun()


def open_conversation(trip_id: str) -> None:
    """Put a conversation from the rail back on screen.

    It leaves the rail as it arrives: the open conversation is drawn as the
    highlighted row, so a copy of it in the list would be a second, clickable
    copy of what is already in front of the traveller.
    """
    remember_active()
    drop_pause()
    reopened = next(c for c in st.session_state.history if c.tripId == trip_id)
    st.session_state.history = forget(st.session_state.history, trip_id)
    st.session_state.trip_id = reopened.tripId
    st.session_state.messages = list(reopened.messages)
    st.session_state.draft = reopened.draft
    st.session_state.plan = reopened.plan
    st.rerun()


def render_history() -> None:
    """The conversation list, in two sections, filtered by the search box.

    Rows are buttons because every one of them does something. There is
    deliberately no Explore/Saved/Updates: this app has no such features, and a
    row that cannot be clicked is a lie about what the product does.
    """
    query = st.session_state.get("rail-search", "")
    live = snapshot(
        st.session_state.trip_id,
        st.session_state.messages,
        st.session_state.draft,
        st.session_state.plan,
    )
    # The open conversation belongs in the list -- it is where the highlight says
    # "you are here" -- but only once it has something to show.
    active = live if st.session_state.messages and matching([live], query) else None
    trips, chats = split(matching(st.session_state.history, query))
    if active is not None:
        (trips if active.is_trip else chats).insert(0, active)

    with st.container(key="rail-history"):
        if not trips and not chats:
            empty = "No matches." if query.strip() else "No chats yet."
            st.markdown(f'<div class="tp-rail__empty">{empty}</div>', unsafe_allow_html=True)
            return
        for label, rows in (("Trips", trips), ("Chats", chats)):
            if not rows:
                continue
            st.markdown(
                f'<div class="tp-rail__section">{label}'
                f'<span class="tp-rail__badge">{len(rows)}</span></div>',
                unsafe_allow_html=True,
            )
            for conversation in rows:
                render_row(conversation, is_active=conversation is active)
                st.caption(conversation.subtitle)


def render_row(conversation: Conversation, *, is_active: bool) -> None:
    """One row: the open conversation as a label, the others as buttons."""
    if is_active:
        # The title comes from the traveller's own words, so it is escaped before
        # it goes anywhere near `unsafe_allow_html`.
        st.markdown(
            f'<div class="tp-rail__active">{conversation.icon} '
            f"{html.escape(conversation.title)}</div>",
            unsafe_allow_html=True,
        )
    elif st.button(
        f"{conversation.icon} {conversation.title}",
        key=f"open-{conversation.tripId}",
        help=conversation.title,
    ):
        open_conversation(conversation.tripId)


def render_rail() -> TripBrief | None:
    """The left rail: navigation, history, and the trip's own details.

    It is always there, because it is navigation rather than trip content -- the
    plan rail still waits for a plan. Nothing in it is invented either: an empty
    history says so, and the form starts empty.
    """
    with st.sidebar:
        st.markdown('<div class="tp-brand">✈️ AI Trip Planner</div>', unsafe_allow_html=True)
        st.text_input(
            "Search trips and chats",
            key="rail-search",
            placeholder="Search…",
            label_visibility="collapsed",
        )

        with st.container(key="rail-nav"):
            with st.expander("🧳 Trip details", expanded=False):
                st.caption("Say it in the chat, or fill it in here.")
                submitted_brief = render_brief_form()
            with st.expander("🤖 Planning team", expanded=False):
                for specialist in ALL_SPECIALISTS:
                    st.markdown(
                        f"**{specialist.label}** &nbsp;`{specialist.name}`",
                        unsafe_allow_html=True,
                    )
            with st.expander("⚙️ Setup", expanded=False):
                st.caption("Model routing")
                for task, provider in MODEL_ROUTING.items():
                    st.caption(f"`{task}` → {provider}")
                keyed = bool(os.getenv("DEEPSEEK_API_KEY") or os.getenv("MINIMAX_API_KEY"))
                st.caption(
                    "Live models configured."
                    if keyed
                    else "No model key set — specialists use their deterministic fallbacks."
                )
                mocked = os.getenv("USE_MOCK_TOOLS", "true").lower() != "false"
                st.caption(f"Tools: {'mock fixtures' if mocked else 'OpenStreetMap'}")
                if os.getenv("LANGSMITH_TRACING", "").lower() == "true":
                    st.caption(f"Tracing → {os.getenv('LANGSMITH_PROJECT', 'default')}")

        render_history()

        # Pinned to the bottom of the rail: the primary action has to be
        # reachable without scrolling back up a long history.
        with st.container(key="rail-new-chat"):
            if st.button("＋ New chat", key="new-chat", type="primary"):
                new_chat()
    return submitted_brief


# The plan is a rail, not a fixed column: collapsed it leaves a handle on the
# edge, exactly like the nav rail on the left. While you are talking to the
# planner the plan is reference material, and a wide column of it crowds out
# the conversation it is meant to accompany.
#
# Before the first plan there is nothing to put beside the conversation, so the
# conversation gets the full width. That is the opening screen -- a greeting and
# a chat box, and no plan to read yet.
has_plan = st.session_state.plan is not None
# The left rail is navigation, so it arrives with the first plan too -- but it
# must not leave with it. `New chat` clears the plan, and a rail that vanished
# with the plan would strand the conversation it had just parked.
has_rail = has_plan or bool(st.session_state.history)

plan_column = None
if has_plan:
    st.session_state.setdefault("show_plan", True)
    if st.session_state.show_plan:
        chat_column, plan_column = st.columns([1, 0.85], gap="large")
    else:
        chat_column, plan_column = st.columns([1, 0.045], gap="small")

    with plan_column:
        if st.button(
            "›" if st.session_state.show_plan else "‹",
            key="toggle-plan",
            help="Hide the trip plan" if st.session_state.show_plan else "Show the trip plan",
            width="content" if st.session_state.show_plan else "stretch",
        ):
            st.session_state.show_plan = not st.session_state.show_plan
            st.rerun()
else:
    chat_column = st.container()

submitted_brief = render_rail() if has_rail else None


# --------------------------------------------------------------------------
# Left: the brief and the resulting plan.
# --------------------------------------------------------------------------
def render_decisions(plan: TripPlan) -> None:
    """Offer the choices a specialist already weighed up.

    They are never blocking: the specialist has pre-selected a sensible option,
    so a traveller who skips these still has a complete plan.
    """
    choices = [c for c in plan.hitl if c.type == "confirm_choice"]
    if not choices:
        return

    settled = sum(c.status == "approved" for c in choices)
    st.markdown(
        f"**Your choices** &nbsp;<span class='tp-note'>{settled} of "
        f"{len(choices)} confirmed</span>",
        unsafe_allow_html=True,
    )

    for checkpoint in choices:
        with st.expander(checkpoint.title, expanded=checkpoint.status != "approved"):
            labels = {o.id: o.label for o in checkpoint.options}
            ids = list(labels)
            current = checkpoint.selected if checkpoint.selected in ids else ids[0]
            # The label is the checkpoint's title ("Choose where to stay in Kyoto"), not
            # its detail: a screen reader reads the label as the group's name.
            picked = st.radio(
                checkpoint.title,
                ids,
                index=ids.index(current),
                # Bind the mapping now: a bare closure over `labels` would make
                # every checkpoint render the last one's labels.
                format_func=lambda i, labels=labels: labels[i],
                key=f"choice-{checkpoint.id}",
            )
            st.caption(checkpoint.detail)
            st.markdown(
                "".join(choice_option(o, o.id == picked) for o in checkpoint.options),
                unsafe_allow_html=True,
            )
            disabled = picked == checkpoint.selected and checkpoint.status == "approved"
            if st.button(
                "Confirm and continue",
                key=f"confirm-{checkpoint.id}",
                type="primary",
                disabled=disabled,
                width="stretch",
            ):
                with st.spinner("Re-planning around your choice…"):
                    st.session_state.plan = apply_decision(
                        plan, checkpoint.id, picked, OrchestratorOptions()
                    )
                st.rerun()


def render_escalation() -> None:
    """The answer to a paused run.

    An escalation that cannot be acted on is only worth showing if it can be
    answered, so the buttons resume the paused thread with the traveller's call
    instead of leaving the warning to repeat every turn.
    """
    paused = st.session_state.get("pending_escalation")
    if not paused:
        return
    for option in paused["options"]:
        if st.button(
            option["label"],
            key=f"escalation-{option['value']}",
            type="primary",
            width="stretch",
        ):
            # `plan_trip` reads the paused thread id from session state and clears
            # it after the resume, so it must not be cleared here: otherwise the
            # answer would be sent to a fresh thread, and the run would simply pause
            # again.
            with st.spinner("Recording your call…"):
                plan_trip(option["label"], st.session_state.draft, decision=option["value"])
            st.rerun()


def render_reasoning(plan: TripPlan) -> None:
    """One expander per specialist: what it read, and which path it took.

    The plan says what was decided. Without this a section written by a model is
    indistinguishable from one that fell back, and there is no way to see
    whether a place came from the maps port or was invented.
    """
    if not plan.traces:
        return
    st.markdown("**How each specialist decided**")
    sources = {t.agent: t.source for t in sorted(plan.traces, key=lambda t: t.round)}
    icons = {"model": "🧠", "calculator": "📐", "deterministic fallback": "🛟"}
    for section in plan.sections:
        source = sources.get(section.id, "")
        label = f"{icons.get(source, '·')} {section.label} — {source or 'not run'}"
        with st.expander(label, expanded=False):
            st.markdown(trace_block(plan.traces, section.id), unsafe_allow_html=True)
            if section.proposal and section.proposal.assumptions:
                st.caption("Stated assumptions")
                for note in section.proposal.assumptions:
                    st.markdown(f"- {note}")


VERDICT_STYLE = {
    "converged": st.success,
    "stuck": st.warning,
    "unresolved": st.warning,
    "unknown": st.info,
}


def render_plan(plan: TripPlan) -> None:
    st.subheader(plan.brief.destination)
    st.caption(
        f"{plan.brief.dates[0]} – {plan.brief.dates[1]} · {plan.brief.groupSize} travellers "
        f"· settled after round {plan.round}"
    )
    st.markdown(budget_block(plan), unsafe_allow_html=True)

    for checkpoint in (h for h in plan.hitl if h.status == "pending"):
        shout = st.warning if checkpoint.type == "escalation" else st.info
        shout(f"**{checkpoint.title}** — {checkpoint.detail}")

    render_escalation()

    steps_tab, day_tab, detail_tab, talks_tab = st.tabs(
        ["Steps", "Day by day", "By specialist", "Negotiation"]
    )

    with steps_tab:
        st.markdown(steps(plan), unsafe_allow_html=True)

    with day_tab:
        # Everything scheduled, from every specialist, on one axis. A transport
        # leg and an activity only look like a clash when they share a column.
        st.markdown(timeline(plan), unsafe_allow_html=True)
        st.caption("Colour marks the owning specialist. Transport legs are fixed; activities move.")

    with detail_tab:
        breakdown = budget_breakdown(plan)
        if breakdown:  # a heading with nothing under it reads as a bug
            st.markdown("**Where the money goes**")
            st.markdown(breakdown, unsafe_allow_html=True)
            st.divider()
        for section in plan.sections:
            header = f"{section.label} — ${section.estCost:,.2f}"
            with st.expander(header, expanded=section.status == "needs_you"):
                st.markdown(chip(section.status), unsafe_allow_html=True)
                st.write(section.summary)
                st.markdown(proposal_items(section.proposal), unsafe_allow_html=True)
                if section.proposal and section.proposal.assumptions:
                    with st.popover(f"Important notes ({len(section.proposal.assumptions)})"):
                        for note in section.proposal.assumptions:
                            st.markdown(f"- {note}")

    with talks_tab:
        verdict, explanation = negotiation_verdict(plan)
        VERDICT_STYLE[verdict](explanation)
        st.caption(
            "Each round: what the orchestrator found, and the exact constraint it sent back. "
            "A specialist only ever sees its own proposal, so the constraint has to name the "
            "window it must avoid and who holds it."
        )
        st.markdown(negotiation(plan), unsafe_allow_html=True)

    st.download_button(
        "Download plan as Markdown",
        data=plan_markdown(plan),
        file_name="trip-plan.md",
        mime="text/markdown",
        width="stretch",
    )


def plan_trip(message: str, draft: BriefPatch | None = None, decision: str | None = None) -> None:
    """Run one turn and stream per-agent progress while it runs.

    `decision` resumes a run that paused for an escalation: the paused thread id is
    in session state, and the traveller's answer is the graph's resume value.
    """
    st.session_state.messages.append({"role": "user", "content": message})
    paused = st.session_state.get("pending_escalation")
    if decision is None and paused:
        # Asking something else supersedes the pause: drop its checkpoint instead of
        # keeping a thread nobody will resume.
        forget_thread(paused["threadId"])
        paused = None

    with chat_column:
        st.markdown("**Agent activity**")
        slots = {s.name: st.empty() for s in ALL_SPECIALISTS}
        state = {s.name: "queued" for s in ALL_SPECIALISTS}
        for name in state:
            slots[name].markdown(agent_row(LABELS[name], "queued", 1, None), unsafe_allow_html=True)

        # The graph reports progress on its custom stream, so this loop runs on the
        # script's own thread: no specialist ever touches a widget, whichever thread
        # the supervisor's tool pool ran it on.
        try:
            with st.spinner("The team is planning…"):
                stream = run_trip_chat_stream(
                    ChatRequest(
                        tripId=st.session_state.trip_id,
                        message=message,
                        draft=draft,
                        userId=st.session_state.user_id,
                    ),
                    OrchestratorOptions(),
                    resume=decision,
                    thread_id=paused["threadId"] if decision and paused else None,
                )
                for event in stream:
                    state[event.agent] = event.type
                    slots[event.agent].markdown(
                        agent_row(LABELS[event.agent], event.type, event.round, event.error),
                        unsafe_allow_html=True,
                    )
                response = stream.response
        except Exception as error:  # noqa: BLE001
            st.session_state.messages.append(
                {"role": "assistant", "content": failure_message(error)}
            )
            return
        if response is None:  # cannot happen: the loop above drains the stream
            st.session_state.messages.append(
                {"role": "assistant", "content": "Something went wrong while planning."}
            )
            return

        # A run that escalated paused for a human. Keep the thread and what the pause
        # offers, so the buttons below can resume it; a run that did not pause has
        # nothing to answer.
        st.session_state.pending_escalation = (
            {"threadId": stream.thread_id, **stream.interrupt} if stream.interrupt else None
        )

    # The turn always hands back what the conversation now knows; only a turn that
    # actually planned replaces the plan. A turn that asked a question leaves the
    # previous plan standing rather than blanking the rail.
    st.session_state.draft = response.draft
    if response.plan is not None:
        st.session_state.plan = response.plan
    st.session_state.messages.append({"role": "assistant", "content": response.reply})


if plan_column is not None:
    with plan_column:
        render_plan(st.session_state.plan)


def render_hero() -> None:
    """The first screen: a greeting and a chat box, and nothing already decided.

    No example trips, no form, no rails. Each of those was a suggestion the
    traveller had to read past before saying where they want to go.
    """
    st.markdown(
        '<div class="tp-hero">'
        '<div class="tp-hero__mark">✈️</div>'
        '<div class="tp-hero__title">Where to today?</div>'
        '<div class="tp-hero__sub">Tell me where you want to go and roughly when. Five '
        "specialists plan the route, the stays, the food and the costs together, and ask "
        "whenever something is missing.</div>"
        "</div>",
        unsafe_allow_html=True,
    )


with chat_column:
    if not st.session_state.messages:
        # Nothing else on the first screen: no example chips, no rails. A
        # suggestion that fills the page is one more thing to read before saying
        # where you want to go, and the chat box already says what it wants.
        render_hero()
    else:
        st.markdown("**Conversation**")
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.write(message["content"])

        if st.session_state.plan is not None:
            render_decisions(st.session_state.plan)
            with st.expander("How the team decided", expanded=False):
                render_reasoning(st.session_state.plan)

prompt = st.chat_input(
    "Tell the team what to change…"
    if st.session_state.plan is not None
    else "Where would you like to go?"
)
# With a conversation on screen there are prices and opening hours to be careful
# about. On the empty first screen there is nothing to verify yet, and a stray
# footnote under the greeting reads as part of it.
if st.session_state.messages:
    st.caption(
        "Prices, opening hours, entry rules and weather change without notice. Verify anything you "
        "act on with the venue or an official source before booking."
    )

if submitted_brief is not None:
    # The form is a complete answer, so it replaces the draft wholesale and the
    # turn below is an ordinary planning turn.
    st.session_state.draft = BriefPatch.from_brief(submitted_brief)
    plan_trip(
        f"Plan {submitted_brief.destination} from {submitted_brief.dates[0]} to "
        f"{submitted_brief.dates[1]} for {submitted_brief.groupSize} travellers "
        f"with a budget of USD {submitted_brief.budgetTotal:,.0f}.",
        st.session_state.draft,
    )
    st.rerun()
elif prompt:
    plan_trip(prompt, st.session_state.draft)
    st.rerun()
