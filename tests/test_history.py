"""The rail's conversation list: what a row says, and what it must not do.

The ways a list like this goes wrong are ordering, duplication and a snapshot
that quietly shares state with the live conversation -- all three are cheap to
test here and expensive to notice in a browser.
"""

from __future__ import annotations

from trip_planner.contracts import BriefPatch, TripBrief, TripPlan
from trip_planner.ui.history import (
    CHAT_ICON,
    MAX_HISTORY,
    TITLE_CHARS,
    TRIP_ICON,
    UNTITLED,
    forget,
    matching,
    remember,
    snapshot,
    split,
)


def say(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def trip(destination: str = "Kyoto") -> TripPlan:
    brief = TripBrief(
        tripId="t1",
        destination=destination,
        dates=("2026-10-01", "2026-10-05"),
        groupSize=2,
        budgetTotal=3000,
    )
    return TripPlan(
        tripId="t1",
        brief=brief,
        round=1,
        budgetTotal=3000,
        estTotal=2500,
        overrunPct=-16.67,
        sections=[],
        hitl=[],
    )


def test_a_conversation_with_a_plan_is_named_after_where_it_goes():
    live = snapshot("t1", [say("user", "somewhere cold please")], BriefPatch(), trip("Kyoto"))

    assert live.title == "Kyoto"
    assert live.subtitle == "2026-10-01 – 2026-10-05"
    assert live.is_trip
    assert live.icon == TRIP_ICON


def test_a_conversation_without_a_plan_is_named_after_its_first_message():
    live = snapshot("t1", [say("user", "five days in Lisbon"), say("assistant", "…")], BriefPatch())

    assert live.title == "five days in Lisbon"
    assert live.subtitle == "In progress"
    assert not live.is_trip
    assert live.icon == CHAT_ICON


def test_a_conversation_nobody_has_spoken_in_is_untitled():
    assert snapshot("t1", [say("assistant", "hello")], BriefPatch()).title == UNTITLED
    assert snapshot("t1", [], BriefPatch()).title == UNTITLED


def test_a_long_opening_message_is_cut_to_one_line():
    live = snapshot("t1", [say("user", "x" * 200)], BriefPatch())

    assert live.title.endswith("…")
    assert len(live.title) == TITLE_CHARS


def test_whitespace_in_the_opening_message_is_collapsed():
    """The title goes into a single-line row, so a pasted paragraph must not
    arrive with its newlines."""
    live = snapshot("t1", [say("user", "  two\n\nlines  ")], BriefPatch())

    assert live.title == "two lines"


def test_a_snapshot_does_not_follow_the_live_conversation():
    """The live `messages` list is appended to on every turn. A snapshot that
    shared it would make yesterday's conversation grow with today's turns."""
    messages = [say("user", "Tokyo")]
    live = snapshot("t1", messages, BriefPatch())

    messages.append(say("assistant", "later"))

    assert len(live.messages) == 1


def test_remember_puts_the_newest_first_and_never_duplicates():
    first = snapshot("t1", [say("user", "one")], BriefPatch())
    second = snapshot("t2", [say("user", "two")], BriefPatch())

    history = remember(remember([], first), second)
    assert [c.tripId for c in history] == ["t2", "t1"]

    # Re-opening writes the conversation back rather than adding a second row.
    again = remember(history, first)
    assert [c.tripId for c in again] == ["t1", "t2"]


def test_history_is_bounded():
    history = []
    for index in range(MAX_HISTORY + 5):
        history = remember(history, snapshot(f"t{index}", [say("user", str(index))], BriefPatch()))

    assert len(history) == MAX_HISTORY
    assert history[0].tripId == f"t{MAX_HISTORY + 4}"


def test_trips_and_chats_are_split_without_reordering():
    history = [
        snapshot("t1", [say("user", "one")], BriefPatch(), trip("Kyoto")),
        snapshot("t2", [say("user", "two")], BriefPatch()),
        snapshot("t3", [say("user", "three")], BriefPatch(), trip("Lisbon")),
    ]

    trips, chats = split(history)

    assert [c.tripId for c in trips] == ["t1", "t3"]
    assert [c.tripId for c in chats] == ["t2"]


def test_forget_removes_only_that_conversation():
    history = [
        snapshot("t1", [say("user", "one")], BriefPatch()),
        snapshot("t2", [say("user", "two")], BriefPatch()),
    ]

    assert [c.tripId for c in forget(history, "t1")] == ["t2"]


def test_search_matches_the_title_the_dates_and_the_transcript():
    kyoto = snapshot("t1", [say("user", "somewhere cold")], BriefPatch(), trip("Kyoto"))
    lisbon = snapshot("t2", [say("user", "the tiles of Lisbon")], BriefPatch())

    assert matching([kyoto, lisbon], "kyoto") == [kyoto]  # title, case-insensitive
    assert matching([kyoto, lisbon], "2026-10-05") == [kyoto]  # the row's subtitle
    assert matching([kyoto, lisbon], "tiles") == [lisbon]  # the transcript


def test_search_finds_a_word_that_was_cut_off_the_title():
    """The title is truncated for display. Searching would otherwise fail for a
    word the traveller can see in the conversation but not in the row."""
    live = snapshot("t1", [say("user", "a" * 80 + " needle")], BriefPatch())

    assert "needle" not in live.title
    assert matching([live], "needle") == [live]


def test_search_reads_chinese_too():
    live = snapshot("t1", [say("user", "去京都旅行，预算 5000")], BriefPatch())

    assert matching([live], "京都") == [live]
    assert matching([live], "大阪") == []


def test_an_empty_search_matches_everything():
    history = [snapshot("t1", [say("user", "one")], BriefPatch())]

    assert matching(history, "") == history
    assert matching(history, "   ") == history
