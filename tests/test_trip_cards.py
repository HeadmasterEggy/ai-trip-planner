"""What a Your-trips card is drawn from, tested without a browser."""

from __future__ import annotations

from types import SimpleNamespace

from trip_planner.ui.history import Conversation, cover_index
from trip_planner.ui.theme import COVERS


def trip(start: str, end: str) -> Conversation:
    plan = SimpleNamespace(brief=SimpleNamespace(destination="Sydney", dates=(start, end)))
    return Conversation(tripId="t", plan=plan)  # type: ignore[arg-type]


def test_days_counts_both_ends():
    assert trip("2025-12-01", "2025-12-05").days == 5
    assert trip("2025-12-01", "2025-12-01").days == 1


def test_a_chat_has_no_days():
    assert Conversation(tripId="c").days == 0


def test_a_cover_is_the_same_colour_every_time():
    assert cover_index("Sydney", COVERS) == cover_index(" sydney ", COVERS)
    assert all(0 <= cover_index(name, COVERS) < COVERS for name in ("Tokyo", "Lisbon", ""))
