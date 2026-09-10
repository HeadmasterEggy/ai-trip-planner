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

from ..contracts import AgentProposal, RevisionRequest, SpecialistTrace, TripBrief
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


def stamp_duration(produced: dict[str, object], seconds: float) -> None:
    """Record how long an invocation took, on every trace it reported.

    The specialist knows what it did, the caller knows how long it took, and neither
    has to guess about the other -- so the caller stamps what it measured.
    """
    for trace in produced.get("traces", []) or []:
        trace.seconds = seconds  # type: ignore[attr-defined]


def record_trace(
    ctx: AgentContext,
    agent: str,
    source: str,
    *,
    evidence: dict[str, str] | None = None,
    revision: RevisionRequest | None = None,
    fallback_reason: str | None = None,
    notes: list[str] | None = None,
) -> None:
    """Report how this specialist arrived at its proposal.

    Written into the shared context rather than the proposal because it is
    diagnostic rather than part of the plan: a caller that ignores it still gets
    everything it needs, and the contract stays about the trip.
    """
    ctx.extras.setdefault("traces", []).append(
        SpecialistTrace(
            agent=agent,  # type: ignore[arg-type]
            round=ctx.round,
            source=source,  # type: ignore[arg-type]
            evidence=evidence or {},
            revision=(
                f"{revision.reason} → {'; '.join(revision.constraints)}" if revision else None
            ),
            fallbackReason=fallback_reason,
            notes=notes or [],
        )
    )
