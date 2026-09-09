"""The framework-neutral specialist contract.

One immutable entry point serves both the initial plan and every revision, so
the orchestrator never has to know whether a specialist reasons with a model
or computes deterministically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from ..contracts import AgentProposal, RevisionRequest, TripBrief
from ..ports import AgentContext


class Specialist(Protocol):
    name: str
    label: str

    def invoke(
        self, brief: TripBrief, ctx: AgentContext, revision: RevisionRequest | None = None
    ) -> AgentProposal: ...


@dataclass
class FunctionSpecialist:
    """Adapts a plain function into a Specialist."""

    name: str
    label: str
    fn: Callable[[TripBrief, AgentContext, RevisionRequest | None], AgentProposal]

    def invoke(
        self, brief: TripBrief, ctx: AgentContext, revision: RevisionRequest | None = None
    ) -> AgentProposal:
        return self.fn(brief, ctx, revision)


def trip_days(dates: tuple[str, str]) -> int:
    start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[1])
    days = (end - start).days
    if days < 1:
        raise ValueError("A trip needs ordered dates at least one day apart.")
    return days


def cities(destination: str) -> list[str]:
    """Parse the demo's ampersand-separated destination convention."""
    result = [c.strip() for c in destination.split("&") if c.strip()]
    if not result:
        raise ValueError("At least one destination is required.")
    return result


def clock(total_minutes: int) -> str:
    if total_minutes < 0 or total_minutes >= 24 * 60:
        raise ValueError("A leg cannot fit inside one planning day.")
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def is_budget_revision(revision: RevisionRequest | None) -> bool:
    if revision is None:
        return False
    text = " ".join([revision.reason, *revision.constraints]).lower()
    return any(word in text for word in ("budget", "cost", "cheaper", "overrun"))


def is_schedule_revision(revision: RevisionRequest | None) -> bool:
    if revision is None:
        return False
    text = revision.reason.lower()
    return any(word in text for word in ("time", "overlap", "schedule"))
