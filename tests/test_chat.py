"""Chat intake: what a message is allowed to change, and what it must not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trip_planner.chat import (
    changed_fields,
    extract_brief_patch_locally,
    fallback_question_for,
    fallback_reply_for,
    run_trip_chat,
)
from trip_planner.contracts import (
    BriefPatch,
    ChatRequest,
    brief_from_draft,
    draft_problem,
    merge_draft,
    missing_fields,
)
from trip_planner.demo import DEMO_BRIEF
from trip_planner.memory import InMemoryStore
from trip_planner.specialists import ALL_SPECIALISTS
from trip_planner.workflow import OrchestratorOptions


def complete_draft() -> BriefPatch:
    """The demo trip as the partial layer the conversation accumulates."""
    return BriefPatch.from_brief(DEMO_BRIEF)


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
    after = merge_draft(complete_draft(), BriefPatch(groupSize=5))
    assert after.groupSize == 5
    assert after.destination == DEMO_BRIEF.destination
    assert changed_fields(complete_draft(), after) == ["groupSize"]


def test_a_required_field_is_missing_until_it_is_stated():
    assert missing_fields(None) == ["destination", "dates", "groupSize", "budgetTotal"]
    assert missing_fields(BriefPatch(destination="Kyoto")) == ["dates", "groupSize", "budgetTotal"]
    # Nationality is never asked for: it changes a visa note, not the plan.
    assert missing_fields(complete_draft()) == []


def test_a_partial_draft_is_not_a_brief():
    with pytest.raises(ValueError, match="still missing dates"):
        brief_from_draft(BriefPatch(destination="Kyoto"), trip_id="t1", user_id="u1")

    brief = brief_from_draft(complete_draft(), trip_id="t1", user_id="u1")
    assert brief.tripId == "t1" and brief.userId == "u1"


def test_impossible_dates_are_reported_once_not_five_times():
    # Checked before the draft is complete, so the traveller hears about a bad
    # span when they say it rather than after three more questions.
    reversed_span = merge_draft(complete_draft(), BriefPatch(dates=("2026-06-22", "2026-06-15")))
    assert draft_problem(reversed_span) == "Trip end date must be after the start date."

    unreal = merge_draft(complete_draft(), BriefPatch(dates=("2026-02-30", "2026-03-05")))
    assert draft_problem(unreal) == "Trip dates must be real dates in YYYY-MM-DD format."


def test_a_multi_city_trip_shorter_than_its_cities_is_refused_here():
    """The "&" convention is only plannable with a night per city.

    It used to be discovered inside the accommodation specialist, after the
    other four had run, so the traveller saw an internal error instead of a
    reason they could act on.
    """
    two_cities_one_night = merge_draft(
        BriefPatch(destination="Tokyo & Kyoto"), BriefPatch(dates=("2026-06-15", "2026-06-16"))
    )
    assert draft_problem(two_cities_one_night) == (
        "2 destinations need at least 2 nights; this trip is 1 night(s) long."
    )


def test_a_single_city_trip_still_works_on_one_night():
    one_city = merge_draft(
        complete_draft(), BriefPatch(destination="Kyoto", dates=("2026-06-15", "2026-06-16"))
    )
    assert draft_problem(one_city) is None


def test_a_chat_turn_replans_and_records_both_sides():
    mem = InMemoryStore()
    response = run_trip_chat(
        ChatRequest(tripId="t1", message="make it 4 people", brief=DEMO_BRIEF),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=mem, max_rounds=1),
        extractor=None,
        reply_generator=lambda prompt: "ok",
    )
    assert response.plan is not None
    assert response.plan.brief.groupSize == 4
    assert response.draft.groupSize == 4
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
        def extract(self, message: str, current: BriefPatch) -> BriefPatch:
            return BriefPatch(destination="Porto")

    response = run_trip_chat(
        ChatRequest(tripId="t3", message="anything", brief=DEMO_BRIEF),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        extractor=FixedExtractor(),
        reply_generator=lambda prompt: "ok",
    )
    assert response.plan is not None
    assert response.plan.brief.destination == "Porto"


def test_an_empty_request_asks_instead_of_planning():
    """There is no demo trip behind an empty request.

    It used to fall back to `demo_brief()`, which meant a caller who said
    nothing got a plan for Tokyo & Kyoto. The first screen has to be able to
    mean "nothing here yet".
    """
    response = run_trip_chat(
        ChatRequest(tripId="t4", message="hello"),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        question_generator=lambda prompt: "ok",
    )

    assert response.plan is None
    assert response.draft == BriefPatch()
    assert response.reply == "ok"


def test_the_offline_question_asks_for_the_first_missing_field():
    assert fallback_question_for(["destination", "dates", "groupSize", "budgetTotal"]) == (
        "Where would you like to go? I'll also need your travel dates, how many people are "
        "travelling and your total budget in USD."
    )
    assert fallback_question_for(["budgetTotal"]) == "What is your total budget, in USD?"


def test_a_question_failure_still_asks_something():
    """A model that cannot phrase the question must not turn into a silent turn."""

    def failing_generator(prompt: str) -> str:
        raise RuntimeError("model down")

    response = run_trip_chat(
        ChatRequest(tripId="t8", message="hello"),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        question_generator=failing_generator,
    )

    assert response.plan is None
    assert response.reply == fallback_question_for(missing_fields(BriefPatch()))


def test_an_opening_message_that_names_nothing_else_is_the_destination():
    """ "Tokyo" matches none of the extraction patterns, and answering it with
    "where would you like to go?" is the one reply that is certainly wrong."""
    response = run_trip_chat(
        ChatRequest(tripId="t5", message="Tokyo"),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        question_generator=lambda prompt: "ok",
    )

    assert response.draft.destination == "Tokyo"
    assert response.plan is None
    # The other three fields are still open, so the next turn can ask for them.
    assert missing_fields(response.draft) == ["dates", "groupSize", "budgetTotal"]


@pytest.mark.parametrize("message", ["hello there", "hi", "somewhere warm", "help me please"])
def test_a_conversational_opening_is_not_mistaken_for_a_destination(message):
    response = run_trip_chat(
        ChatRequest(tripId="t6", message=message),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        question_generator=lambda prompt: "ok",
    )

    assert response.draft.destination is None


def test_a_mid_conversation_word_is_never_read_as_a_destination():
    """ "cheaper" is a short bare phrase too. Only an empty draft may be read as
    a place name, which is what keeps it a rule about openings."""
    draft = BriefPatch(destination="Tokyo", dates=("2026-10-01", "2026-10-05"))
    response = run_trip_chat(
        ChatRequest(tripId="t7", message="cheaper", draft=draft),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        question_generator=lambda prompt: "ok",
    )

    assert response.draft.destination == "Tokyo"


def test_a_conversation_that_completes_the_trip_plans_it():
    """The whole point of the draft: two natural turns, one plan, no form."""
    start = datetime.now(UTC).date() + timedelta(days=30)
    end = start + timedelta(days=4)

    first = run_trip_chat(
        ChatRequest(tripId="t9", message="Tokyo"),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        question_generator=lambda prompt: "ok",
    )
    assert first.plan is None

    second = run_trip_chat(
        ChatRequest(
            tripId="t9",
            message=f"{start} to {end}, 2 people, budget $3000",
            draft=first.draft,
        ),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        reply_generator=lambda prompt: "ok",
    )

    assert second.plan is not None
    assert second.plan.brief.destination == "Tokyo"
    assert second.plan.brief.dates == (start.isoformat(), end.isoformat())
    assert second.plan.brief.groupSize == 2
    assert second.plan.brief.budgetTotal == 3000
    # The draft comes back complete, so a form or a stateless caller can render it.
    assert missing_fields(second.draft) == []


def test_a_turn_carries_the_callers_identity_onto_the_brief():
    """Long-term preferences are stored per user, so the id has to reach the
    brief even when it arrives as a draft rather than as a complete one."""
    response = run_trip_chat(
        ChatRequest(
            tripId="t10",
            message="make it 4 people",
            brief=DEMO_BRIEF.model_copy(update={"userId": "user-abc"}),
        ),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        reply_generator=lambda prompt: "ok",
    )
    assert response.plan is not None
    assert response.plan.brief.userId == "user-abc"

    from_draft = run_trip_chat(
        ChatRequest(tripId="t11", message="Tokyo", userId="user-xyz", draft=complete_draft()),
        OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        reply_generator=lambda prompt: "ok",
    )
    assert from_draft.plan is not None
    assert from_draft.plan.brief.userId == "user-xyz"


def test_an_impossible_draft_is_refused_before_anything_runs():
    with pytest.raises(ValueError, match="after the start date"):
        run_trip_chat(
            ChatRequest(
                tripId="t12",
                message="change the dates",
                draft=merge_draft(complete_draft(), BriefPatch(dates=("2026-06-22", "2026-06-15"))),
            ),
            OrchestratorOptions(specialists=ALL_SPECIALISTS, mem=InMemoryStore(), max_rounds=1),
        )
