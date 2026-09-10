"""Accommodation specialist.

Deliberately deterministic: dates, preference filtering, room counts and
costing live in this module, so a model can narrate a stay but never price
one. A multi-city trip is split into contiguous per-city segments, which is
why the total is not simply one city's rate times the whole trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..contracts import (
    AgentProposal,
    ChoiceOption,
    ProposalItem,
    RevisionRequest,
    TripBrief,
    UserPreference,
)
from ..ports import AgentContext, StayOption
from .base import FunctionSpecialist, cities, is_budget_revision

GUESTS_PER_ROOM = 2


@dataclass(frozen=True)
class StaySegment:
    city: str
    checkIn: str
    checkOut: str
    nights: int
    day: int


@dataclass(frozen=True)
class StayPreferences:
    roomAllocation: str = "shared"
    minRating: float = 0.0
    freeCancellation: bool = False
    # city -> the stay the traveller picked at a checkpoint. A confirmed choice
    # outranks every soft default, including a budget revision's preference for
    # the cheapest option: the traveller already decided.
    chosen: dict[str, str] = field(default_factory=dict)


def stay_choice_key(city: str) -> str:
    """The long-term preference key a confirmed stay is written to."""
    return f"accommodation.stayChoice.{city.strip()}"


def read_preferences(prefs: list[UserPreference]) -> StayPreferences:
    values = {p.key: p.value for p in prefs}
    allocation = values.get("accommodation.roomAllocation", "shared")
    if allocation not in ("shared", "individual"):
        raise ValueError("accommodation.roomAllocation must be shared or individual.")
    rating = float(values.get("accommodation.minRating", "0"))
    if not 0 <= rating <= 10:
        raise ValueError("accommodation.minRating must be between 0 and 10.")
    cancellation = values.get("accommodation.freeCancellation", "false")
    if cancellation not in ("true", "false"):
        raise ValueError("accommodation.freeCancellation must be true or false.")
    prefix = "accommodation.stayChoice."
    chosen = {k[len(prefix) :]: v for k, v in values.items() if k.startswith(prefix)}
    return StayPreferences(allocation, rating, cancellation == "true", chosen)


def split_stay(brief: TripBrief) -> list[StaySegment]:
    """Split a multi-city trip into evenly distributed overnight segments."""
    start = date.fromisoformat(brief.dates[0])
    nights = (date.fromisoformat(brief.dates[1]) - start).days
    if nights <= 0:
        raise ValueError("Accommodation check-out must be after check-in.")
    names = cities(brief.destination)
    if nights < len(names):
        raise ValueError("Each destination needs at least one overnight stay.")
    segments: list[StaySegment] = []
    offset = 0
    for index, city in enumerate(names):
        city_nights = nights // len(names) + (1 if index < nights % len(names) else 0)
        segments.append(
            StaySegment(
                city=city,
                checkIn=(start + timedelta(days=offset)).isoformat(),
                checkOut=(start + timedelta(days=offset + city_nights)).isoformat(),
                nights=city_nights,
                day=offset + 1,
            )
        )
        offset += city_nights
    return segments


def eligible_options(options: list[StayOption], prefs: StayPreferences) -> list[StayOption]:
    """Drop malformed or preference-incompatible records, cheapest first.

    Sorting here is what lets a budget revision take the first entry
    deterministically instead of re-deciding policy at the call site.
    """
    kept = [
        o
        for o in options
        if o.name.strip()
        and o.area.strip()
        and o.pricePerNightUsd > 0
        and prefs.minRating <= o.rating <= 10
        and (not prefs.freeCancellation or o.freeCancellation)
    ]
    return sorted(kept, key=lambda o: (o.pricePerNightUsd, -o.rating, o.name))


def choose_initial(options: list[StayOption]) -> StayOption:
    """Rating >=8 with free cancellation are soft defaults, applied after
    confirmed preferences have already filtered the list."""
    return next((o for o in options if o.rating >= 8 and o.freeCancellation), options[0])


def stay_cost(option: StayOption, nights: int, rooms: int) -> float:
    nightly_cents = round(option.pricePerNightUsd * 100)
    if nightly_cents <= 0:
        raise ValueError("Accommodation estimate exceeds supported precision.")
    return (nightly_cents * nights * rooms) / 100


def _plan(brief: TripBrief, ctx: AgentContext, revision: RevisionRequest | None) -> AgentProposal:
    prefs = read_preferences(ctx.mem.get_long_term(brief.userId))
    segments = split_stay(brief)
    rooms = (
        brief.groupSize
        if prefs.roomAllocation == "individual"
        else -(-brief.groupSize // GUESTS_PER_ROOM)
    )
    budget_revision = is_budget_revision(revision)

    items: list[ProposalItem] = []
    offered: dict[str, tuple[StaySegment, list[StayOption], StayOption]] = {}
    total = 0.0
    for segment in segments:
        options = ctx.tools.booking.search_stays(
            city=segment.city,
            check_in=segment.checkIn,
            check_out=segment.checkOut,
            guests=brief.groupSize,
        )
        eligible = eligible_options(options, prefs)
        if not eligible:
            continue
        picked_name = prefs.chosen.get(segment.city)
        confirmed = next((o for o in eligible if o.name == picked_name), None)
        if confirmed is not None:
            chosen = confirmed
        elif budget_revision:
            chosen = eligible[0]
        else:
            chosen = choose_initial(eligible)
        offered[segment.city] = (segment, eligible, chosen)
        cost = stay_cost(chosen, segment.nights, rooms)
        total += cost
        items.append(
            ProposalItem(
                kind="hotel",
                detail=(
                    f"{chosen.name} — {chosen.area}; {segment.checkIn} to {segment.checkOut}; "
                    f"{rooms} room(s) x {segment.nights} night(s) x USD "
                    f"{chosen.pricePerNightUsd:.2f} per room/night; rating {chosen.rating}; "
                    f"{'free' if chosen.freeCancellation else 'no free'} cancellation."
                ),
                estCost=cost,
                day=segment.day,
                location=chosen.name,
            )
        )

    if not items:
        return AgentProposal(
            agent="accommodation",
            summary=f"No eligible stay found for {brief.destination}.",
            items=[],
            assumptions=[
                (
                    "The booking port returned no option matching the confirmed "
                    "preferences; nothing was invented to fill the gap."
                )
            ],
        )

    nights = sum(s.nights for s in segments)
    allocation = (
        "One room per traveller"
        if prefs.roomAllocation == "individual"
        else f"{GUESTS_PER_ROOM} guests per room"
    )
    assumptions = [
        f"{allocation}, so {brief.groupSize} travellers need {rooms} room(s).",
        "Rates come from the booking port and are estimates, not reservations or availability.",
    ]
    if prefs.chosen:
        picked = ", ".join(f"{city}: {name}" for city, name in sorted(prefs.chosen.items()))
        assumptions.append(f"Honoured the stay you confirmed ({picked}).")
    else:
        assumptions.append(
            "Budget revision selected the lowest eligible nightly rate in each city."
            if budget_revision
            else "Preferred a free-cancellation option rated 8.0 or better where one was eligible."
        )

    ctx.extras.setdefault("stay_choices", {}).update(
        {
            city: [
                ChoiceOption(
                    id=option.name,
                    label=option.name,
                    detail=(
                        f"{option.area} · {option.rating}/10 · USD "
                        f"{option.pricePerNightUsd:.2f} per room/night · "
                        f"{'free' if option.freeCancellation else 'no free'} cancellation"
                    ),
                    estCost=stay_cost(option, segment.nights, rooms),
                    meta={
                        "city": city,
                        "nights": str(segment.nights),
                        "rating": str(option.rating),
                    },
                    recommended=option.name == chosen.name,
                )
                for option in eligible
            ]
            for city, (segment, eligible, chosen) in offered.items()
        }
    )

    return AgentProposal(
        agent="accommodation",
        summary=(
            f"{rooms} room(s), {nights} nights in {brief.destination} · USD {total:.2f}"
            f"{' (lowest eligible cost)' if budget_revision else ''}"
        ),
        items=items,
        assumptions=assumptions,
    )


accommodation_specialist = FunctionSpecialist("accommodation", "Stay", _plan)
