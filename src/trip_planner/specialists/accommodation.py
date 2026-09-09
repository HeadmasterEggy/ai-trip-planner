"""Accommodation specialist.

Deliberately deterministic: dates, preference filtering, room counts and
costing live in this module, so a model can narrate a stay but never price
one. A multi-city trip is split into contiguous per-city segments, which is
why the total is not simply one city's rate times the whole trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..contracts import AgentProposal, ProposalItem, RevisionRequest, TripBrief, UserPreference
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
    return StayPreferences(allocation, rating, cancellation == "true")


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
        chosen = eligible[0] if budget_revision else choose_initial(eligible)
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
    assumptions.append(
        "Budget revision selected the lowest eligible nightly rate in each city."
        if budget_revision
        else "Preferred a free-cancellation option rated 8.0 or better where one was eligible."
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
