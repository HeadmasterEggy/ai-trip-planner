"""Chat intake: what a message is allowed to change, and what it must not."""

from __future__ import annotations

import pytest

from trip_planner.chat import (
    BriefPatch,
    apply_brief_patch,
    changed_fields,
    extract_brief_patch_locally,
    fallback_reply_for,
    run_trip_chat,
)
from trip_planner.contracts import ChatRequest, TripBrief
from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.workflow import OrchestratorOptions


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Kyoto, 2026-10-01 to 2026-10-05, 2 people, budget $3000",
            {
                "destination": "Kyoto",
                "dates": ("2026-10-01", "2026-10-05"),
                "groupSize": 2,
                "budgetTotal": 3000.0,
            },
        ),
        ("make it 3 people", {"groupSize": 3}),
        ("a trip to Lisbon for 4 people", {"destination": "Lisbon", "groupSize": 4}),
        (
            "去大阪旅行，预算 5000，三个人",
            {"destination": "大阪", "groupSize": 3, "budgetTotal": 5000.0},
        ),
        ("Australian passport", {"nationality": "Australian"}),
    ],
)
def test_the_local_parser_reads_explicit_updates(message, expected):
    assert extract_brief_patch_locally(message).model_dump(exclude_none=True) == expected


def test_the_local_parser_infers_nothing_from_an_unrelated_message():
    # Inferring a date or budget the traveller never gave would silently move
    # the plan away from what they asked for.
    assert extract_brief_patch_locally("hello there").model_dump(exclude_none=True) == {}


def test_a_patch_only_changes_the_fields_it_carries():
    after = apply_brief_patch(DEMO_BRIEF, BriefPatch(groupSize=5), DEMO_BRIEF.tripId)
    assert after.groupSize == 5
    assert after.destination == DEMO_BRIEF.destination
    assert changed_fields(DEMO_BRIEF, after) == ["groupSize"]


def test_impossible_dates_are_rejected_once_not_five_times():
    with pytest.raises(ValueError, match="after the start date"):
        apply_brief_patch(
            DEMO_BRIEF, BriefPatch(dates=("2026-06-22", "2026-06-15")), DEMO_BRIEF.tripId
        )
    with pytest.raises(ValueError, match="real dates"):
        apply_brief_patch(
            DEMO_BRIEF, BriefPatch(dates=("2026-02-30", "2026-03-05")), DEMO_BRIEF.tripId
        )


def test_a_multi_city_trip_shorter_than_its_cities_is_refused_here():
    """The "&" convention is only plannable with a night per city.

    It used to be discovered inside the accommodation specialist, after the
    other four had run, so the traveller saw an internal error instead of a
    reason they could act on.
    """
    with pytest.raises(ValueError, match="2 destinations need at least 2 nights"):
        apply_brief_patch(
            DEMO_BRIEF, BriefPatch(dates=("2026-06-15", "2026-06-16")), DEMO_BRIEF.tripId
        )


def test_a_single_city_trip_still_works_on_one_night():
    after = apply_brief_patch(
        DEMO_BRIEF,
        BriefPatch(destination="Kyoto", dates=("2026-06-15", "2026-06-16")),
        DEMO_BRIEF.tripId,
    )
    assert after.destination == "Kyoto"
    assert after.dates == ("2026-06-15", "2026-06-16")


def test_a_chat_turn_replans_and_records_both_sides():
    mem = InMemoryStore()
    response = run_trip_chat(
        ChatRequest(tripId="t1", message="make it 4 people", brief=DEMO_BRIEF),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=mem, max_rounds=1),
        extractor=None,
        reply_generator=lambda prompt: "ok",
    )
    assert response.plan.brief.groupSize == 4
    assert response.reply == "ok"
    turns = mem.get_short_term("t1")
    assert [t.role for t in turns] == ["user", "assistant"]


def test_the_reply_falls_back_to_the_plan_when_no_model_answers():
    mem = InMemoryStore()

    def failing_generator(prompt: str) -> str:
        raise RuntimeError("model down")

    response = run_trip_chat(
        ChatRequest(tripId="t2", message="make it 4 people", brief=DEMO_BRIEF),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=mem, max_rounds=1),
        reply_generator=failing_generator,
    )
    # A reply failure must not lose the plan.
    assert response.plan is not None
    assert response.reply == fallback_reply_for(response.plan)


def test_an_injected_extractor_is_preferred_over_the_local_parser():
    class FixedExtractor:
        def extract(self, message: str, current: TripBrief) -> BriefPatch:
            return BriefPatch(destination="Porto")

    response = run_trip_chat(
        ChatRequest(tripId="t3", message="anything", brief=DEMO_BRIEF),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        extractor=FixedExtractor(),
        reply_generator=lambda prompt: "ok",
    )
    assert response.plan.brief.destination == "Porto"
