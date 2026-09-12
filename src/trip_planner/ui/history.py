"""The rail's conversation list.

Session state holds one conversation at a time -- the one on screen -- and this
module holds the rest: enough to list them, order them, filter them and put one
back. Plain functions over plain data, because the ways a list like this goes
wrong are ordering and duplication, and those are worth testing without a
browser.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..contracts import BriefPatch, TripPlan

UNTITLED = "Untitled"
TRIP_ICON = "🧳"
CHAT_ICON = "💬"
# A row is one line, so a long opening message is cut rather than left to the
# stylesheet: the ellipsis has to survive being copied into a title attribute.
TITLE_CHARS = 42
# The rail is session state, which lives as long as the tab. Bounded like the
# memory store so a long-lived session cannot grow without limit.
MAX_HISTORY = 50


@dataclass
class Conversation:
    """One conversation, as the rail needs it: enough to list it and reopen it."""

    tripId: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    draft: BriefPatch = field(default_factory=BriefPatch)
    plan: TripPlan | None = None

    @property
    def is_trip(self) -> bool:
        """A conversation that produced a plan is a trip."""
        return self.plan is not None

    @property
    def icon(self) -> str:
        return TRIP_ICON if self.is_trip else CHAT_ICON

    @property
    def title(self) -> str:
        """Where it goes if that is known, otherwise what was asked for."""
        if self.plan is not None:
            return self.plan.brief.destination
        opening = next(
            (
                str(message.get("content", ""))
                for message in self.messages
                if message.get("role") == "user" and message.get("content")
            ),
            "",
        )
        opening = " ".join(opening.split())
        if not opening:
            return UNTITLED
        if len(opening) <= TITLE_CHARS:
            return opening
        return opening[: TITLE_CHARS - 1].rstrip() + "…"

    @property
    def subtitle(self) -> str:
        if self.plan is None:
            return "In progress"
        start, end = self.plan.brief.dates
        return f"{start} – {end}"

    @property
    def days(self) -> int:
        """Calendar days the trip covers, both ends included; 0 before a plan."""
        if self.plan is None:
            return 0
        start, end = (date.fromisoformat(value) for value in self.plan.brief.dates)
        return (end - start).days + 1


def cover_index(destination: str, slots: int) -> int:
    """A stable palette slot for a trip card's cover.

    `hash()` is salted per process, so a card would change colour on every
    restart; a checksum of the name keeps Sydney the same colour every time.
    """
    return zlib.crc32(destination.strip().casefold().encode()) % slots


def snapshot(
    trip_id: str,
    messages: list[dict[str, Any]],
    draft: BriefPatch,
    plan: TripPlan | None = None,
) -> Conversation:
    """A `Conversation` that will not change when the live one does.

    `messages` is the only container the caller keeps appending to, so it is the
    only thing that has to be copied. Without this every snapshot shares one list,
    and yesterday's conversation grows with today's turns.
    """
    return Conversation(tripId=trip_id, messages=list(messages), draft=draft, plan=plan)


def remember(
    history: list[Conversation], conversation: Conversation, limit: int = MAX_HISTORY
) -> list[Conversation]:
    """`history` with `conversation` upserted at the front, newest first."""
    kept = [conversation, *(c for c in history if c.tripId != conversation.tripId)]
    return kept[:limit]


def forget(history: list[Conversation], trip_id: str) -> list[Conversation]:
    """`history` without that conversation: it is open now, so it is not a row."""
    return [c for c in history if c.tripId != trip_id]


def split(history: list[Conversation]) -> tuple[list[Conversation], list[Conversation]]:
    """`(trips, chats)`, keeping the newest-first order inside each."""
    return ([c for c in history if c.is_trip], [c for c in history if not c.is_trip])


def matching(history: list[Conversation], query: str) -> list[Conversation]:
    """The conversations whose row, or whose transcript, contains `query`.

    The transcript is searched too, because the title is truncated for display and
    a traveller looking for a word that fell off the end of it would otherwise be
    told the conversation does not exist.
    """
    needle = query.strip().casefold()
    if not needle:
        return list(history)
    return [c for c in history if needle in _haystack(c)]


def _haystack(conversation: Conversation) -> str:
    parts = [conversation.title, conversation.subtitle]
    parts += [str(message.get("content", "")) for message in conversation.messages]
    return " ".join(parts).casefold()
