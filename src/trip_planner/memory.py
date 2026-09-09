"""In-process preference memory.

Mirrors `packages/services/src/memory`. Short-term is per trip, long-term is
per user. `promote` moves a confirmed short-term fact into the long-term
profile, which is what lets dining and transport read a preference the user
stated in an earlier turn.
"""

from __future__ import annotations

from .contracts import ChatTurn, UserPreference


class InMemoryStore:
    def __init__(self) -> None:
        self._short: dict[str, list[ChatTurn]] = {}
        self._long: dict[str, list[UserPreference]] = {}

    def get_short_term(self, trip_id: str) -> list[ChatTurn]:
        return list(self._short.get(trip_id, []))

    def append_short_term(self, trip_id: str, turn: ChatTurn) -> None:
        self._short.setdefault(trip_id, []).append(turn)

    def get_long_term(self, user_id: str) -> list[UserPreference]:
        return list(self._long.get(user_id, []))

    def set_long_term(self, user_id: str, pref: UserPreference) -> None:
        kept = [p for p in self._long.get(user_id, []) if p.key != pref.key]
        self._long[user_id] = [*kept, pref]

    def promote(self, trip_id: str, user_id: str, key: str) -> None:
        for turn in reversed(self.get_short_term(trip_id)):
            if key in turn.content:
                self.set_long_term(
                    user_id, UserPreference(key=key, value=turn.content, source="chat_confirmed")
                )
                return


memory = InMemoryStore()
