"""What the traveller typed, as the ISO dates the contract wants.

`TripBrief.dates` is strict — every cost rule and every specialist reads it — but
people do not type ISO dates, and answering "10.9-12.9" with "dates must be
YYYY-MM-DD" is the app being pedantic about a format it can work out for itself.
This is the one place that guesses, and a guess has to be a rule rather than a
mood:

* **Day first.** "10.9" is the 9th of October in Australia and the 10th of
  September in the United States, and nothing in the string says which. The app's
  clock is Australia/Sydney, so day-first is the convention it already assumes —
  and when either part is larger than 12 there is nothing left to resolve.
* **A missing year is the next one that works.** "10.9" said in September means
  next year, not a trip that has already happened. An explicit year is never
  rewritten.

Both rules are visible to the traveller rather than hidden: the reply states the
dates it understood, so a misreading is one sentence away from being corrected.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))

# What separates the two ends of a range. `-` inside a date ("2026-10-01") is not
# one of these: a range only counts when a whole date sits on each side.
SEPARATOR = re.compile(r"\s*(?:-|–|—|~|～|to|until|through|至|到)\s*", re.IGNORECASE)

_YMD = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?")
_PAIR_YEAR = re.compile(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})")
_CN_MD = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_EN_MD = re.compile(
    rf"({_MONTH})\.?\s*(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{4}}))?", re.IGNORECASE
)
_EN_DM = re.compile(
    rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\.?(?:\s*,?\s*(\d{{4}}))?", re.IGNORECASE
)
# The lookarounds matter: without them "2026-02-30" offers up "26-02" as a
# second, bogus date, and a bad year silently becomes a good date.
_PAIR = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)")
_DAY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?(?!\d)")


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _order(first: int, second: int) -> tuple[int, int]:
    """`(day, month)` from a two-part date, using magnitude before convention."""
    if first > 12 >= second:
        return first, second
    if second > 12 >= first:
        return second, first
    return first, second  # day first


def _read(kind: str, match: re.Match, fallback_year: int) -> tuple[date, bool] | None:
    """`(date, year was written down)` for one pattern match."""
    if kind == "ymd":
        return _pair(_make(int(match[1]), int(match[2]), int(match[3])), True)
    if kind == "pair_year":
        day, month = _order(int(match[1]), int(match[2]))
        return _pair(_make(int(match[3]), month, day), True)
    if kind == "cn_md":
        return _pair(_make(fallback_year, int(match[1]), int(match[2])), False)
    if kind == "en_md":
        year = int(match[3]) if match[3] else fallback_year
        return _pair(_make(year, MONTHS[match[1].lower()], int(match[2])), bool(match[3]))
    if kind == "en_dm":
        year = int(match[3]) if match[3] else fallback_year
        return _pair(_make(year, MONTHS[match[2].lower()], int(match[1])), bool(match[3]))
    day, month = _order(int(match[1]), int(match[2]))
    return _pair(_make(fallback_year, month, day), False)


def _pair(value: date | None, explicit: bool) -> tuple[date, bool] | None:
    return None if value is None else (value, explicit)


_PATTERNS = (
    ("ymd", _YMD),
    ("pair_year", _PAIR_YEAR),
    ("cn_md", _CN_MD),
    ("en_md", _EN_MD),
    ("en_dm", _EN_DM),
    ("pair", _PAIR),
)


def find(text: str, *, today: date, month: int | None = None) -> list[tuple[int, int, date, bool]]:
    """Every date in `text`, as `(start, end, value, year was written)`.

    `month` fills in a bare day -- "Oct 9-12" is one date followed by a day, and
    only the first half says which month.
    """
    found: list[tuple[int, int, date, bool]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            read = _read(kind, match, today.year)
            if read is not None:
                found.append((match.start(), match.end(), read[0], read[1]))
    if month is not None:
        for match in _DAY.finditer(text):
            value = _make(today.year, month, int(match[1]))
            if value is not None:
                found.append((match.start(), match.end(), value, False))

    # Longest match at each position, and never two overlapping dates.
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, date, bool]] = []
    for item in found:
        if kept and item[0] < kept[-1][1]:
            continue
        kept.append(item)
    return kept


def _next_year(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:  # 29 February
        return value.replace(year=value.year + 1, day=28)


def date_range(text: str, *, today: date | None = None) -> tuple[str, str] | None:
    """The two ends of a range in `text`, as ISO dates, or None.

    One date on its own is not a range: the app asks for the other end rather
    than inventing a trip length, which is the same reason an unstated budget is
    asked for rather than guessed.
    """
    today = today or datetime.now(UTC).date()
    found = find(text, today=today)
    if not found:
        return None

    start, explicit = found[0][2], found[0][3]
    end: date | None = None
    if len(found) > 1:
        between = text[found[0][1] : found[-1][0]]
        if SEPARATOR.fullmatch(between):
            end, explicit_end = found[-1][2], found[-1][3]
            explicit = explicit or explicit_end
    if end is None:
        # "Oct 9-12": a day on its own, completed by the month just read.
        tail = text[found[0][1] :]
        for separator in SEPARATOR.finditer(tail):
            after = find(tail[separator.end() :], today=today, month=start.month)
            if after:
                end = after[0][2]
                break
    if end is None:
        return None

    if end < start:
        end = _next_year(end)
    if not explicit and start < today:
        start, end = _next_year(start), _next_year(end)
    return start.isoformat(), end.isoformat()
