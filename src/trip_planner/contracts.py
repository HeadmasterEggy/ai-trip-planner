"""Shared contracts.

A direct port of `packages/shared/src/contracts.ts` and `plan.ts` from the
TypeScript implementation. These shapes are the boundary every specialist,
the orchestrator and the UI agree on, so they are validated rather than
trusted: a model that returns something off-schema is rejected and the caller
falls back to deterministic output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# The five specialists. `name` is the stable id used as the section id in the
# trip plan; keep this list and `specialists.ALL_SPECIALISTS` in sync.
AGENT_NAMES = (
    "itinerary",
    "transport",
    "accommodation",
    "destination-guide",
    "dining",
)
AgentName = Literal["itinerary", "transport", "accommodation", "destination-guide", "dining"]

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def cities(destination: str) -> list[str]:
    """Parse the ampersand-separated destination convention."""
    result = [c.strip() for c in destination.split("&") if c.strip()]
    if not result:
        raise ValueError("At least one destination is required.")
    return result


def _iso_date(value: str) -> date | None:
    """A real, zero-padded ISO date, or None.

    `date.fromisoformat` also accepts the basic `YYYYMMDD` form, which would
    make a malformed brief look valid here and fail somewhere less obvious.
    """
    if not ISO_DATE.match(value):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


class TripBrief(BaseModel):
    """The structured request the orchestrator hands to every specialist."""

    tripId: str
    userId: str = "demo-user"
    destination: str
    dates: tuple[str, str]  # [start, end] ISO date
    groupSize: Annotated[int, Field(gt=0)]
    budgetTotal: Annotated[float, Field(gt=0)]
    nationality: str | None = None


def brief_problem(brief: TripBrief) -> str | None:
    """Why this brief cannot be planned, or None when it can.

    One place decides feasibility, and everyone asks it: the form, chat intake
    and the orchestrator. The alternative is what happened before -- a brief
    with more cities than nights failed inside the accommodation specialist,
    after the other four had already run, and the traveller got an internal
    error instead of a reason.
    """
    start, end = _iso_date(brief.dates[0]), _iso_date(brief.dates[1])
    if start is None or end is None:
        return "Trip dates must be real dates in YYYY-MM-DD format."
    if end <= start:
        return "Trip end date must be after the start date."
    nights = (end - start).days
    try:
        names = cities(brief.destination)
    except ValueError as error:
        return str(error)
    if nights < len(names):
        return (
            f"{len(names)} destinations need at least {len(names)} nights; "
            f"this trip is {nights} night(s) long."
        )
    return None


class ProposalItem(BaseModel):
    """One line of a specialist's proposal.

    `estCost` is USD for the whole trip, not per person. The optional schedule
    metadata is what lets the orchestrator detect cross-agent time conflicts
    without parsing human-readable `detail` strings.
    """

    kind: str  # "transport" | "hotel" | "activity" | "meal" | "note" ...
    detail: str
    estCost: Annotated[float, Field(ge=0)] | None = None
    day: int | None = None
    startTime: str | None = None
    endTime: str | None = None
    location: str | None = None

    @model_validator(mode="after")
    def _check_schedule(self) -> ProposalItem:
        for field in ("startTime", "endTime"):
            value = getattr(self, field)
            if value is not None and not HHMM.match(value):
                raise ValueError(f"{field} must be HH:MM (24h)")
        if bool(self.startTime) != bool(self.endTime):
            raise ValueError("startTime and endTime must be provided together")
        if self.startTime and self.endTime and self.startTime >= self.endTime:
            raise ValueError("endTime must be after startTime on the same day")
        if self.startTime and self.day is None:
            raise ValueError("day must be provided when startTime/endTime are set")
        return self


class AgentProposal(BaseModel):
    """What every specialist returns for one round."""

    agent: AgentName
    summary: str
    items: list[ProposalItem]
    assumptions: list[str]
    conflictsWith: list[str] = Field(default_factory=list)


class RevisionRequest(BaseModel):
    """Orchestrator -> a single specialist, rounds 2..K."""

    tripId: str
    targetAgent: AgentName
    reason: str  # "over budget by 18%", "day 2 route infeasible" ...
    constraints: list[str]


SectionStatus = Literal["planning", "draft", "needs_you", "confirmed"]


class TripSection(BaseModel):
    """One row of the right-hand "Your trip" panel."""

    id: str  # = specialist name
    label: str  # human label, e.g. "Getting around"
    summary: str
    status: SectionStatus
    estCost: Annotated[float, Field(ge=0)]
    proposal: AgentProposal | None = None


class ChoiceOption(BaseModel):
    """One candidate a traveller can pick between at a checkpoint.

    Specialists already compare candidates internally; this exposes the ones
    that were considered so the decision can be handed back rather than made
    silently on the traveller's behalf.
    """

    id: str
    label: str
    detail: str
    estCost: Annotated[float, Field(ge=0)] | None = None
    meta: dict[str, str] = Field(default_factory=dict)
    recommended: bool = False


# The keys a specialist reports through `AgentContext.extras`, and the matching state
# channels. Constants because a typo in a string literal would silently drop a report
# rather than fail: 16 call sites shared these two words.
TRACES_KEY = "traces"
STAY_CHOICES_KEY = "stay_choices"


def merge_choices(
    left: dict[str, list[ChoiceOption]] | None,
    right: dict[str, list[ChoiceOption]] | None,
) -> dict[str, list[ChoiceOption]]:
    """Reducer for the per-city candidate lists: the later writer wins per city.

    Two graphs carry `stay_choices` as a state channel (the supervisor's nested
    agent and the outer workflow), each with this reducer, so a specialist that
    runs in a later round or on another thread cannot drop an earlier city.
    """
    return {**(left or {}), **(right or {})}


class HitlCheckpoint(BaseModel):
    """A point where the flow pauses for the human, or escalates to them."""

    id: str
    type: Literal["confirm_brief", "confirm_plan", "confirm_choice", "escalation"]
    title: str
    detail: str
    status: Literal["pending", "approved", "rejected"]
    # Present on confirm_choice. Empty elsewhere, so a caller can treat any
    # checkpoint uniformly and simply find nothing to offer.
    options: list[ChoiceOption] = Field(default_factory=list)
    selected: str | None = None
    # The long-term preference key a decision writes to, which is how a choice
    # survives into the next round instead of being re-decided.
    preferenceKey: str | None = None


class SpecialistTrace(BaseModel):
    """What one specialist read, which path it took, and why.

    A plan shows what was decided; this shows how. Without it a section that
    fell back is indistinguishable from one the model wrote, and a reader has
    no way to tell whether a place came from the maps port or was invented.
    """

    agent: AgentName
    round: int
    source: Literal["model", "deterministic fallback", "calculator"]
    # Facts the specialist was given, as short label -> value.
    evidence: dict[str, str] = Field(default_factory=dict)
    # The constraint it was re-planning against, if this was a revision.
    revision: str | None = None
    # Why the model path was abandoned. Present only on a fallback.
    fallbackReason: str | None = None
    notes: list[str] = Field(default_factory=list)
    # How long this specialist's invocation took, stamped by whoever ran it. The
    # per-section cost of a plan is otherwise invisible, and with roles routable
    # independently (MODEL_ROUTING) it is the first question worth answering.
    seconds: float | None = None


class NegotiationRound(BaseModel):
    """What one round of the negotiation found and who was asked to fix it.

    The orchestrator discards this once it has a plan, but it is the only
    record of *why* the plan looks the way it does: which specialists were sent
    back, and under exactly what constraint.
    """

    round: int
    conflicts: list[RevisionRequest]
    revised: list[str] = Field(default_factory=list)  # specialist names re-run after this round
    # Set when a revision changed nothing for every specialist it targeted.
    # Asking them again cannot help, so the negotiation stops here.
    stalled: bool = False


class TripPlan(BaseModel):
    """The aggregated artifact the UI renders."""

    tripId: str
    brief: TripBrief
    round: int
    budgetTotal: float
    estTotal: float
    overrunPct: float  # (estTotal - budgetTotal) / budgetTotal * 100, can be negative
    sections: list[TripSection]
    hitl: list[HitlCheckpoint]
    # Additive with a default, so an older caller keeps working.
    negotiation: list[NegotiationRound] = Field(default_factory=list)
    traces: list[SpecialistTrace] = Field(default_factory=list)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class UserPreference(BaseModel):
    key: str
    value: str
    source: Literal["filter", "chat_confirmed"]


# ---------------------------------------------------------------------------
# The chat contract. Frozen shape: streaming can be added without changing it,
# because the final frame of a stream is still one ChatResponse.
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    tripId: str
    message: Annotated[str, Field(min_length=1)]
    # Optional so a caller can rely on the demo brief. The UI sends the latest
    # brief so a stateless request can still apply an incremental edit.
    brief: TripBrief | None = None


class ChatResponse(BaseModel):
    reply: str  # assistant text for the chat stream
    plan: TripPlan  # the fresh aggregated plan for the trip panel


@dataclass
class ProgressEvent:
    """One specialist starting, finishing or failing.

    It travels on the graph's custom stream rather than through a callback: a
    node writes with `langgraph.config.get_stream_writer`, a delegation tool with
    `ToolRuntime.stream_writer`, and the consumer reads it on its own thread. That
    is what keeps Streamlit out of the specialist layer -- see item 1.4 of
    `docs/framework-alignment.md`.
    """

    type: Literal["agent_started", "agent_completed", "agent_failed"]
    agent: str
    round: int
    error: str | None = None
