"""In-process preference memory.

Mirrors `packages/services/src/memory`. Short-term is per trip, long-term is
per user. `promote` moves a confirmed short-term fact into the long-term
profile, which is what lets dining and transport read a preference the user
stated in an earlier turn.
"""

from __future__ import annotations

from .contracts import ChatTurn, UserPreference

# Bounds, because this store lives for the life of the process. A long-running
# deployment would otherwise keep every trip's transcript and every user's
# preferences forever; the oldest thread is dropped when the cap is reached.
MAX_TRIPS = 200
MAX_USERS = 200
MAX_TURNS_PER_TRIP = 200


class InMemoryStore:
    """Short-term per trip, long-term per user, both bounded.

    Keyed by `tripId` and `userId`, so isolation is the caller's responsibility: the
    UI gives every session its own pair (`streamlit_app.py`), which is what keeps two
    visitors' preferences apart on a shared deployment.
    """

    def __init__(self) -> None:
        self._short: dict[str, list[ChatTurn]] = {}
        self._long: dict[str, list[UserPreference]] = {}

    def get_short_term(self, trip_id: str) -> list[ChatTurn]:
        return list(self._short.get(trip_id, []))

    def append_short_term(self, trip_id: str, turn: ChatTurn) -> None:
        turns = self._short.setdefault(trip_id, [])
        turns.append(turn)
        del turns[:-MAX_TURNS_PER_TRIP]
        self._evict(self._short, MAX_TRIPS)

    @staticmethod
    def _evict(buckets: dict, cap: int) -> None:
        """Drop the oldest bucket, so a long-lived process stays bounded."""
        while len(buckets) > cap:
            del buckets[next(iter(buckets))]

    def get_long_term(self, user_id: str) -> list[UserPreference]:
        return list(self._long.get(user_id, []))

    def set_long_term(self, user_id: str, pref: UserPreference) -> None:
        kept = [p for p in self._long.get(user_id, []) if p.key != pref.key]
        self._long[user_id] = [*kept, pref]
        self._evict(self._long, MAX_USERS)

    def promote(self, trip_id: str, user_id: str, key: str) -> None:
        for turn in reversed(self.get_short_term(trip_id)):
            if key in turn.content:
                self.set_long_term(
                    user_id, UserPreference(key=key, value=turn.content, source="chat_confirmed")
                )
                return


memory = InMemoryStore()
