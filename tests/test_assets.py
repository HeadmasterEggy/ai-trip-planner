"""The logo the app serves, and what it costs.

`st.logo` inlines the image bytes into *every rerun* — measured at about 1.5MB
per rerun for the file as supplied, against 1.8KB for the render the app actually
uses — so the size of this asset is a behaviour, not a detail. The source is kept
beside the render so a future swap has something to regenerate from; see
`assets/README.md`.
"""

from __future__ import annotations

import struct
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SOURCE = ASSETS / "ai-trip-planner-logo.svg"
SERVED = ASSETS / "ai-trip-planner-logo.png"
# Comfortably above the 32px it is drawn at, far below the 837KB source.
MAX_BYTES = 64 * 1024


def test_the_logo_the_app_serves_is_present_and_small():
    assert SERVED.is_file(), "the app's logo is missing"
    size = SERVED.stat().st_size
    assert size < MAX_BYTES, (
        f"{SERVED.name} is {size} bytes and is re-sent on every rerun; "
        "regenerate it smaller (assets/README.md)"
    )


def test_the_served_logo_is_sharp_enough_and_no_larger():
    """Drawn at 32px, so 64px covers a 2x screen and 256px is already generous."""
    data = SERVED.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "the served logo should be a PNG"
    width, height = struct.unpack(">II", data[16:24])
    assert 64 <= width <= 256, f"width {width}px"
    assert 64 <= height <= 256, f"height {height}px"


def test_the_source_logo_is_kept_beside_the_render():
    assert SOURCE.is_file(), "keep the supplied logo: it is what the render comes from"
