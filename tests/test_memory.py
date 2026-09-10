"""The in-process store: what it keys on, and that it stays bounded.

It lives for the life of the process, so the caps are part of its contract: a
deployment that keeps every trip's transcript and every user's preferences forever
is a leak with a friendly name. Isolation is by key, which is why the UI gives each
session its own pair.
"""

from __future__ import annotations

from trip_planner.contracts import ChatTurn, UserPreference
from trip_planner.memory import MAX_TRIPS, MAX_TURNS_PER_TRIP, MAX_USERS, InMemoryStore


def turn(content: str = "x") -> ChatTurn:
    return ChatTurn(role="user", content=content)


def pref(value: str = "v") -> UserPreference:
    return UserPreference(key="k", value=value, source="chat_confirmed")


def test_preferences_are_kept_per_user():
    store = InMemoryStore()
    store.set_long_term("a", pref("vegetarian"))
    store.set_long_term("b", pref("halal"))

    assert [p.value for p in store.get_long_term("a")] == ["vegetarian"]
    assert [p.value for p in store.get_long_term("b")] == ["halal"]


def test_setting_a_key_replaces_it_rather_than_appending():
    store = InMemoryStore()
    store.set_long_term("u", pref("first"))
    store.set_long_term("u", pref("second"))

    assert [p.value for p in store.get_long_term("u")] == ["second"]


def test_a_trip_keeps_only_its_most_recent_turns():
    store = InMemoryStore()
    for index in range(MAX_TURNS_PER_TRIP + 5):
        store.append_short_term("t", turn(str(index)))

    turns = store.get_short_term("t")
    assert len(turns) == MAX_TURNS_PER_TRIP
    assert turns[0].content == "5"  # the oldest were dropped
    assert turns[-1].content == str(MAX_TURNS_PER_TRIP + 4)


def test_the_oldest_trip_is_evicted_at_the_cap():
    store = InMemoryStore()
    for index in range(MAX_TRIPS + 3):
        store.append_short_term(f"t{index}", turn())

    assert store.get_short_term("t0") == []
    assert store.get_short_term(f"t{MAX_TRIPS + 2}")  # the newest survives


def test_the_oldest_user_is_evicted_at_the_cap():
    store = InMemoryStore()
    for index in range(MAX_USERS + 2):
        store.set_long_term(f"u{index}", pref())

    assert store.get_long_term("u0") == []
    assert store.get_long_term(f"u{MAX_USERS + 1}")
