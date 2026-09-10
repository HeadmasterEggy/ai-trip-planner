"""Dining specialist.

Venue names are grounded in the maps port. The priced item is a single
whole-trip meal budget envelope, not a sum of the individual venue picks --
double-counting a meal budget and its venues was a real defect in an earlier
build.

The specialist never asserts menu contents, allergen safety or dietary
certification; those change without notice and travellers are told to confirm
them with the venue directly.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from ..contracts import AgentProposal, ProposalItem, RevisionRequest, TripBrief, UserPreference
from ..models import create_structured_invoker
from ..ports import AgentContext, Place
from .base import FunctionSpecialist, is_budget_revision, record_trace, trip_days

# The meal envelope is capped both as a share of the trip budget and per
# person per day, so a large budget cannot quietly become a huge food bill.
MEAL_BUDGET_SHARE = 0.2
MAX_DAILY_PER_PERSON_USD = 75.0
BUDGET_REVISION_CUT = 0.7


class DiningPick(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    detail: Annotated[str, Field(min_length=1, max_length=500)]


class DiningDraft(BaseModel):
    summary: Annotated[str, Field(min_length=1, max_length=400)]
    dailyBudgetPerPersonUsd: Annotated[float, Field(ge=0)]
    picks: Annotated[list[DiningPick], Field(max_length=5)]
    assumptions: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=400)]], Field(max_length=6)
    ] = Field(default_factory=list)


def _dietary(preferences: list[UserPreference]) -> list[UserPreference]:
    return [p for p in preferences if p.key.startswith("dining.") or "diet" in p.key.lower()]


def _validate(draft: DiningDraft, places: list[Place], ceiling: float) -> DiningDraft:
    candidates = {p.name.strip().lower() for p in places}
    for pick in draft.picks:
        if pick.name.strip().lower() not in candidates:
            raise ValueError(f"Dining returned an ungrounded venue: {pick.name}")
    if draft.dailyBudgetPerPersonUsd > ceiling:
        raise ValueError(
            f"Dining budget {draft.dailyBudgetPerPersonUsd:.2f} exceeds the ceiling {ceiling:.2f}."
        )
    return draft


def _fallback(brief: TripBrief, places: list[Place], ceiling: float) -> DiningDraft:
    return DiningDraft(
        summary=(
            f"Grounded dining candidates with a whole-trip meal budget envelope for "
            f"{brief.destination}."
        ),
        dailyBudgetPerPersonUsd=ceiling,
        picks=[
            DiningPick(
                name=p.name,
                detail=f"{p.name}: candidate from the maps port; confirm menu and suitability.",
            )
            for p in places[:5]
        ],
        assumptions=[
            "Deterministic fallback does not infer cuisine, menu, certification or availability."
        ],
    )


def _prompt(
    brief: TripBrief,
    days: int,
    places: list[Place],
    dietary: list[UserPreference],
    ceiling: float,
    revision: RevisionRequest | None,
) -> str:
    candidates = (
        "\n".join(f"- name: {p.name}\n  category: {p.category}" for p in places) or "- (none)"
    )
    diet = "; ".join(f"{p.key}={p.value}" for p in dietary) or "none recorded"
    revision_text = (
        (
            f"\n\nRevision to address exactly:\n{revision.reason}\nConstraints: "
            f"{'; '.join(revision.constraints)}"
        )
        if revision
        else ""
    )
    return (
        "Write concise dining recommendations and a realistic daily per-person meal budget "
        "in USD.\n\n"
        f"Trip: {brief.destination}, {days} days, {brief.groupSize} travellers, total budget "
        f"USD {brief.budgetTotal:.2f}.\n"
        f"Confirmed dietary preferences: {diet}.\n"
        f"dailyBudgetPerPersonUsd must be non-negative and at most {ceiling:.2f}.\n\n"
        "Grounded venue candidates — a pick's name must equal one candidate's `name` value "
        "exactly. Copy the name field only, with no category, rating or district appended. "
        "Return no picks rather than "
        f"inventing a venue that is not listed.\n{candidates}\n\n"
        "Never claim live hours, availability, menu items, allergen safety, halal/kosher "
        "certification or dietary suitability; tell travellers to confirm important dietary "
        "constraints directly with the venue.\n\n"
        "Hard limits, which the schema also enforces: summary at most 400 characters; at most 5 "
        "picks, each name at most 120 and detail at most 500 characters; at most 6 assumptions "
        "of at most 400 characters each."
        f"{revision_text}"
    )


def _plan(brief: TripBrief, ctx: AgentContext, revision: RevisionRequest | None) -> AgentProposal:
    days = trip_days(brief.dates)
    places = ctx.tools.maps.places(near=brief.destination, category="restaurant")
    preferences = ctx.mem.get_long_term(brief.userId)
    dietary = _dietary(preferences)

    # Ceiling is whichever of the two caps binds first.
    share_ceiling = (brief.budgetTotal * MEAL_BUDGET_SHARE) / max(1, days * brief.groupSize)
    ceiling = min(MAX_DAILY_PER_PERSON_USD, share_ceiling)
    if is_budget_revision(revision):
        ceiling = round(ceiling * BUDGET_REVISION_CUT, 2)

    source = "deterministic fallback"
    fallback_reason: str | None = None
    invoke = create_structured_invoker("dining", DiningDraft, "DiningDraft")
    if invoke is None:
        draft = _fallback(brief, places, ceiling)
    else:
        try:
            draft = _validate(
                invoke(_prompt(brief, days, places, dietary, ceiling, revision)), places, ceiling
            )
            source = "model"
        except Exception as error:  # noqa: BLE001
            fallback_reason = str(error)
            print(f"[dining] Model draft failed; using a safe local plan: {error}")
            draft = _fallback(brief, places, ceiling)

    record_trace(
        ctx,
        "dining",
        source,
        evidence={
            "venue candidates": ", ".join(p.name for p in places) or "none",
            "daily ceiling": f"USD {ceiling:,.2f} per person",
            "dietary preferences": ", ".join(f"{p.key}={p.value}" for p in dietary)
            or "none recorded",
        },
        revision=revision,
        fallback_reason=fallback_reason,
    )

    envelope = round(draft.dailyBudgetPerPersonUsd * days * brief.groupSize, 2)
    items = [
        # Only the envelope carries a cost. The picks are unpriced so the meal
        # budget is never counted twice.
        ProposalItem(
            kind="meal",
            detail=(
                f"Whole-trip meal budget: USD {draft.dailyBudgetPerPersonUsd:.2f} per person "
                f"per day x {days} days x {brief.groupSize} travellers."
            ),
            estCost=envelope,
        )
    ]
    items += [
        ProposalItem(kind="venue", detail=pick.detail, location=pick.name) for pick in draft.picks
    ]

    assumptions = [
        f"Dining source: {source}.",
        "The priced item is a budget envelope, not a reservation or a sum of the venue picks.",
        (
            "Venue data comes only from the maps port; menus, dietary suitability and "
            "availability require direct confirmation."
        ),
    ]
    if not dietary:
        assumptions.append("No confirmed dietary preferences were found in long-term memory.")
    assumptions.extend(draft.assumptions)

    return AgentProposal(
        agent="dining",
        summary=f"{draft.summary} · USD {envelope:.2f} meal budget",
        items=items,
        assumptions=assumptions,
    )


dining_specialist = FunctionSpecialist("dining", "Food & dining", _plan)
