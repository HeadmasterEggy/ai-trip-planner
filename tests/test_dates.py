"""What people type instead of ISO dates.

The reader is allowed to guess, so the guesses have to be written down and pinned:
day-first for an ambiguous pair, and a missing year read as the next one that
works rather than one that has already passed.
"""

from __future__ import annotations

from datetime import date

import pytest

from trip_planner.dates import date_range

TODAY = date(2026, 9, 12)


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        # ISO and its near neighbours
        ("2026-10-01 to 2026-10-05", ("2026-10-01", "2026-10-05")),
        ("2026/10/1 - 2026/10/5", ("2026-10-01", "2026-10-05")),
        ("2026.10.01-2026.10.05", ("2026-10-01", "2026-10-05")),
        ("2026年10月1日 到 2026年10月5日", ("2026-10-01", "2026-10-05")),
        # month names, either way round
        ("Oct 9 to Oct 12", ("2026-10-09", "2026-10-12")),
        ("9 Oct 2026 - 12 Oct 2026", ("2026-10-09", "2026-10-12")),
        ("October 9, 2026 through October 12, 2026", ("2026-10-09", "2026-10-12")),
        ("9th of October to 12th of October", ("2026-10-09", "2026-10-12")),
        # a day range that leans on the month it follows
        ("Oct 9-12", ("2026-10-09", "2026-10-12")),
        # Chinese month/day
        ("10月9日 至 10月12日", ("2026-10-09", "2026-10-12")),
        # dotted pairs, day first when nothing says otherwise
        ("10.9-12.9", ("2027-09-10", "2027-09-12")),
        # ... unless the magnitude leaves only one reading
        ("25.12-28.12", ("2026-12-25", "2026-12-28")),
        ("12/25 - 12/28", ("2026-12-25", "2026-12-28")),
        # a range that crosses the new year
        ("28.12-3.1", ("2026-12-28", "2027-01-03")),
    ],
)
def test_the_shapes_people_actually_write(written, expected):
    assert date_range(written, today=TODAY) == expected


def test_a_destination_in_front_of_the_dates_does_not_confuse_it():
    assert date_range("Sydney, Oct 9 to Oct 12", today=TODAY) == ("2026-10-09", "2026-10-12")
    assert date_range("去悉尼 10月9日 到 10月12日", today=TODAY) == ("2026-10-09", "2026-10-12")


def test_an_ambiguous_pair_is_read_day_first():
    """The one rule the string cannot settle for itself.

    "10.9" is the 9th of October in the United States and the 10th of September
    in Australia, and nothing in the string says which. The app's clock is
    Australia/Sydney, so it reads day first -- and the reply says which it read,
    so the other reading is one sentence away from being corrected.
    """
    assert date_range("10.9 to 12.9", today=date(2026, 8, 1)) == ("2026-09-10", "2026-09-12")
    # Both ends read the same way, which is what makes it a range rather than a
    # coincidence: 9/10 is 9 October and 12/10 is 12 October.
    assert date_range("9/10-12/10", today=date(2026, 8, 1)) == ("2026-10-09", "2026-10-12")
    # With a year in the middle of it, the same reading applies.
    assert date_range("10.9.2026 to 12.9.2026", today=TODAY) == ("2026-09-10", "2026-09-12")


def test_a_missing_year_is_the_next_one_that_works():
    """A trip in the past is not a trip."""
    assert date_range("10.9-12.9", today=date(2026, 9, 12)) == ("2027-09-10", "2027-09-12")
    # ... but only when the traveller did not write the year down.
    assert date_range("2025-10-09 to 2025-10-12", today=TODAY) == (
        "2025-10-09",
        "2025-10-12",
    )


def test_one_date_is_not_a_range():
    """The other end is asked for rather than invented -- an unstated trip length
    is as much a guess as an unstated budget."""
    assert date_range("Oct 9", today=TODAY) is None
    assert date_range("10.9", today=TODAY) is None


def test_a_message_with_no_dates_gets_none():
    for text in ("somewhere warm", "budget 3000", "2 people", ""):
        assert date_range(text, today=TODAY) is None


def test_an_impossible_date_is_not_a_date():
    assert date_range("2026-02-30 to 2026-03-05", today=TODAY) is None
    assert date_range("32.13-35.14", today=TODAY) is None
