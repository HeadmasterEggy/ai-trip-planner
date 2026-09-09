"""Shared contracts.

A direct port of `packages/shared/src/contracts.ts` and `plan.ts` from the
TypeScript implementation. These shapes are the boundary every specialist,
the orchestrator and the UI agree on, so they are validated rather than
trusted: a model that returns something off-schema is rejected and the caller
falls back to deterministic output.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
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


class TripBrief(BaseModel):
    """The structured request the orchestrator hands to every specialist."""

    tripId: str
    userId: str = "demo-user"
    destination: str
    dates: tuple[str, str]  # [start, end] ISO date
    groupSize: Annotated[int, Field(gt=0)]
    budgetTotal: Annotated[float, Field(gt=0)]
    nationality: str | None = None


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


class HitlCheckpoint(BaseModel):
    """A point where the flow pauses for the human, or escalates to them."""

    id: str
    type: Literal["confirm_brief", "confirm_plan", "escalation"]
    title: str
    detail: str
    status: Literal["pending", "approved", "rejected"]


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
