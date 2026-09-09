"""LangGraph orchestration.

    START -> dispatch_specialists -> detect_conflicts
                                          | (conditional)
                             revise_conflicts <-> detect_conflicts
                                          |
                                     build_plan -> END

Ported from `packages/orchestrator/src/workflow.ts`, including the fix that
made the loop converge: a revising specialist only ever sees its own proposal,
so `detect_conflicts` has to name the window it must avoid and who holds it.
Handing it bare clock times made it guess -- in one round the itinerary moved
an activity exactly onto the transport leg it was supposed to avoid, and the
budget overrun oscillated 29.25% -> 16.50% -> 25.25% without ever settling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from .budget import (
    ESCALATION_OVERRUN_PCT,
    NEGOTIATION_OVERRUN_PCT,
    assess_budget,
    cost_of,
    roll_up_cost,
)
from .contracts import (
    AgentProposal,
    HitlCheckpoint,
    RevisionRequest,
    TripBrief,
    TripPlan,
    TripSection,
)
from .memory import memory as default_memory
from .ports import AgentContext, ToolGateway
from .specialists import ALL_SPECIALISTS
from .tools.maps import create_tool_gateway

DEFAULT_MAX_ROUNDS = 3


@dataclass
class ProgressEvent:
    """Emitted as each specialist starts, finishes or fails, so a UI can show
    per-agent state instead of one opaque spinner."""

    type: Literal["agent_started", "agent_completed", "agent_failed"]
    agent: str
    round: int
    error: str | None = None


@dataclass
class OrchestratorOptions:
    """Dependencies are injectable so the graph is testable without network or
    singleton state."""

    specialists: list[Any] | None = None
    tools: ToolGateway | None = None
    mem: Any | None = None
    max_rounds: int = DEFAULT_MAX_ROUNDS
    on_progress: Callable[[ProgressEvent], None] | None = None


class State(TypedDict, total=False):
    brief: TripBrief
    round: int
    proposals: list[AgentProposal]
    conflicts: list[RevisionRequest]
    plan: TripPlan


def _minutes(hhmm: str) -> int:
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


@traceable(name="detect_conflicts", run_type="chain")
def detect_conflicts(proposals: list[AgentProposal], brief: TripBrief) -> list[RevisionRequest]:
    """Budget overruns, agent-declared conflicts and cross-agent time overlaps.

    Traced explicitly: when the graph fails to converge, the question is always
    which conflict recurred and what constraint each round actually sent, and
    that is invisible from the model calls alone.
    """
    _, overrun_pct = assess_budget([cost_of(p) for p in proposals], brief.budgetTotal)
    pending: dict[str, tuple[list[str], list[str]]] = {}

    def add(agent: str, reason: str, constraint: str) -> None:
        reasons, constraints = pending.setdefault(agent, ([], []))
        if reason not in reasons:
            reasons.append(reason)
        if constraint not in constraints:
            constraints.append(constraint)

    if overrun_pct > NEGOTIATION_OVERRUN_PCT:
        # Ask the two most expensive contributors, not everyone: a specialist
        # with no cost has nothing to give back.
        costly = sorted((p for p in proposals if cost_of(p) > 0), key=cost_of, reverse=True)[:2]
        for proposal in costly:
            add(
                proposal.agent,
                f"plan is {overrun_pct:.2f}% over budget",
                f"cut {proposal.agent} cost by ~30%",
            )

    for proposal in proposals:
        for reason in proposal.conflictsWith:
            add(proposal.agent, reason, "make the route geographically feasible")

    scheduled = [
        (proposal.agent, item)
        for proposal in proposals
        for item in proposal.items
        if item.day is not None and item.startTime and item.endTime
    ]
    for i in range(len(scheduled)):
        for j in range(i + 1, len(scheduled)):
            left_agent, left = scheduled[i]
            right_agent, right = scheduled[j]
            if left.day != right.day:
                continue
            overlaps = _minutes(left.startTime) < _minutes(right.endTime) and _minutes(
                right.startTime
            ) < _minutes(left.endTime)
            if not overlaps:
                continue
            # The itinerary owns the flexible half of a schedule clash: a
            # transport leg is a fixed departure, an activity can move.
            targets = (
                ["itinerary"]
                if "itinerary" in (left_agent, right_agent)
                else [left_agent, right_agent]
            )
            reason = (
                f"time overlap on day {left.day}: {left.startTime}-{left.endTime} "
                f"conflicts with {right.startTime}-{right.endTime}"
            )
            for target in dict.fromkeys(targets):
                blocker_agent, blocker = (
                    (right_agent, right) if target == left_agent else (left_agent, left)
                )
                add(
                    target,
                    reason,
                    f"on day {left.day} keep clear of {blocker.startTime}-{blocker.endTime}, "
                    f"held by {blocker_agent}"
                    f"{f' ({blocker.location})' if blocker.location else ''}; "
                    "reschedule without changing trip dates",
                )

    return [
        RevisionRequest(
            tripId=brief.tripId,
            targetAgent=agent,  # type: ignore[arg-type]
            reason="; ".join(reasons),
            constraints=constraints,
        )
        for agent, (reasons, constraints) in pending.items()
    ]


def _build_hitl(
    brief: TripBrief, overrun_pct: float, unresolved: bool, max_rounds: int
) -> list[HitlCheckpoint]:
    items = [
        HitlCheckpoint(
            id="confirm-brief",
            type="confirm_brief",
            title="Confirm your trip basics",
            detail=(
                f"{brief.destination} · {brief.dates[0]} to {brief.dates[1]} · "
                f"{brief.groupSize} people · ${brief.budgetTotal:,.0f}"
            ),
            status="pending",
        )
    ]
    if overrun_pct > ESCALATION_OVERRUN_PCT or unresolved:
        items.append(
            HitlCheckpoint(
                id="escalation",
                type="escalation",
                title="Needs a human decision",
                detail=(
                    f"Agents did not converge within {max_rounds} rounds."
                    if unresolved
                    else f"Plan is {overrun_pct:.2f}% over budget."
                ),
                status="pending",
            )
        )
    return items


def _resolve(options: OrchestratorOptions):
    specialists = options.specialists or ALL_SPECIALISTS
    if not specialists:
        raise ValueError("The orchestrator requires at least one specialist.")
    by_name = {s.name: s for s in specialists}
    if len(by_name) != len(specialists):
        raise ValueError("Specialist names must be unique.")
    if options.max_rounds < 1:
        raise ValueError("max_rounds must be a positive integer.")
    return (
        specialists,
        by_name,
        options.tools or create_tool_gateway(),
        options.mem or default_memory,
        options.max_rounds,
    )


def create_orchestrator_graph(options: OrchestratorOptions | None = None):
    options = options or OrchestratorOptions()
    specialists, by_name, tools, mem, max_rounds = _resolve(options)
    emit = options.on_progress or (lambda event: None)

    def context(brief: TripBrief, round_no: int) -> AgentContext:
        return AgentContext(tripId=brief.tripId, round=round_no, tools=tools, mem=mem)

    def run_one(specialist, brief, round_no, revision=None) -> AgentProposal:
        emit(ProgressEvent("agent_started", specialist.name, round_no))
        try:
            proposal = specialist.invoke(brief, context(brief, round_no), revision)
            emit(ProgressEvent("agent_completed", specialist.name, round_no))
            return proposal
        except Exception as error:
            emit(ProgressEvent("agent_failed", specialist.name, round_no, str(error)))
            raise

    def dispatch(state: State) -> State:
        brief = state["brief"]
        return {
            "round": 1,
            "proposals": [run_one(s, brief, 1) for s in specialists],
        }

    def detect(state: State) -> State:
        return {"conflicts": detect_conflicts(state["proposals"], state["brief"])}

    def revise(state: State) -> State:
        round_no = state["round"] + 1
        brief = state["brief"]
        by_agent = {c.targetAgent: c for c in state["conflicts"]}
        revised: list[AgentProposal] = []
        for proposal in state["proposals"]:
            request = by_agent.get(proposal.agent)
            specialist = by_name.get(proposal.agent)
            if request is None or specialist is None:
                revised.append(proposal)
                continue
            revised.append(run_one(specialist, brief, round_no, request))
        return {"round": round_no, "proposals": revised}

    def build_plan(state: State) -> State:
        brief, proposals, conflicts = state["brief"], state["proposals"], state["conflicts"]
        unresolved = len(conflicts) > 0
        conflicting = {c.targetAgent for c in conflicts}
        sections = [
            TripSection(
                id=p.agent,
                label=by_name[p.agent].label if p.agent in by_name else p.agent,
                summary=p.summary,
                status="needs_you" if p.agent in conflicting else "draft",
                estCost=cost_of(p),
                proposal=p,
            )
            for p in proposals
        ]
        est_total, overrun_pct = roll_up_cost(sections, brief.budgetTotal)
        return {
            "plan": TripPlan(
                tripId=brief.tripId,
                brief=brief,
                round=state["round"],
                budgetTotal=brief.budgetTotal,
                estTotal=est_total,
                overrunPct=overrun_pct,
                sections=sections,
                hitl=_build_hitl(brief, overrun_pct, unresolved, max_rounds),
            )
        }

    def route_after_detection(state: State) -> str:
        return (
            "revise_conflicts"
            if state["conflicts"] and state["round"] < max_rounds
            else "build_plan"
        )

    graph = StateGraph(State)
    graph.add_node("dispatch_specialists", dispatch)
    graph.add_node("detect_conflicts", detect)
    graph.add_node("revise_conflicts", revise)
    graph.add_node("build_plan", build_plan)
    graph.add_edge(START, "dispatch_specialists")
    graph.add_edge("dispatch_specialists", "detect_conflicts")
    graph.add_conditional_edges(
        "detect_conflicts", route_after_detection, ["revise_conflicts", "build_plan"]
    )
    graph.add_edge("revise_conflicts", "detect_conflicts")
    graph.add_edge("build_plan", END)
    return graph.compile()


def run_orchestrator(brief: TripBrief, options: OrchestratorOptions | None = None) -> TripPlan:
    result = create_orchestrator_graph(options).invoke({"brief": brief})
    plan = result.get("plan")
    if plan is None:
        raise RuntimeError("The orchestrator graph finished without a trip plan.")
    return plan
