"""The theme, and the arrangements that keep it switchable.

The app can be flipped between light and dark at runtime, which puts two
constraints on the palette that are easy to break silently:

- Streamlit applies a *pinned* neutral to both bases, so pinning one in
  `config.toml` freezes the theme and the toggle stops doing anything.
- The neutrals therefore have to be Streamlit's own. That is a dependency on its
  built-in palette, so the values are pinned here rather than trusted.

Neither is visible in a code review, which is why they are tests.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from trip_planner.ui.theme import DEFAULT_THEME, PALETTES, stylesheet

CONFIG = Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml"

# Streamlit's built-in neutrals, which `config.toml` deliberately does not pin.
# If an upgrade changes them, this is the alarm: our cards would otherwise be
# drawn for a palette the widgets around them no longer use.
STREAMLIT_DEFAULTS = {
    "dark": {"bg": "#0e1117", "surface": "#262730", "text": "#fafafa"},
    "light": {"bg": "#ffffff", "surface": "#f0f2f6", "text": "#31333f"},
}

# The keys that are applied to both bases, and so must stay unset.
NEUTRAL_KEYS = ("backgroundColor", "secondaryBackgroundColor", "textColor")


def theme_config() -> dict:
    with CONFIG.open("rb") as handle:
        return tomllib.load(handle)["theme"]


def test_both_bases_have_a_palette_and_dark_is_the_default():
    assert set(PALETTES) == {"dark", "light"}
    assert DEFAULT_THEME == "dark"
    assert theme_config()["base"] == DEFAULT_THEME


def test_no_neutral_colour_is_pinned_in_the_config():
    """The one thing that would break the toggle.

    These are applied to both bases, so setting any of them means switching the
    base changes nothing -- which reads as a broken button, not a broken palette.
    """
    config = theme_config()
    assert [key for key in NEUTRAL_KEYS if key in config] == []


def test_an_accent_is_pinned_so_light_mode_is_not_streamlits_red():
    assert theme_config()["primaryColor"].startswith("#")


def test_the_neutrals_are_streamlits_own():
    for base, expected in STREAMLIT_DEFAULTS.items():
        for token, value in expected.items():
            assert PALETTES[base][token].lower() == value, f"{base}.{token}"


def test_every_palette_declares_the_same_tokens():
    """A token missing from one palette is a colour that goes unset in that mode,
    and an unset custom property fails to transparent rather than to something
    readable."""
    assert set(PALETTES["dark"]) == set(PALETTES["light"])


def test_every_token_reaches_every_stylesheet():
    for base, palette in PALETTES.items():
        css = stylesheet(base)
        for token in palette:
            assert f"--tp-{token}:" in css, f"{token} missing from the {base} stylesheet"


def test_a_stylesheet_is_generated_per_base():
    assert stylesheet("dark") != stylesheet("light")
    # An unknown base is readable rather than blank.
    assert stylesheet("sepia") == stylesheet(DEFAULT_THEME)


def test_no_colour_is_written_as_a_literal_in_the_stylesheet():
    """A colour written straight into a rule is one the next re-theme misses.

    That is how eight light-theme values survived the first pass of the dark
    rewrite: the tokens changed and the literals beside them did not.
    """
    for base, palette in PALETTES.items():
        literals = {
            literal.lower() for literal in re.findall(r"#[0-9a-fA-F]{3,8}", stylesheet(base))
        }
        known = {value.lower() for value in palette.values()}
        assert literals <= known, f"{base} colours outside the palette: {sorted(literals - known)}"
