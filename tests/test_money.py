"""Budgets in currencies that are not the one the plan costs in."""

from __future__ import annotations

import pytest

from trip_planner import money


@pytest.mark.parametrize(
    ("written", "code"),
    [
        ("$", "USD"),
        ("USD", "USD"),
        ("美元", "USD"),
        ("¥", "CNY"),
        ("元", "CNY"),
        ("RMB", "CNY"),
        ("人民币", "CNY"),
        ("€", "EUR"),
        ("欧元", "EUR"),
        ("£", "GBP"),
        ("A$", "AUD"),
        ("澳元", "AUD"),
        ("円", "JPY"),
        ("JPY", "JPY"),
        # Case and padding come from however the traveller typed it.
        ("  eur ", "EUR"),
        ("Aud", "AUD"),
    ],
)
def test_the_tokens_people_write(written, code):
    assert money.code_for(written) == code


def test_something_that_is_not_a_currency_is_not_one():
    for written in (None, "", "   ", "bananas", "3000"):
        assert money.code_for(written) is None


def test_a_budget_becomes_usd_before_any_rule_sees_it():
    # Treating ¥3,000 as $3,000 would put the plan out by a factor of seven.
    assert money.to_usd(3000, "CNY") == pytest.approx(417.0)
    assert money.to_usd(2000, "EUR") == pytest.approx(2160.0)
    assert money.to_usd(2500, None) == 2500.0


def test_an_unknown_code_is_left_alone_rather_than_half_converted():
    assert money.rate("XYZ") == 1.0
    assert money.to_usd(100, "XYZ") == 100.0


def test_the_travellers_own_figure_survives_round_tripping():
    assert money.given(3000, "CNY") == "¥3,000"
    assert money.given(2000, "EUR") == "€2,000"
    assert money.given(2500.5, "USD") == "$2,500.50"
    # A code with no symbol is written out rather than guessed at.
    assert money.given(100, "XYZ") == "XYZ 100"


def test_every_rate_is_a_plausible_multiplier():
    for code, rate in money.RATES.items():
        assert code == code.upper()
        assert 0 < rate <= 2, f"{code} -> {rate}"
    assert money.RATES["USD"] == 1.0


@pytest.mark.parametrize(
    ("text", "foreign"),
    [
        ("that's about ¥30,000", True),
        ("the €2,000 you mentioned", True),
        ("预算 5000 元", True),
        ("roughly 3000 CNY", True),
        ("USD 4,170 works", False),
        ("a $4,170 budget", False),
        ("the 美元 figure", False),
        ("no money here at all", False),
    ],
)
def test_a_reply_that_names_another_currency_is_caught(text, foreign):
    """The guard behind the reply: a model asked to repeat "¥3,000" invents the
    rate, so a reply that starts quoting another currency is not shipped."""
    assert money.mentions_other_currency(text) is foreign
