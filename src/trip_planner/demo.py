"""A complete example trip, for scripts, tests and the documented API example.

The UI no longer starts from this. Its first screen is an empty conversation, so
a traveller never opens the app on someone else's trip and never has to delete
Tokyo before saying where they actually want to go. What remains is a convenient
full brief for a script or a test.

The dates are relative to today rather than fixed, so the example does not
describe a trip that has already happened.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from .contracts import TripBrief

TRIP_START_OFFSET_DAYS = 60
TRIP_LENGTH_DAYS = 7


def demo_brief(today: date | None = None) -> TripBrief:
    # Anchor to a real timezone rather than the server's local clock.
    start = (today or datetime.now(UTC).date()) + timedelta(days=TRIP_START_OFFSET_DAYS)
    return TripBrief(
        tripId="trip-demo",
        userId="demo-user",
        destination="Tokyo & Kyoto",
        dates=(start.isoformat(), (start + timedelta(days=TRIP_LENGTH_DAYS)).isoformat()),
        groupSize=2,
        budgetTotal=4000,
        nationality="Australian",
    )


DEMO_BRIEF = demo_brief()
