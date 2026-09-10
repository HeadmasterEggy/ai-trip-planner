"""Transport specialist.

Fares and legs come from the booking and maps ports, never from a model, so
prices and routes cannot be invented. The trip's inter-city legs are scheduled
so the orchestrator can detect overlaps against itinerary activities.
"""

from __future__ import annotations

from ..contracts import AgentProposal, ProposalItem, RevisionRequest, TripBrief
from ..ports import AgentContext
from .base import (
    FunctionSpecialist,
    cities,
    clock,
    is_budget_revision,
    is_schedule_revision,
    record_trace,
    trip_days,
)


def _plan(brief: TripBrief, ctx: AgentContext, revision: RevisionRequest | None) -> AgentProposal:
    destinations = cities(brief.destination)
    days = trip_days(brief.dates)
    preferences = ctx.mem.get_long_term(brief.userId)
    origin = next((p.value.strip() for p in preferences if p.key == "transport.origin"), "Sydney")
    budget_revision = is_budget_revision(revision)
    schedule_revision = is_schedule_revision(revision)

    items: list[ProposalItem] = []
    assumptions = [
        "Fares and routes come from the booking and maps ports; the model never prices a leg.",
        "Flight prices cover the whole group; mock fixtures are not quotes or availability.",
    ]

    if origin.lower() != destinations[0].lower():
        flights = ctx.tools.booking.search_flights(
            frm=origin,
            to=destinations[0],
            depart=brief.dates[0],
            ret=brief.dates[1],
            passengers=brief.groupSize,
        )
        if flights:
            # A budget revision takes the cheapest fare; otherwise prefer a
            # changeable one, which is what a traveller usually wants.
            chosen = (
                min(flights, key=lambda f: f.priceUsd)
                if budget_revision
                else next((f for f in flights if "flex" in f.carrier.lower()), flights[0])
            )
            items.append(
                ProposalItem(
                    kind="transport",
                    detail=f"{chosen.carrier}: {origin} to {destinations[0]}. {chosen.note or ''}".strip(),
                    estCost=chosen.priceUsd,
                    day=1,
                    location=f"{origin} → {destinations[0]}",
                )
            )
            if budget_revision:
                assumptions.append("Budget revision selected the lowest returned fare.")

    # Inter-city legs, spread across the trip.
    cursor = 6 * 60 if schedule_revision else 9 * 60
    for index, destination in enumerate(destinations[1:]):
        frm = destinations[index]
        day = min(days, (days * (index + 1)) // len(destinations) + 1)
        legs = ctx.tools.maps.route(frm=frm, to=destination, date=brief.dates[0])
        if not legs:
            continue
        leg = legs[0]
        start = cursor
        end = start + leg.durationMin
        items.append(
            ProposalItem(
                kind="transport",
                detail=(
                    f"{leg.mode} from {frm} to {destination}; {leg.durationMin} minutes"
                    f"{f'. {leg.note}' if leg.note else ''}"
                ),
                estCost=leg.priceUsd,
                day=day,
                startTime=clock(start),
                endTime=clock(end),
                location=f"{frm} → {destination}",
            )
        )
        if schedule_revision:
            assumptions.append(
                f"Schedule revision moved the day {day} leg earlier to clear the conflict."
            )

    record_trace(
        ctx,
        "transport",
        "calculator",
        evidence={
            "origin": origin,
            "cities": " → ".join(destinations),
            "trip days": str(days),
            "fares": "booking port"
            if origin.lower() != destinations[0].lower()
            else "no flight leg",
            "routes": "maps port",
        },
        revision=revision,
        notes=["Fares and routes come from ports; no model prices a leg."],
    )

    return AgentProposal(
        agent="transport",
        summary=(
            f"{len(items)} transport option(s) for {origin} ⇄ {' → '.join(destinations)}"
            f" · USD {sum(i.estCost or 0 for i in items):.2f}"
        ),
        items=items,
        assumptions=assumptions,
    )


transport_specialist = FunctionSpecialist("transport", "Getting around", _plan)
