"""Streamlit interface for the multi-agent trip planner.

Streamlit Community Cloud looks for this file at the repository root, so the
entrypoint is a naming convention rather than configuration. Everything below
is presentation: the planning logic lives in `trip_planner`, which also runs
from a test or a script without importing Streamlit at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Streamlit Community Cloud installs from requirements.txt and does not install
# this project itself, so the src layout is not on the path there. Locally the
# editable install already provides it and this is a no-op.
_SRC = Path(__file__).parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

from trip_planner.contracts import TripBrief, TripPlan
from trip_planner.models import MODEL_ROUTING
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.workflow import OrchestratorOptions, ProgressEvent, run_orchestrator

st.set_page_config(page_title="AI Trip Planner", page_icon="✈️", layout="wide")
load_dotenv()

LABELS = {s.name: s.label for s in ALL_SPECIALISTS}
STATUS_TEXT = {
    "queued": "Queued",
    "agent_started": "Running",
    "agent_completed": "Complete",
    "agent_failed": "Needs attention",
}


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

if "plan" not in st.session_state:
    st.session_state.plan = None

with st.sidebar:
    st.title("Planning team")
    st.caption("A supervisor dispatches five specialists, then re-runs the ones in conflict.")
    for specialist in ALL_SPECIALISTS:
        st.markdown(f"**{specialist.label}** — `{specialist.name}`")
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
    st.caption(
        f"Tools: {'mock fixtures' if os.getenv('USE_MOCK_TOOLS', 'true') != 'false' else 'OpenStreetMap'}"
    )

st.title("✈️ AI Trip Planner")
st.caption("Five specialists negotiate a plan; conflicts are re-planned before you see it.")
st.info(
    "Prices, opening hours, entry rules and weather change without notice. Verify anything you "
    "act on with the venue or an official source before booking."
)

today = datetime.now(ZoneInfo("Australia/Sydney")).date()
with st.form("trip"):
    left, right = st.columns([2, 1])
    with left:
        destination = st.text_input(
            "Destination", value="Tokyo & Kyoto", help="Separate multiple cities with &"
        )
    with right:
        nationality = st.text_input("Passport nationality", value="Australian")

    start_col, end_col, people_col, budget_col = st.columns([1, 1, 0.7, 1])
    with start_col:
        start_date = st.date_input("Start", value=today + timedelta(days=60))
    with end_col:
        end_date = st.date_input("End", value=today + timedelta(days=67))
    with people_col:
        travellers = st.number_input("Travellers", min_value=1, max_value=20, value=2)
    with budget_col:
        budget = st.number_input("Total budget (USD)", min_value=100, value=4000, step=100)

    submitted = st.form_submit_button("Plan this trip", type="primary", use_container_width=True)

if submitted:
    if not destination.strip():
        st.error("Enter a destination.")
    elif end_date <= start_date:
        st.error("The end date must be after the start date.")
    else:
        brief = TripBrief(
            tripId=f"trip-{start_date.isoformat()}",
            destination=destination.strip(),
            dates=(start_date.isoformat(), end_date.isoformat()),
            groupSize=int(travellers),
            budgetTotal=float(budget),
            nationality=nationality.strip() or None,
        )

        st.subheader("Agent activity")
        slots = {s.name: st.empty() for s in ALL_SPECIALISTS}
        state: dict[str, str] = {s.name: "queued" for s in ALL_SPECIALISTS}

        def render(name: str, round_no: int = 1, error: str | None = None) -> None:
            status = STATUS_TEXT[state[name]]
            suffix = f" · round {round_no}" if round_no > 1 else ""
            icon = {
                "queued": "⏳",
                "agent_started": "🔄",
                "agent_completed": "✅",
                "agent_failed": "⚠️",
            }[state[name]]
            slots[name].markdown(
                f"{icon} **{LABELS[name]}** — {status}{suffix}"
                + (f"  \n`{error}`" if error else "")
            )

        for name in state:
            render(name)

        # The stream is consumed inside this single script run, so each event
        # can repaint its own row rather than leaving one opaque spinner.
        def on_progress(event: ProgressEvent) -> None:
            state[event.agent] = event.type
            render(event.agent, event.round, event.error)

        try:
            with st.spinner("The team is planning…"):
                st.session_state.plan = run_orchestrator(
                    brief, OrchestratorOptions(on_progress=on_progress)
                )
        except Exception as error:  # noqa: BLE001
            st.session_state.plan = None
            st.error(f"Planning failed: {error}")


def render_plan(plan: TripPlan) -> None:
    st.divider()
    head, meta = st.columns([3, 1])
    with head:
        st.subheader(f"{plan.brief.destination}")
        st.caption(
            f"{plan.brief.dates[0]} – {plan.brief.dates[1]} · {plan.brief.groupSize} travellers "
            f"· settled after round {plan.round}"
        )
    with meta:
        delta = plan.budgetTotal - plan.estTotal
        st.metric(
            "Estimated total",
            f"${plan.estTotal:,.2f}",
            f"${abs(delta):,.2f} {'under' if delta >= 0 else 'over'} budget",
            delta_color="normal" if delta >= 0 else "inverse",
        )
    st.progress(min(1.0, plan.estTotal / plan.budgetTotal) if plan.budgetTotal else 0.0)

    pending = [h for h in plan.hitl if h.status == "pending"]
    for checkpoint in pending:
        (st.warning if checkpoint.type == "escalation" else st.info)(
            f"**{checkpoint.title}** — {checkpoint.detail}"
        )

    for section in plan.sections:
        badge = "⚠️ needs you" if section.status == "needs_you" else "draft"
        with st.expander(f"{section.label} — ${section.estCost:,.2f} · {badge}", expanded=False):
            st.write(section.summary)
            if section.proposal is None:
                st.caption("Details are still being prepared.")
                continue
            if not section.proposal.items:
                st.caption("No detailed items were returned.")
            for item in sorted(
                section.proposal.items, key=lambda i: (i.day or 999, i.startTime or "")
            ):
                meta_bits = [item.kind.replace("-", " ").title()]
                if item.day is not None:
                    meta_bits.append(f"Day {item.day}")
                if item.startTime and item.endTime:
                    meta_bits.append(f"{item.startTime}–{item.endTime}")
                if item.estCost is not None:
                    meta_bits.append(f"**${item.estCost:,.2f}**")
                st.markdown(" · ".join(meta_bits))
                if item.location:
                    st.markdown(f"##### {item.location}")
                st.caption(item.detail)
                st.divider()
            if section.proposal.assumptions:
                with st.popover(f"Important notes ({len(section.proposal.assumptions)})"):
                    for note in section.proposal.assumptions:
                        st.markdown(f"- {note}")

    st.download_button(
        "Download plan as Markdown",
        data=_markdown(plan),
        file_name="trip-plan.md",
        mime="text/markdown",
        use_container_width=True,
    )


def _markdown(plan: TripPlan) -> str:
    lines = [
        f"# Trip plan — {plan.brief.destination}",
        "",
        f"{plan.brief.dates[0]} to {plan.brief.dates[1]} · {plan.brief.groupSize} travellers",
        (
            f"Estimated USD {plan.estTotal:,.2f} of a USD {plan.budgetTotal:,.2f} budget "
            f"({plan.overrunPct:+.2f}%)"
        ),
        "",
    ]
    for section in plan.sections:
        lines += [f"## {section.label} — USD {section.estCost:,.2f}", "", section.summary, ""]
        for item in section.proposal.items if section.proposal else []:
            when = f"Day {item.day} " if item.day else ""
            when += f"{item.startTime}–{item.endTime} " if item.startTime else ""
            cost = f" (USD {item.estCost:,.2f})" if item.estCost else ""
            lines.append(f"- {when}{item.location or item.kind}{cost}: {item.detail}")
        lines.append("")
    return "\n".join(lines)


if st.session_state.plan is not None:
    render_plan(st.session_state.plan)
