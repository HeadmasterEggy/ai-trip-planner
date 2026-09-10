"""Streamlit interface for the multi-agent trip planner.

Streamlit Community Cloud looks for this file at the repository root, so the
entry point is a naming convention rather than configuration. Everything here
is presentation: the planning logic lives in `trip_planner`, which also runs
from a test or a script without importing Streamlit at all.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

# Streamlit Community Cloud installs from requirements.txt and does not install
# this project itself, so the src layout is not on the path there. Locally the
# editable install already provides it and this is a no-op.
_SRC = Path(__file__).parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from trip_planner.chat import run_trip_chat
from trip_planner.contracts import ChatRequest, TripBrief, TripPlan
from trip_planner.demo import DEMO_BRIEF
from trip_planner.models import MODEL_ROUTING
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.ui.render import (
    agent_row,
    budget_block,
    budget_breakdown,
    chip,
    negotiation,
    negotiation_verdict,
    plan_markdown,
    proposal_items,
    timeline,
)
from trip_planner.ui.theme import CSS
from trip_planner.workflow import OrchestratorOptions, ProgressEvent

st.set_page_config(page_title="AI Trip Planner", page_icon="✈️", layout="wide")
load_dotenv()
st.markdown(CSS, unsafe_allow_html=True)

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

st.session_state.setdefault("plan", None)
st.session_state.setdefault("brief", DEMO_BRIEF)
st.session_state.setdefault("messages", [])
st.session_state.setdefault("trip_id", "trip-demo")


# --------------------------------------------------------------------------
# Sidebar: who is on the team, and what this deployment is actually wired to.
# --------------------------------------------------------------------------
with st.sidebar:
    st.title("Planning team")
    st.caption("A supervisor delegates to five specialists, then re-runs the ones in conflict.")
    for specialist in ALL_SPECIALISTS:
        st.markdown(f"**{specialist.label}** &nbsp;`{specialist.name}`", unsafe_allow_html=True)

    st.divider()
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

    st.divider()
    if st.button("Start a new trip", use_container_width=True):
        st.session_state.plan = None
        st.session_state.brief = DEMO_BRIEF
        st.session_state.messages = []
        st.rerun()


st.title("✈️ AI Trip Planner")
st.caption(
    "Describe a trip. Five specialists negotiate it; conflicts are re-planned before you see it."
)
st.info(
    "Prices, opening hours, entry rules and weather change without notice. Verify anything you "
    "act on with the venue or an official source before booking."
)

plan_column, chat_column = st.columns([1.15, 1], gap="large")


# --------------------------------------------------------------------------
# Left: the brief and the resulting plan.
# --------------------------------------------------------------------------
def render_brief_form() -> TripBrief | None:
    """The structured path. Chat can change the same fields conversationally."""
    brief: TripBrief = st.session_state.brief
    today = datetime.now(ZoneInfo("Australia/Sydney")).date()
    with st.form("trip"):
        destination = st.text_input(
            "Destination", value=brief.destination, help="Separate multiple cities with &"
        )
        left, right = st.columns(2)
        with left:
            start = st.date_input("Start", value=datetime.fromisoformat(brief.dates[0]).date())
        with right:
            end = st.date_input("End", value=datetime.fromisoformat(brief.dates[1]).date())
        people, budget, nationality = st.columns([0.8, 1, 1])
        with people:
            group = st.number_input("Travellers", 1, 20, brief.groupSize)
        with budget:
            total = st.number_input("Budget (USD)", 100, value=int(brief.budgetTotal), step=100)
        with nationality:
            passport = st.text_input("Passport", value=brief.nationality or "")
        submitted = st.form_submit_button(
            "Plan this trip", type="primary", use_container_width=True
        )
    if not submitted:
        return None
    if not destination.strip():
        st.error("Enter a destination.")
        return None
    if end <= start:
        st.error("The end date must be after the start date.")
        return None
    if start < today:
        st.warning("That start date is in the past; planning it anyway.")
    return TripBrief(
        tripId=st.session_state.trip_id,
        destination=destination.strip(),
        dates=(start.isoformat(), end.isoformat()),
        groupSize=int(group),
        budgetTotal=float(total),
        nationality=passport.strip() or None,
    )


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

    day_tab, detail_tab, talks_tab = st.tabs(["Day by day", "By specialist", "Negotiation"])

    with day_tab:
        # Everything scheduled, from every specialist, on one axis. A transport
        # leg and an activity only look like a clash when they share a column.
        st.markdown(timeline(plan), unsafe_allow_html=True)
        st.caption("Colour marks the owning specialist. Transport legs are fixed; activities move.")

    with detail_tab:
        st.markdown("**Where the money goes**")
        st.markdown(budget_breakdown(plan), unsafe_allow_html=True)
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
        use_container_width=True,
    )


def plan_trip(message: str, brief: TripBrief | None) -> None:
    """Run one turn and stream per-agent progress while it runs."""
    st.session_state.messages.append({"role": "user", "content": message})

    with chat_column:
        st.markdown("**Agent activity**")
        slots = {s.name: st.empty() for s in ALL_SPECIALISTS}
        state = {s.name: "queued" for s in ALL_SPECIALISTS}
        for name in state:
            slots[name].markdown(agent_row(LABELS[name], "queued", 1, None), unsafe_allow_html=True)

        # The stream is consumed inside this single script run, so each event
        # repaints its own row instead of leaving one opaque spinner.
        def on_progress(event: ProgressEvent) -> None:
            state[event.agent] = event.type
            slots[event.agent].markdown(
                agent_row(LABELS[event.agent], event.type, event.round, event.error),
                unsafe_allow_html=True,
            )

        try:
            with st.spinner("The team is planning…"):
                response = run_trip_chat(
                    ChatRequest(tripId=st.session_state.trip_id, message=message, brief=brief),
                    OrchestratorOptions(on_progress=on_progress),
                )
        except Exception as error:  # noqa: BLE001
            st.session_state.messages.append(
                {"role": "assistant", "content": f"Planning failed: {error}"}
            )
            return

    st.session_state.plan = response.plan
    st.session_state.brief = response.plan.brief
    st.session_state.messages.append({"role": "assistant", "content": response.reply})


with plan_column:
    submitted_brief = render_brief_form()
    if st.session_state.plan is not None:
        st.divider()
        render_plan(st.session_state.plan)

EXAMPLES = [
    "Make it 4 people",
    "Cut the budget to $2,500",
    "去京都，2026-10-01 到 2026-10-05，三个人",
]

with chat_column:
    st.markdown("**Conversation**")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    if not st.session_state.messages:
        # A bare input box does not say what this accepts. Offer the shapes that
        # actually work, including a non-English one, since the reply follows the
        # language of the request.
        st.caption("Ask for a change in plain language. Try one of these:")
        for index, example in enumerate(EXAMPLES):
            if st.button(example, key=f"example-{index}", use_container_width=True):
                st.session_state.pending = example
                st.rerun()

# A clicked example and a typed message take the same path from here.
prompt = st.chat_input("Tell the team what to change…") or st.session_state.pop("pending", None)

if submitted_brief is not None:
    plan_trip(
        f"Plan {submitted_brief.destination} from {submitted_brief.dates[0]} to "
        f"{submitted_brief.dates[1]} for {submitted_brief.groupSize} travellers "
        f"with a budget of USD {submitted_brief.budgetTotal:,.0f}.",
        submitted_brief,
    )
    st.rerun()
elif prompt:
    plan_trip(prompt, st.session_state.brief)
    st.rerun()
