"""The ports the orchestrator injects, and the factory that picks their adapters.

Booking is always the deterministic fixture: there is no live provider for it, and the
roadmap says one has to exist before that changes. Maps switch to OpenStreetMap with
`USE_MOCK_TOOLS=false`; the fixtures stay the default so the app runs with no keys and
no network.
"""

from __future__ import annotations

from ..ports import ToolGateway
from .booking import MockBooking
from .maps import MapsAdapter


def create_tool_gateway() -> ToolGateway:
    return ToolGateway(maps=MapsAdapter(), booking=MockBooking())


__all__ = ["create_tool_gateway"]
