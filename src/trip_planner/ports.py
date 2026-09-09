"""Ports the orchestrator injects into every specialist.

Specialists depend only on these protocols, so a unit test can pass a fake
instead of reaching the network. `tools/` and `memory.py` implement them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .contracts import ChatTurn, UserPreference


@dataclass(frozen=True)
class RouteLeg:
    mode: Literal["train", "flight", "bus", "walk", "transit"]
    durationMin: int
    priceUsd: float
    note: str | None = None


@dataclass(frozen=True)
class Place:
    name: str
    category: str
    rating: float | None = None


class MapsPort(Protocol):
    def route(self, *, frm: str, to: str, date: str | None = None) -> list[RouteLeg]: ...
    def places(self, *, near: str, category: str | None = None) -> list[Place]: ...


@dataclass(frozen=True)
class StayOption:
    name: str
    area: str
    pricePerNightUsd: float
    rating: float
    freeCancellation: bool


@dataclass(frozen=True)
class FlightOption:
    carrier: str
    priceUsd: float
    note: str | None = None


class BookingPort(Protocol):
    def search_stays(
        self, *, city: str, check_in: str, check_out: str, guests: int
    ) -> list[StayOption]: ...
    def search_flights(
        self, *, frm: str, to: str, depart: str, ret: str | None, passengers: int
    ) -> list[FlightOption]: ...


class MemoryStore(Protocol):
    def get_short_term(self, trip_id: str) -> list[ChatTurn]: ...
    def append_short_term(self, trip_id: str, turn: ChatTurn) -> None: ...
    def get_long_term(self, user_id: str) -> list[UserPreference]: ...
    def set_long_term(self, user_id: str, pref: UserPreference) -> None: ...
    def promote(self, trip_id: str, user_id: str, key: str) -> None: ...


@dataclass
class ToolGateway:
    maps: Any
    booking: Any


@dataclass
class AgentContext:
    """Everything a specialist is allowed to reach outside its own prompt."""

    tripId: str
    round: int
    tools: ToolGateway
    mem: Any
    extras: dict[str, Any] = field(default_factory=dict)
