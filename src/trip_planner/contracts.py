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
    """The structured request the orchestrator hands to every specialist.

    Strict on purpose: every specialist depends on all of it. A conversation
    arrives one field at a time and accumulates a `BriefPatch` instead, which
    becomes this only once nothing is missing.
    """

    tripId: str
    userId: str = "demo-user"
    destination: str
    dates: tuple[str, str]  # [start, end] ISO date
    groupSize: Annotated[int, Field(gt=0)]
    budgetTotal: Annotated[float, Field(gt=0)]
    nationality: str | None = None


# Every field a patch can carry, and the subset a plan cannot be built without.
# `nationality` is deliberately absent from the second: it changes a visa note,
# not whether the trip can be planned, so a traveller who never mentions a
# passport still gets a complete plan instead of a question.
BRIEF_FIELDS = ("destination", "dates", "groupSize", "budgetTotal", "nationality")
REQUIRED_BRIEF_FIELDS = ("destination", "dates", "groupSize", "budgetTotal")


class BriefPatch(BaseModel):
    """A partial trip: only the fields that have actually been stated.

    The conversational layer collects one field at a time, so it cannot hold a
    `TripBrief` while it is still asking questions. This is what it holds, and
    being all-optional is the point -- a patch never claims to be a brief.
    """

    destination: str | None = None
    dates: tuple[str, str] | None = None
    groupSize: int | None = Field(default=None, gt=0)
    budgetTotal: float | None = Field(default=None, gt=0)
    nationality: str | None = None

    @classmethod
    def from_brief(cls, brief: TripBrief) -> BriefPatch:
        """The patch a complete brief already satisfies."""
        return cls(**{field: getattr(brief, field) for field in BRIEF_FIELDS})

    def is_empty(self) -> bool:
        """True when nothing has been stated at all."""
        return not any(getattr(self, field) for field in BRIEF_FIELDS)


def merge_draft(draft: BriefPatch | None, patch: BriefPatch) -> BriefPatch:
    """`draft` with the fields `patch` actually carries applied over it.

    Nothing is validated here: a draft is allowed to be incomplete, and being
    asked for the next field is not an error. `draft_problem` and
    `missing_fields` decide what happens to a draft next.
    """
    base = draft or BriefPatch()
    return BriefPatch(**{**base.model_dump(), **patch.model_dump(exclude_none=True)})


def missing_fields(draft: BriefPatch | None) -> list[str]:
    """Which required fields the draft still lacks, in the order worth asking.

    The order is the order a person would ask in -- where, then when, then who,
    then how much -- and it is also the order the offline question reads.
    """
    return [field for field in REQUIRED_BRIEF_FIELDS if draft is None or not getattr(draft, field)]


def dates_problem(dates: tuple[str, str]) -> str | None:
    """Why this date span cannot be planned, or None. Answerable on its own."""
    start, end = _iso_date(dates[0]), _iso_date(dates[1])
    if start is None or end is None:
        return "Trip dates must be real dates in YYYY-MM-DD format."
    if end <= start:
        return "Trip end date must be after the start date."
    return None


def cities_nights_problem(destination: str, dates: tuple[str, str]) -> str | None:
    """Why these destinations cannot be covered in these nights, or None.

    Assumes the dates themselves have already been checked: with an unparseable
    or reversed span there is no night count to compare against.
    """
    start, end = _iso_date(dates[0]), _iso_date(dates[1])
    if start is None or end is None:
        return None
    try:
        names = cities(destination)
    except ValueError as error:
        return str(error)
    nights = (end - start).days
    if nights < len(names):
        return (
            f"{len(names)} destinations need at least {len(names)} nights; "
            f"this trip is {nights} night(s) long."
        )
    return None


def brief_problem(brief: TripBrief) -> str | None:
    """Why this brief cannot be planned, or None when it can.

    One place decides feasibility, and everyone asks it: the form, chat intake
    and the orchestrator. The alternative is what happened before -- a brief
    with more cities than nights failed inside the accommodation specialist,
    after the other four had already run, and the traveller got an internal
    error instead of a reason.
    """
    return dates_problem(brief.dates) or cities_nights_problem(brief.destination, brief.dates)


def draft_problem(draft: BriefPatch) -> str | None:
    """The part of `brief_problem` a still-partial draft can already answer.

    Checked before the draft is complete so that a bad span, or two cities in
    one night, is reported the moment it is said rather than after three more
    questions have been answered.
    """
    if draft.dates is None:
        return None
    problem = dates_problem(draft.dates)
    if problem:
        return problem
    if not draft.destination:
        return None
    return cities_nights_problem(draft.destination, draft.dates)


def brief_from_draft(draft: BriefPatch, *, trip_id: str, user_id: str) -> TripBrief:
    """The validated brief a complete draft describes.

    Raises `ValueError` when a required field is still missing: a caller that
    reaches the orchestrator with a partial draft skipped `missing_fields`, and
    that is a bug rather than a conversation state.
    """
    missing = missing_fields(draft)
    if missing:
        raise ValueError(f"The trip is still missing {', '.join(missing)}.")
    return TripBrief(**{**draft.model_dump(), "tripId": trip_id, "userId": user_id})


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
# because the final frame of a stream is still one ChatResponse. A turn that had
# nothing to plan is that same frame with `plan=None`, so a caller never has to
# guess which of two response types it is holding.
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    tripId: str
    message: Annotated[str, Field(min_length=1)]
    # A complete, already-validated brief: the form path, or any caller that
    # holds one. Preferred over `draft` when both are sent.
    brief: TripBrief | None = None
    # What a conversation has collected so far. The UI sends it back on every
    # turn, so a stateless request still accumulates and the next missing field
    # can be asked for. An empty request is legitimate -- it is the first screen
    # -- and is never quietly replaced by a demo trip.
    draft: BriefPatch | None = None
    # Who is talking. Read only when no complete `brief` already carries an
    # identity, since long-term preferences are stored per user.
    userId: str = "demo-user"


class ChatResponse(BaseModel):
    reply: str  # assistant text for the chat stream
    # None while the draft is still missing something a plan needs. The reply
    # asks for it, and there is nothing to render yet.
    plan: TripPlan | None = None
    # The trip after this turn, complete or not, so a caller can seed a form
    # from what the conversation collected and send it back with the next
    # message.
    draft: BriefPatch


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
