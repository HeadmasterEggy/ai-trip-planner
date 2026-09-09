"""Rendering helpers shared by the Streamlit pages.

Kept out of `streamlit_app.py` so the entry point stays a thin shell and these
can be unit-tested by asserting on the HTML they return.
"""

from __future__ import annotations

import html

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
        f'<p class="tp-delta{" tp-delta--over" if over else ""}">{wording}</p>'
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


def plan_markdown(plan: TripPlan) -> str:
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
