"""Itinerary specialist.

The model drafts the day plan, but every activity location must be a candidate
name copied character for character from the maps port. That rule lives in the
prompt *and* in `_validate`, because the validator throws the whole draft away
-- during the TypeScript build the model kept appending the category, returning
"Mock attraction near Tokyo & Kyoto (sight)" for the candidate "Mock attraction
near Tokyo & Kyoto", and every plan silently fell back.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated

from pydantic import BaseModel, Field

from ..contracts import AgentProposal, ProposalItem, RevisionRequest, TripBrief
from ..models import create_structured_invoker
from ..ports import AgentContext, Place
from .base import FunctionSpecialist, record_trace, trip_days

# Activities may claim at most this share of the trip budget, leaving room for
# transport, stay and meals.
ACTIVITY_BUDGET_SHARE = 0.4
# What one anchored activity per day costs when the budget is comfortable. A
# flat figure alone ignored the cap above, so a tight budget went straight to
# escalation on the one path that always runs offline.
DEFAULT_ACTIVITY_COST_USD = 60.0
MIN_TRANSFER_MINUTES = 150


class DraftActivity(BaseModel):
    day: Annotated[int, Field(ge=1)]
    startTime: str
    endTime: str
    location: Annotated[str, Field(min_length=1, max_length=120)]
    detail: Annotated[str, Field(min_length=1, max_length=400)]
    estCost: Annotated[float, Field(ge=0)]


class ItineraryDraft(BaseModel):
    summary: Annotated[str, Field(min_length=1, max_length=400)]
    activities: list[DraftActivity]
    assumptions: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(
        default_factory=list, max_length=6
    )


def travel_conflicts(draft: ItineraryDraft, ctx: AgentContext) -> list[str]:
    """Check map travel time between consecutive activities on each day.

    These are reported to the orchestrator rather than silently shifting times:
    the itinerary does not know what else is scheduled that day, so quietly
    moving an activity could land it on a transport leg it cannot see.
    """
    conflicts: list[str] = []
    for day in sorted({a.day for a in draft.activities}):
        same_day = sorted(
            (a for a in draft.activities if a.day == day), key=lambda a: _minutes(a.startTime)
        )
        for previous, current in pairwise(same_day):
            if previous.location == current.location:
                continue
            legs = ctx.tools.maps.route(frm=previous.location, to=current.location)
            required = sum(leg.durationMin for leg in legs)
            available = _minutes(current.startTime) - _minutes(previous.endTime)
            if required > available:
                conflicts.append(
                    f"geography conflict on day {day}: {previous.location} to "
                    f"{current.location} needs {required} minutes but only {available} "
                    "are available"
                )
    return conflicts


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _validate(draft: ItineraryDraft, days: int, places: list[Place]) -> ItineraryDraft:
    candidates = {p.name.strip().lower() for p in places}
    for activity in draft.activities:
        if activity.location.strip().lower() not in candidates:
            raise ValueError(f"Itinerary returned an ungrounded location: {activity.location}")
        if activity.day > days:
            raise ValueError(f"Itinerary day {activity.day} exceeds trip length.")
        if _minutes(activity.endTime) <= _minutes(activity.startTime):
            raise ValueError(f"Itinerary activity on day {activity.day} must end after it starts.")
    return draft


def _fallback(brief: TripBrief, days: int, places: list[Place]) -> ItineraryDraft:
    """One anchored activity per day, drawn only from grounded candidates.

    The daily estimate is clamped to the same share of the budget the model
    prompt is held to, so the deterministic path cannot spend more than the
    model was allowed to.
    """
    if not places:
        return ItineraryDraft(
            summary=f"No grounded activity candidates were available for {brief.destination}.",
            activities=[],
            assumptions=["The maps port returned no candidates; no activity was invented."],
        )
    per_day = round(
        min(DEFAULT_ACTIVITY_COST_USD, brief.budgetTotal * ACTIVITY_BUDGET_SHARE / days), 2
    )
    activities = [
        DraftActivity(
            day=day,
            startTime="13:00",
            endTime="16:00",
            location=places[(day - 1) % len(places)].name,
            detail=(
                f"{places[(day - 1) % len(places)].name}: suggested stop; confirm timing and "
                "suitability before visiting."
            ),
            estCost=per_day,
        )
        for day in range(1, days + 1)
    ]
    return ItineraryDraft(
        summary=f"{days}-day plan for {brief.destination} with one grounded activity per day.",
        activities=activities,
        assumptions=[
            "Opening hours and live availability must be confirmed before travel.",
            "One anchored activity per day leaves room for meals, transfers and changes.",
        ],
    )


def _prompt(
    brief: TripBrief, days: int, places: list[Place], revision: RevisionRequest | None
) -> str:
    cap = brief.budgetTotal * ACTIVITY_BUDGET_SHARE
    candidates = (
        "\n".join(f"- name: {p.name}\n  category: {p.category}" for p in places) or "- (none)"
    )
    revision_text = (
        f"\n\nRevision to address exactly:\n{revision.reason}\nConstraints: "
        f"{'; '.join(revision.constraints)}"
        if revision
        else ""
    )
    return (
        "Draft a practical trip itinerary using only the supplied facts.\n\n"
        f"Trip: {brief.destination}, {brief.dates[0]} to {brief.dates[1]}, "
        f"{brief.groupSize} travellers, total budget USD {brief.budgetTotal:.2f}.\n"
        f"Cover every day from 1 to {days}, with 1-3 non-overlapping activities per day using "
        "24-hour HH:MM times.\n"
        f"Leave at least {MIN_TRANSFER_MINUTES} minutes between activities at different "
        "locations so the transfer is feasible.\n"
        f"Keep total activity cost for the whole group at or below USD {cap:.2f}.\n\n"
        "Grounded candidates — an activity location must equal one candidate's `name` value "
        "exactly. Copy the name field only: never append the category, rating or district, and "
        "do not reword it. An "
        "activity whose location is not an exact candidate name causes the entire draft to be "
        f"discarded.\n{candidates}\n\n"
        "Hard limits, which the schema also enforces: summary at most 400 characters; each "
        "detail at most 400 characters; at most 6 assumptions of at most 400 characters each.\n"
        "Never claim live opening hours, availability, safety, visa or weather facts."
        f"{revision_text}"
    )


def _plan(brief: TripBrief, ctx: AgentContext, revision: RevisionRequest | None) -> AgentProposal:
    days = trip_days(brief.dates)
    places = ctx.tools.maps.places(near=brief.destination, category="sight")
    places += ctx.tools.maps.places(near=brief.destination, category="neighborhood")
    seen: dict[str, Place] = {}
    for place in places:
        seen.setdefault(place.name.strip().lower(), place)
    grounded = list(seen.values())

    source = "deterministic fallback"
    fallback_reason: str | None = None
    invoke = create_structured_invoker("itinerary", ItineraryDraft, "ItineraryDraft")
    draft: ItineraryDraft
    if invoke is None:
        draft = _fallback(brief, days, grounded)
    else:
        try:
            draft = _validate(invoke(_prompt(brief, days, grounded, revision)), days, grounded)
            source = "model"
        except Exception as error:  # noqa: BLE001
            fallback_reason = str(error)
            print(f"[itinerary] Model draft failed; using a safe local plan: {error}")
            draft = _fallback(brief, days, grounded)

    conflicts = travel_conflicts(draft, ctx)
    if revision is not None and conflicts:
        # A revision must not carry a newly discovered geography conflict
        # forward. Fall back to the conservative plan and re-check it.
        reason = "revision still conflicted: " + "; ".join(conflicts)
        fallback_reason = f"{fallback_reason}; {reason}" if fallback_reason else reason
        draft = _fallback(brief, days, grounded)
        source = "deterministic fallback"
        conflicts = travel_conflicts(draft, ctx)

    # Recorded last, after any fallback: the trace is the only record of how the
    # section was produced, so it must not credit a model draft the proposal
    # itself says was thrown away.
    record_trace(
        ctx,
        "itinerary",
        source,
        evidence={
            "grounded candidates": ", ".join(p.name for p in grounded) or "none",
            "trip days": str(days),
            "activity budget cap": f"USD {brief.budgetTotal * ACTIVITY_BUDGET_SHARE:,.2f}",
            "min transfer": f"{MIN_TRANSFER_MINUTES} minutes",
        },
        revision=revision,
        fallback_reason=fallback_reason,
    )

    items = [
        ProposalItem(
            kind="activity",
            detail=a.detail,
            estCost=a.estCost,
            day=a.day,
            startTime=a.startTime,
            endTime=a.endTime,
            location=a.location,
        )
        for a in sorted(draft.activities, key=lambda a: (a.day, a.startTime))
    ]
    return AgentProposal(
        agent="itinerary",
        summary=draft.summary,
        items=items,
        assumptions=[f"Planner source: {source}.", *draft.assumptions],
        conflictsWith=conflicts,
    )


itinerary_specialist = FunctionSpecialist("itinerary", "Day plan", _plan)
