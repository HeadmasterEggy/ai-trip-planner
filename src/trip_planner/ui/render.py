"""Rendering helpers shared by the Streamlit pages.

Kept out of `streamlit_app.py` so the entry point stays a thin shell and these
can be unit-tested by asserting on the HTML they return.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Literal

from .. import money
from ..contracts import AgentProposal, ProposalItem, TripPlan
from .theme import STATUS_LABEL


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def item_card(item: ProposalItem) -> str:
    """One proposal line as a card: meta row, optional heading, detail."""
    meta = [f'<span class="tp-item__kind">{_esc(item.kind.replace("-", " "))}</span>']
    if item.day is not None:
        meta.append(f"<span>Day {item.day}</span>")
    if item.startTime and item.endTime:
        meta.append(f"<span>{_esc(item.startTime)}–{_esc(item.endTime)}</span>")
    if item.estCost is not None:
        meta.append(f'<span class="tp-item__cost">${item.estCost:,.2f}</span>')
    title = f'<div class="tp-item__title">{_esc(item.location)}</div>' if item.location else ""
    return (
        '<div class="tp-item">'
        f'<div class="tp-item__meta">{"".join(meta)}</div>'
        f"{title}"
        f'<p class="tp-item__detail">{_esc(item.detail)}</p>'
        "</div>"
    )


def proposal_items(proposal: AgentProposal | None) -> str:
    """Order by day then start time, so a reader follows the trip, not the
    order the model happened to emit."""
    if proposal is None:
        return '<p class="tp-note">Details are still being prepared.</p>'
    if not proposal.items:
        return '<p class="tp-note">No detailed items were returned.</p>'
    ordered = sorted(proposal.items, key=lambda i: (i.day or 999, i.startTime or ""))
    return "".join(item_card(i) for i in ordered)


def chip(status: str) -> str:
    return f'<span class="tp-chip tp-chip--{_esc(status)}">{_esc(STATUS_LABEL.get(status, status))}</span>'


def budget_as_given(plan: TripPlan) -> str:
    """The budget in the traveller's own words, when they named a currency.

    Everything is planned in USD, so a budget of "¥3,000" becomes about USD 420
    before any rule sees it. Showing the figure they actually said, next to the
    one it became, is the difference between a conversion and a silent swap.
    """
    brief = plan.brief
    if not brief.budgetCurrency or brief.budgetCurrency == "USD" or not brief.budgetAsGiven:
        return ""
    return f" (≈ {money.given(brief.budgetAsGiven, brief.budgetCurrency)})"


def budget_block(plan: TripPlan) -> str:
    delta = plan.budgetTotal - plan.estTotal
    over = delta < 0
    pct = min(100, round(plan.estTotal / plan.budgetTotal * 100)) if plan.budgetTotal else 0
    wording = f"${abs(delta):,.2f} {'over' if over else 'under'} budget"
    return (
        '<div class="tp-budget">'
        "<span>Budget</span>"
        f"<span><strong>${plan.estTotal:,.2f}</strong> "
        f'<span class="tp-budget__total">/ ${plan.budgetTotal:,.2f}</span></span>'
        "</div>"
        f'<div class="tp-bar{" tp-bar--over" if over else ""}"><span style="width:{pct}%"></span></div>'
        f'<p class="tp-delta{" tp-delta--over" if over else ""}">{wording}'
        f"{budget_as_given(plan)}</p>"
    )


def agent_row(label: str, state: str, round_no: int, error: str | None) -> str:
    from .theme import AGENT_ICONS, AGENT_STATUS_TEXT

    text = AGENT_STATUS_TEXT[state]
    if round_no > 1 and state in ("agent_started", "agent_completed"):
        text += f" · round {round_no}"
    dot = '<span class="tp-dot"></span>' if state == "agent_started" else ""
    detail = f'<br><span class="tp-note">{_esc(error)}</span>' if error else ""
    return (
        '<div class="tp-agent">'
        f"<span>{AGENT_ICONS[state]}</span><span>{_esc(label)}</span>"
        f'<span class="tp-agent__status tp-agent__status--{state.replace("agent_", "")}">'
        f"{dot} {text}</span>"
        "</div>"
        f"{detail}"
    )


def _how(plan: TripPlan, section_id: str) -> str:
    """One line saying how a section was produced: path, route, time."""
    trace = next((t for t in plan.traces if t.agent == section_id), None)
    if trace is None:
        return ""
    parts = [trace.source]
    route = trace.evidence.get("route")
    if route:
        parts.append(route)
    if trace.seconds is not None:
        parts.append(f"{trace.seconds:.2f}s")
    return f"*How: {' · '.join(parts)}*"


def plan_markdown(plan: TripPlan) -> str:
    """The plan as a document, with the parts that make it safe to act on.

    The screen shows assumptions, the negotiation and what still needs a decision;
    an export that dropped them was a different document from the one being reviewed.
    """
    lines = [
        f"# Trip plan — {plan.brief.destination}",
        "",
        f"{plan.brief.dates[0]} to {plan.brief.dates[1]} · {plan.brief.groupSize} travellers",
        (
            f"Estimated USD {plan.estTotal:,.2f} of a USD {plan.budgetTotal:,.2f} budget"
            f"{budget_as_given(plan)} "
            f"({plan.overrunPct:+.2f}%)"
        ),
        "",
    ]

    pending = [checkpoint for checkpoint in plan.hitl if checkpoint.status == "pending"]
    if pending:
        lines += ["## Needs your decision", ""]
        lines += [f"- **{c.title}** — {c.detail}" for c in pending]
        lines.append("")

    for section in plan.sections:
        lines += [f"## {section.label} — USD {section.estCost:,.2f}", "", section.summary, ""]
        how = _how(plan, section.id)
        if how:
            lines += [how, ""]
        for item in section.proposal.items if section.proposal else []:
            when = f"Day {item.day} " if item.day else ""
            when += f"{item.startTime}–{item.endTime} " if item.startTime else ""
            cost = f" (USD {item.estCost:,.2f})" if item.estCost else ""
            lines.append(f"- {when}{item.location or item.kind}{cost}: {item.detail}")
        assumptions = section.proposal.assumptions if section.proposal else []
        if assumptions:
            lines += ["", "*Assumptions and caveats*", ""]
            lines += [f"- {note}" for note in assumptions]
        lines.append("")

    if plan.negotiation:
        lines += ["## Negotiation", ""]
        for entry in plan.negotiation:
            if not entry.conflicts:
                lines.append(f"- Round {entry.round}: no conflicts.")
                continue
            for request in entry.conflicts:
                lines.append(f"- Round {entry.round}: **{request.targetAgent}** — {request.reason}")
                lines += [f"  - {constraint}" for constraint in request.constraints]
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cross-section views. The plan stores proposals per specialist, but a traveller
# reads a trip by day and a reviewer reads it by disagreement, so both of those
# have to be reassembled here.
# ---------------------------------------------------------------------------


def scheduled_days(plan: TripPlan) -> dict[int, list[tuple[str, ProposalItem]]]:
    """Group every timed item across all specialists by day.

    Activities and transport legs live in different sections, so a clash is
    invisible until they are put on the same axis -- which is exactly what the
    orchestrator's conflict detection is looking at.
    """
    days: dict[int, list[tuple[str, ProposalItem]]] = {}
    for section in plan.sections:
        for item in section.proposal.items if section.proposal else []:
            if item.day is None:
                continue
            days.setdefault(item.day, []).append((section.id, item))
    for entries in days.values():
        entries.sort(key=lambda pair: pair[1].startTime or "")
    return dict(sorted(days.items()))


def timeline(plan: TripPlan) -> str:
    days = scheduled_days(plan)
    if not days:
        return '<p class="tp-note">No dated items yet.</p>'
    owners = {section.id: section.label for section in plan.sections}
    blocks = []
    for day, entries in days.items():
        rows = []
        for agent, item in entries:
            owner = owners.get(agent, agent)
            when = (
                f"{_esc(item.startTime)}–{_esc(item.endTime)}"
                if item.startTime and item.endTime
                else "unscheduled"
            )
            cost = f"${item.estCost:,.0f}" if item.estCost else ""
            rows.append(
                f'<div class="tp-tl__row tp-tl__row--{_esc(agent)}">'
                f'<span class="tp-tl__when">{when}</span>'
                f'<span class="tp-tl__what"><strong>{_esc(item.location or item.kind)}</strong>'
                # The owner was only the border colour: unreadable in print, invisible
                # to a reader who cannot separate the four hues.
                f'<span class="tp-tl__owner">{_esc(owner)}</span>'
                f'<br><span class="tp-note">{_esc(item.detail[:110])}</span></span>'
                f'<span class="tp-tl__cost">{cost}</span>'
                "</div>"
            )
        blocks.append(
            f'<div class="tp-tl__day"><div class="tp-tl__head">Day {day}</div>{"".join(rows)}</div>'
        )
    return f'<div class="tp-tl">{"".join(blocks)}</div>'


def budget_breakdown(plan: TripPlan) -> str:
    """Which section is driving the total.

    A single bar says a plan is over budget; it does not say which specialist to
    argue with, which is the only actionable question.
    """
    priced = [s for s in plan.sections if s.estCost > 0]
    if not priced:
        return ""
    largest = max(s.estCost for s in priced)
    rows = []
    for section in sorted(priced, key=lambda s: s.estCost, reverse=True):
        share = section.estCost / plan.estTotal * 100 if plan.estTotal else 0
        width = section.estCost / largest * 100 if largest else 0
        rows.append(
            '<div class="tp-bd__row">'
            f'<span class="tp-bd__label">{_esc(section.label)}</span>'
            f'<span class="tp-bd__track"><span style="width:{width:.1f}%"></span></span>'
            f'<span class="tp-bd__value">${section.estCost:,.0f}'
            f'<span class="tp-note"> · {share:.0f}%</span></span>'
            "</div>"
        )
    return f'<div class="tp-bd">{"".join(rows)}</div>'


def negotiation(plan: TripPlan) -> str:
    """Round-by-round: what was found, and the exact constraint that was sent."""
    if not plan.negotiation:
        return '<p class="tp-note">No negotiation was recorded for this plan.</p>'
    blocks = []
    for entry in plan.negotiation:
        if not entry.conflicts:
            blocks.append(
                f'<div class="tp-rd tp-rd--clear"><div class="tp-rd__head">'
                f"Round {entry.round} — no conflicts</div></div>"
            )
            continue
        items = []
        for request in entry.conflicts:
            acted = request.targetAgent in entry.revised
            badge = (
                '<span class="tp-chip tp-chip--planning">re-planned</span>'
                if acted
                else '<span class="tp-chip tp-chip--needs_you">not re-planned</span>'
            )
            constraints = "".join(f"<li>{_esc(c)}</li>" for c in request.constraints)
            items.append(
                '<div class="tp-rd__item">'
                f"<div><strong>{_esc(request.targetAgent)}</strong> {badge}</div>"
                f'<div class="tp-note">{_esc(request.reason)}</div>'
                f"<ul>{constraints}</ul>"
                "</div>"
            )
        blocks.append(
            f'<div class="tp-rd"><div class="tp-rd__head">Round {entry.round} — '
            f"{len(entry.conflicts)} conflict(s)</div>{''.join(items)}</div>"
        )
    return "".join(blocks)


def negotiation_verdict(plan: TripPlan) -> tuple[str, str]:
    """A one-line read on whether the rounds actually achieved anything."""
    if not plan.negotiation:
        return ("unknown", "No negotiation recorded.")
    last = plan.negotiation[-1]
    if not last.conflicts:
        return ("converged", f"Settled in round {last.round} with nothing outstanding.")
    if any(entry.stalled for entry in plan.negotiation):
        stalled_at = next(e.round for e in plan.negotiation if e.stalled)
        return (
            "stuck",
            (
                f"Stopped after round {stalled_at}: the revision changed nothing, so the "
                "specialists involved had nothing further to give. Another round would not "
                "help — this needs a change to the brief."
            ),
        )
    return ("unresolved", f"{len(last.conflicts)} conflict(s) still open at the round limit.")


def choice_option(option, picked: bool) -> str:
    """One candidate at a decision point, marked when it is the current pick."""
    cost = f'<span class="tp-opt__cost">${option.estCost:,.0f}</span>' if option.estCost else ""
    flag = '<span class="tp-opt__flag">suggested</span>' if option.recommended else ""
    return (
        f'<div class="tp-opt{" tp-opt--picked" if picked else ""}">'
        f'<div class="tp-opt__head"><span class="tp-opt__name">{_esc(option.label)}</span>'
        f"{flag}{cost}</div>"
        f'<p class="tp-opt__detail">{_esc(option.detail)}</p>'
        "</div>"
    )


def step_row(number: int, title: str, detail: str, state: str) -> str:
    """A numbered plan step. `state` is done, todo or open."""
    suffix = {"done": " tp-step__n--done", "todo": " tp-step__n--todo"}.get(state, "")
    return (
        '<div class="tp-step">'
        f'<span class="tp-step__n{suffix}">{number}</span>'
        f'<span class="tp-step__body"><strong>{_esc(title)}</strong>'
        f'<br><span class="tp-note">{_esc(detail)}</span></span>'
        "</div>"
    )


@dataclass(frozen=True)
class StepState:
    """One row of the settlement checklist, before it becomes HTML."""

    sectionId: str
    title: str
    detail: str
    state: Literal["done", "todo", "open"]


def step_states(plan: TripPlan) -> list[StepState]:
    """The plan as steps, in the order a traveller settles them.

    A choice checkpoint is joined to its section through `preferenceKey`
    (`accommodation.stayChoice.Kyoto` belongs to `accommodation`). Matching the
    checkpoint *id* against a section id never hit anything, so a confirmed
    choice left its step looking untouched.
    """
    settled = {
        checkpoint.preferenceKey
        for checkpoint in plan.hitl
        if checkpoint.type == "confirm_choice"
        and checkpoint.status == "approved"
        and checkpoint.preferenceKey
    }
    rows = []
    for section in plan.sections:
        if section.status == "needs_you":
            state: Literal["done", "todo", "open"] = "todo"
        elif any(key.startswith(f"{section.id}.") for key in settled):
            state = "done"
        else:
            state = "open"
        cost = f" · ${section.estCost:,.0f}" if section.estCost else ""
        rows.append(StepState(section.id, f"{section.label}{cost}", section.summary[:90], state))
    return rows


def steps(plan: TripPlan) -> str:
    """The whole checklist as HTML, numbered in plan order."""
    return "".join(
        step_row(number, row.title, row.detail, row.state)
        for number, row in enumerate(step_states(plan), start=1)
    )


def trace_block(traces: list, agent: str) -> str:
    """What one specialist read and which path it took, newest round first."""
    mine = [t for t in traces if t.agent == agent]
    if not mine:
        return '<p class="tp-note">No reasoning was recorded for this specialist.</p>'
    blocks = []
    for entry in sorted(mine, key=lambda t: t.round, reverse=True):
        tone = {
            "model": "tp-src--model",
            "calculator": "tp-src--calc",
            "deterministic fallback": "tp-src--fallback",
        }[entry.source]
        rows = "".join(
            f'<div class="tp-ev"><span class="tp-ev__k">{_esc(key)}</span>'
            f'<span class="tp-ev__v">{_esc(value)}</span></div>'
            for key, value in entry.evidence.items()
        )
        revision = (
            f'<div class="tp-ev"><span class="tp-ev__k">re-planning against</span>'
            f'<span class="tp-ev__v">{_esc(entry.revision)}</span></div>'
            if entry.revision
            else ""
        )
        fallback = (
            f'<p class="tp-fallback">Model output rejected: {_esc(entry.fallbackReason)}</p>'
            if entry.fallbackReason
            else ""
        )
        notes = "".join(f'<p class="tp-note">{_esc(n)}</p>' for n in entry.notes)
        # `is not None`, not truthiness: a deterministic section can be fast enough to
        # round to 0.00s, and hiding that looks like missing data.
        took = (
            f'<span class="tp-note">· {entry.seconds:.2f}s</span>'
            if entry.seconds is not None
            else ""
        )
        blocks.append(
            f'<div class="tp-trace"><div class="tp-trace__head">Round {entry.round}'
            f'{took}<span class="tp-src {tone}">{_esc(entry.source)}</span></div>'
            f"{revision}{rows}{fallback}{notes}</div>"
        )
    return "".join(blocks)


logger = logging.getLogger(__name__)


def failure_message(error: Exception) -> str:
    """One line for the traveller, with the detail kept out of the transcript.

    A `ValueError` is ours and already written for them -- brief validation speaks in
    dates and nights. Anything else is a bug: they get a sentence, and the traceback
    goes to the server log rather than into a chat bubble.
    """
    if isinstance(error, ValueError):
        return str(error)
    logger.exception("planning failed")
    return "Something went wrong while planning. Please try again."
