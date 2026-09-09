"""The brief a fresh session starts from."""

from __future__ import annotations

from .contracts import TripBrief

DEMO_BRIEF = TripBrief(
    tripId="trip-demo",
    userId="demo-user",
    destination="Tokyo & Kyoto",
    dates=("2026-06-15", "2026-06-22"),
    groupSize=2,
    budgetTotal=4000,
    nationality="Australian",
)
