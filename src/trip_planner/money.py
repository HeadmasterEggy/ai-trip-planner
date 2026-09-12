"""What a budget is in, and what that is worth in the unit the plan uses.

Every cost rule, every fixture and every comparison in `budget.py` is USD, so a
budget stated in another currency has to become USD before it can be planned
against — treating "¥3,000" as "$3,000" would put the plan out by a factor of
seven and say so with a straight face.

`RATES` is a static snapshot, not a live feed: there is no rate provider in this
project, and a wrong-but-labelled conversion beats either refusing the currency or
inventing a rate. The plan therefore shows the traveller's own figure next to the
USD one it was planned against, and the conversion is described as approximate.
"""

from __future__ import annotations

import re

# 1 unit of the currency in USD. Snapshot, not a feed: see the module docstring.
AS_OF = "2026-09"
RATES = {
    "USD": 1.0,
    "AUD": 0.66,
    "CNY": 0.139,
    "JPY": 0.0064,
    "EUR": 1.08,
    "GBP": 1.27,
    "SGD": 0.74,
    "HKD": 0.128,
    "NZD": 0.60,
    "CAD": 0.73,
    "KRW": 0.00072,
    "INR": 0.012,
    "THB": 0.031,
    "MYR": 0.24,
    "IDR": 0.000062,
    "VND": 0.000040,
    "TWD": 0.031,
    "AED": 0.27,
    "CHF": 1.13,
    "ZAR": 0.055,
}

# An amount, as anything from "3" to "30,000.50".
_NUMBER = r"([\d][\d,]*(?:\.\d+)?)"

SYMBOLS = {
    "USD": "$",
    "AUD": "A$",
    "CNY": "¥",
    "JPY": "¥",
    "EUR": "€",
    "GBP": "£",
    "SGD": "S$",
    "HKD": "HK$",
    "NZD": "NZ$",
    "CAD": "C$",
    "KRW": "₩",
    "INR": "₹",
    "THB": "฿",
    "MYR": "RM",
    "TWD": "NT$",
    "AED": "AED ",
    "CHF": "CHF ",
    "ZAR": "R",
}

# What a traveller actually types, lowercased. `¥` is read as CNY: it is the
# symbol for both yuan and yen, and this app's conversations are mostly Chinese.
TOKENS = {
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "美元": "USD",
    "美金": "USD",
    "a$": "AUD",
    "au$": "AUD",
    "aud": "AUD",
    "澳元": "AUD",
    "澳币": "AUD",
    "澳刀": "AUD",
    "¥": "CNY",
    "￥": "CNY",
    "rmb": "CNY",
    "cny": "CNY",
    "元": "CNY",
    "块": "CNY",
    "人民币": "CNY",
    "jpy": "JPY",
    "円": "JPY",
    "日元": "JPY",
    "日币": "JPY",
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "欧元": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "英镑": "GBP",
    "s$": "SGD",
    "sgd": "SGD",
    "新元": "SGD",
    "新加坡元": "SGD",
    "hk$": "HKD",
    "hkd": "HKD",
    "港币": "HKD",
    "港元": "HKD",
    "nz$": "NZD",
    "nzd": "NZD",
    "c$": "CAD",
    "cad": "CAD",
    "krw": "KRW",
    "₩": "KRW",
    "韩元": "KRW",
    "inr": "INR",
    "₹": "INR",
    "thb": "THB",
    "฿": "THB",
    "泰铢": "THB",
    "myr": "MYR",
    "rm": "MYR",
    "twd": "TWD",
    "nt$": "TWD",
    "新台币": "TWD",
    "aed": "AED",
    "迪拉姆": "AED",
    "chf": "CHF",
    "zar": "ZAR",
}

# Longest first, so "us$" is read before "$" and "人民币" before "元".
TOKEN_PATTERN = "|".join(
    re.escape(token) for token in sorted(TOKENS, key=len, reverse=True) if token.strip()
)


# Everything that names a currency other than USD. A reply that uses one is
# claiming a conversion, and a model asked to repeat "¥3,000" will happily invent
# the rate -- measured: it produced "USD 192" from the yen rate, while relabelling
# CNY as JPY. The plan shows the real conversion; the prose does not get to guess.
_TOKENS = sorted(TOKENS.items(), key=lambda item: len(item[0]), reverse=True)
_OTHER = re.compile(
    "|".join(re.escape(token) for token, code in _TOKENS if code != "USD" and token.strip()),
    re.IGNORECASE,
)
# ... but the USD names go first. "美元" contains the CNY "元", and Chinese
# currency words are not delimited, so a substring search alone reads one as the
# other. Removing the USD names leaves only the question that was being asked.
_USD_NAMES = re.compile(
    "|".join(re.escape(token) for token, code in _TOKENS if code == "USD" and token.strip()),
    re.IGNORECASE,
)


def mentions_other_currency(text: str) -> bool:
    """Whether `text` names a currency that is not USD."""
    return _OTHER.search(_USD_NAMES.sub(" ", text)) is not None


# A token with a number on one side of it, which is how a currency claim is
# written: "¥3,000", "3,000 元", "CNY 3000".
_CLAIM = re.compile(
    "(?:(?P<token_before>"
    + _OTHER.pattern
    + r")\s*"
    + _NUMBER
    + "|"
    + _NUMBER
    + r"\s*(?P<token_after>"
    + _OTHER.pattern
    + "))",
    re.IGNORECASE,
)


def foreign_claims(text: str) -> list[tuple[float, str]]:
    """Every amount `text` states in a currency that is not USD.

    The reply is allowed to quote the figure the traveller gave -- that is the
    acknowledgement the whole feature exists for -- and nothing else. A model
    cannot be trusted with the arithmetic, so any *other* foreign amount means it
    has started converting, and the reply is thrown away.
    """
    claims: list[tuple[float, str]] = []
    for match in _CLAIM.finditer(text):
        token = match.group("token_before") or match.group("token_after")
        code = code_for(token)
        raw = next((g for g in match.groups() if g and g[0].isdigit()), "")
        if not code or code == "USD" or not raw:
            continue
        try:
            claims.append((float(raw.replace(",", "")), code))
        except ValueError:
            continue
    return claims


def code_for(token: str | None) -> str | None:
    """The ISO code a symbol, word or code names, or None."""
    if not token:
        return None
    return TOKENS.get(token.strip().casefold()) or TOKENS.get(token.strip()) or None


def rate(code: str) -> float:
    return RATES.get((code or "USD").upper(), 1.0)


def to_usd(amount: float, code: str | None) -> float:
    """`amount` in `code`, in USD, rounded to cents."""
    return round(amount * rate(code), 2)


def given(amount: float, code: str | None) -> str:
    """The traveller's own figure, as they would write it: "¥3,000"."""
    upper = (code or "USD").upper()
    symbol = SYMBOLS.get(upper, f"{upper} ")
    whole = f"{amount:,.0f}" if float(amount).is_integer() else f"{amount:,.2f}"
    return f"{symbol}{whole}"
