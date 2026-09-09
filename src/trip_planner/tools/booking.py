"""Deterministic booking/price fixtures. No reservations, no payments.

Stay prices are USD per room per night assuming at most two guests per room.
Flight prices cover ALL passengers and include both legs when returning.
These are fictional fixtures, not quotes or availability.
"""

from __future__ import annotations

from datetime import date

from ..ports import FlightOption, StayOption

# Expensive Tokyo/Kyoto rooms keep the budget-negotiation demo meaningful.
NIGHTLY_RATES: dict[str, tuple[float, float, float]] = {
    "tokyo": (240, 380, 520),
    "kyoto": (220, 360, 490),
    "sydney": (150, 230, 330),
    "paris": (160, 250, 360),
}


def _date_value(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Booking requires a valid YYYY-MM-DD date: {value}") from exc


def _require_count(count: int) -> None:
    if not isinstance(count, int) or count <= 0:
        raise ValueError("Booking requires a positive integer guest/passenger count.")


class MockBooking:
    def search_stays(
        self, *, city: str, check_in: str, check_out: str, guests: int
    ) -> list[StayOption]:
        _require_count(guests)
        if _date_value(check_out) <= _date_value(check_in):
            raise ValueError("Check-out must be after check-in.")
        name = city.strip()
        if not name:
            raise ValueError("A city is required for a stay search.")
        economy, standard, comfort = NIGHTLY_RATES.get(name.lower(), (100, 180, 280))
        return [
            StayOption(f"Mock {name} Economy", "Outer district", economy, 7.6, True),
            StayOption(f"Mock {name} Standard", "Central", standard, 8.7, True),
            StayOption(f"Mock {name} Comfort", "Central", comfort, 9.3, True),
            StayOption(f"Mock {name} Saver", "Outer district", economy - 20, 7.2, False),
        ]

    def search_flights(
        self, *, frm: str, to: str, depart: str, ret: str | None, passengers: int
    ) -> list[FlightOption]:
        _require_count(passengers)
        _date_value(depart)
        if ret and _date_value(ret) <= _date_value(depart):
            raise ValueError("Return must be after departure.")
        origin, dest = frm.strip(), to.strip()
        if not origin or not dest or origin.lower() == dest.lower():
            raise ValueError("Flight origin and destination must be distinct, non-empty.")
        legs = 1 if ret is None else 2
        note = (
            f"{origin} {'<->' if legs == 2 else '->'} {dest}; {passengers} passengers; "
            f"{'round-trip' if legs == 2 else 'one-way'} group total in USD; "
            "fictional mock fare"
        )
        return [
            FlightOption("MockAir Economy", 310.0 * passengers * legs, note),
            FlightOption("MockAir Flexible", 420.0 * passengers * legs, note),
        ]
