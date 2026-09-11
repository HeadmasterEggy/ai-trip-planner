"""The palette lives in two files, so something has to hold them together.

`.streamlit/config.toml` is the only place Streamlit's own widgets read colours
from, and `ui/theme.py` owns everything we draw ourselves. The two cannot import
each other, so the shared values are written down twice -- and a palette written
down twice drifts silently, which is exactly the kind of bug nobody notices until
a page is half dark.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from trip_planner.ui.theme import CSS, PALETTE

CONFIG = Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml"

# What the two files have to agree on, as theme.py token -> config.toml option.
SHARED = {
    "bg": "backgroundColor",
    "surface": "secondaryBackgroundColor",
    "border": "borderColor",
    "text": "textColor",
    "accent": "primaryColor",
}


def theme_config() -> dict:
    with CONFIG.open("rb") as handle:
        return tomllib.load(handle)["theme"]


def test_the_app_pins_one_theme():
    """One base, chosen by the app rather than by the browser.

    Streamlit resolves a theme from the app's config *and* the user's own
    preference; pinning here is what makes the app's config win, and it is also
    why Streamlit's own System / Light / Dark picker does not appear. Changing
    this line changes both of those things.
    """
    assert theme_config()["base"] == "light"


def test_the_shared_colours_are_the_same_in_both_files():
    config = theme_config()
    for token, option in SHARED.items():
        assert PALETTE[token].lower() == config[option].lower(), (
            f"{token} is {PALETTE[token]} in theme.py but {config[option]} in config.toml"
        )


def test_every_token_reaches_the_stylesheet():
    for token in PALETTE:
        assert f"--tp-{token}:" in CSS


def test_no_colour_is_written_as_a_literal_in_the_stylesheet():
    """A colour written straight into a rule is one the next re-theme misses.

    This is how the light theme survived in eight places: the tokens changed and
    the literals beside them did not. Every colour has to come from `PALETTE`.
    """
    literals = {literal.lower() for literal in re.findall(r"#[0-9a-fA-F]{3,8}", CSS)}
    known = {value.lower() for value in PALETTE.values()}

    assert literals <= known, f"colours outside PALETTE: {sorted(literals - known)}"
