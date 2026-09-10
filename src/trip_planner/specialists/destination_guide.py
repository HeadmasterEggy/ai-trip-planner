"""Destination guide specialist.

Attraction names are grounded in the maps port. Entry, health and safety
guidance is deliberately non-committal: the guide points at official sources
rather than asserting that a traveller is eligible to enter or that an area is
safe, because that is advice this system is not in a position to give.

The schema's limits are restated in the prompt because the extraction retries
only a few times before the draft is abandoned -- during the TypeScript build
the discarded attempts were over the summary cap or missing required fields,
and the guide fell back on every single round.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field

from ..contracts import AgentProposal, ProposalItem, RevisionRequest, TripBrief, UserPreference
from ..models import create_structured_invoker
from ..ports import AgentContext, Place
from .base import FunctionSpecialist, record_trace

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class GuideAttraction(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    detail: Annotated[str, Field(min_length=1, max_length=500)]


class DestinationGuideDraft(BaseModel):
    summary: Annotated[str, Field(min_length=1, max_length=400)]
    attractions: Annotated[list[GuideAttraction], Field(max_length=5)]
    customs: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=400)]], Field(min_length=1, max_length=4)
    ]
    safety: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=400)]], Field(min_length=1, max_length=4)
    ]
    entryHealth: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=400)]], Field(min_length=1, max_length=4)
    ]
    weather: Annotated[str, Field(min_length=1, max_length=500)]
    packing: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=300)]], Field(min_length=1, max_length=6)
    ]
    assumptions: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=400)]], Field(max_length=6)
    ] = Field(default_factory=list)


def travel_month(start: str) -> str:
    return MONTHS[date.fromisoformat(start).month - 1]


def _validate(draft: DestinationGuideDraft, places: list[Place]) -> DestinationGuideDraft:
    candidates = {p.name.strip().lower() for p in places}
    for attraction in draft.attractions:
        if attraction.name.strip().lower() not in candidates:
            raise ValueError(f"Guide returned an ungrounded attraction: {attraction.name}")
    return draft


def _fallback(brief: TripBrief, month: str, places: list[Place]) -> DestinationGuideDraft:
    return DestinationGuideDraft(
        summary=f"Practical pre-trip checklist for {brief.destination}.",
        attractions=[
            GuideAttraction(
                name=p.name,
                detail=f"{p.name}: {p.category} candidate; verify opening hours before visiting.",
            )
            for p in places[:5]
        ],
        customs=["Follow posted venue rules and check official local visitor guidance."],
        safety=[
            (
                "Save local emergency contacts, keep copies of essential documents, and follow "
                "current official travel advisories."
            )
        ],
        entryHealth=[
            (
                f"Check current official immigration and public-health requirements for a "
                f"{brief.nationality or 'your'} passport; requirements change without notice."
            )
        ],
        weather=f"Treat {month} conditions as planning context only, not a forecast.",
        packing=["Weather-appropriate layers", "Comfortable walking shoes", "Travel documents"],
        assumptions=["Deterministic fallback avoids unsourced destination-specific claims."],
    )


def _prompt(
    brief: TripBrief, month: str, places: list[Place], preferences: list[UserPreference]
) -> str:
    candidates = (
        "\n".join(f"- name: {p.name}\n  category: {p.category}" for p in places) or "- (none)"
    )
    prefs = "; ".join(f"{p.key}={p.value}" for p in preferences) or "none recorded"
    return (
        "Write concise destination guidance using only the supplied facts.\n\n"
        f"Trip: {brief.destination}, {brief.dates[0]} to {brief.dates[1]}, "
        f"{brief.groupSize} travellers, budget USD {brief.budgetTotal:.2f}"
        f"{f', {brief.nationality} passport' if brief.nationality else ''}.\n"
        f"Travel month: {month}. Confirmed preferences: {prefs}.\n\n"
        "Grounded attraction candidates — an attraction name must equal one candidate's `name` "
        "value exactly. Copy the name field only, with no category, rating or district "
        "appended. Return no "
        f"attractions if the list is empty.\n{candidates}\n\n"
        "Describe weather as typical monthly planning context, never a forecast. Do not state "
        "that a traveller is eligible to enter, that a vaccine is required, or that an area is "
        "safe; give practical checks and point at current official immigration, public-health "
        "and travel-advisory sources. Never claim live opening hours or availability.\n\n"
        "Fill every field on the first attempt and respect these limits literally, because the "
        "extraction is retried only a few times before the draft is abandoned: summary at most "
        "400 characters; at most 5 attractions, each detail at most 500 characters; customs, "
        "safety and entryHealth are each 1-4 strings of at most 400 characters; weather at most "
        "500 characters; packing 1-6 strings of at most 300 characters; assumptions at most 6."
    )


def _plan(brief: TripBrief, ctx: AgentContext, revision: RevisionRequest | None) -> AgentProposal:
    month = travel_month(brief.dates[0])
    places = ctx.tools.maps.places(near=brief.destination, category="sight")
    places += ctx.tools.maps.places(near=brief.destination, category="museum")
    seen: dict[str, Place] = {}
    for place in places:
        seen.setdefault(place.name.strip().lower(), place)
    grounded = list(seen.values())
    preferences = ctx.mem.get_long_term(brief.userId)

    source = "deterministic fallback"
    fallback_reason: str | None = None
    invoke = create_structured_invoker(
        "destination-guide", DestinationGuideDraft, "DestinationGuideDraft"
    )
    if invoke is None:
        draft = _fallback(brief, month, grounded)
    else:
        try:
            draft = _validate(invoke(_prompt(brief, month, grounded, preferences)), grounded)
            source = "model"
        except Exception as error:  # noqa: BLE001
            fallback_reason = str(error)
            print(f"[destination-guide] Model draft failed; using a safe local plan: {error}")
            draft = _fallback(brief, month, grounded)

    record_trace(
        ctx,
        "destination-guide",
        source,
        evidence={
            "attraction candidates": ", ".join(p.name for p in grounded) or "none",
            "travel month": month,
            "passport": brief.nationality or "not stated",
            "confirmed preferences": ", ".join(f"{p.key}={p.value}" for p in preferences)
            or "none recorded",
        },
        revision=revision,
        fallback_reason=fallback_reason,
    )

    items = [
        ProposalItem(kind="attraction", detail=a.detail, location=a.name) for a in draft.attractions
    ]
    items += [ProposalItem(kind="customs", detail=text) for text in draft.customs]
    items += [ProposalItem(kind="safety", detail=text) for text in draft.safety]
    items += [
        ProposalItem(
            kind="entry-health",
            detail=f"{text} Verify against current official government sources before booking.",
        )
        for text in draft.entryHealth
    ]
    items.append(
        ProposalItem(
            kind="weather-packing",
            detail=f"{draft.weather} Pack: {', '.join(draft.packing)}.",
        )
    )

    return AgentProposal(
        agent="destination-guide",
        summary=draft.summary,
        items=items,
        assumptions=[
            f"Guide source: {source}.",
            "Attractions come only from the injected maps port and may be mock or stale data.",
            (
                "Weather is planning context, not a forecast; entry, health and safety "
                "guidance requires official verification."
            ),
            *draft.assumptions,
        ],
    )


destination_guide_specialist = FunctionSpecialist("destination-guide", "Destination guide", _plan)
