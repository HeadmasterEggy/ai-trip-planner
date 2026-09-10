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

import operator
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from langsmith import traceable

from .budget import (
    ESCALATION_OVERRUN_PCT,
    NEGOTIATION_OVERRUN_PCT,
    assess_budget,
    cost_of,
    roll_up_cost,
    sum_usd,
)
from .contracts import (
    AgentProposal,
    ChoiceOption,
    HitlCheckpoint,
    NegotiationRound,
    ProgressEvent,
    ProposalItem,
    RevisionRequest,
    SpecialistTrace,
    TripBrief,
    TripPlan,
    TripSection,
    brief_problem,
    merge_choices,
)
from .memory import memory as default_memory
from .ports import AgentContext, ToolGateway
from .specialists import ALL_SPECIALISTS
from .specialists.base import stamp_duration
from .supervisor import dispatch_with_supervisor, revise_with_supervisor
from .tools.maps import create_tool_gateway

DEFAULT_MAX_ROUNDS = 3


@dataclass
class OrchestratorOptions:
    """Dependencies are injectable so the graph is testable without network or
    singleton state.

    Passing `specialists` explicitly is also the deterministic seam: it skips
    supervisor delegation, so a test never depends on a model choosing to call
    every tool.
    """

    specialists: list[Any] | None = None
    tools: ToolGateway | None = None
    mem: Any | None = None
    max_rounds: int = DEFAULT_MAX_ROUNDS
    # Decisions already made by the traveller, as preference key -> chosen id.
    # They are written into memory before planning so the specialists simply
    # read them as confirmed preferences, rather than the graph special-casing
    # a decision after the fact.
    decisions: dict[str, str] | None = None


class State(TypedDict, total=False):
    brief: TripBrief
    round: int
    max_rounds: int
    proposals: list[AgentProposal]
    conflicts: list[RevisionRequest]
    negotiation: list[NegotiationRound]
    stalled: bool
    plan: TripPlan
    # Set once the traveller has answered an escalation, so the pause happens at
    # most once per run and the node stays idempotent across a resume.
    escalation_decided: bool
    escalation_revise: bool
    # Worker diagnostics and candidates, as state rather than a dict the nodes
    # passed around. Reducers because a round can append from several specialists
    # (and, under the supervisor, from several threads) -- item 2.2 of
    # docs/framework-alignment.md.
    traces: Annotated[list[SpecialistTrace], operator.add]
    stay_choices: Annotated[dict[str, list[ChoiceOption]], merge_choices]


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
        excess = sum_usd([cost_of(p) for p in proposals]) - brief.budgetTotal
        share_total = sum(cost_of(p) for p in costly)
        for proposal in costly:
            # Apportion the actual shortfall by each one's share of the spend,
            # rather than asking for a flat 30%. A plan USD 30 over does not
            # need two specialists to find USD 840 between them, and a plan far
            # over needs more than 30% from each.
            own = cost_of(proposal)
            target = excess * (own / share_total) if share_total else excess
            add(
                proposal.agent,
                f"plan is {overrun_pct:.2f}% over budget",
                (
                    f"cut {proposal.agent} cost by about USD {target:,.2f} "
                    f"(from USD {own:,.2f}) to close the plan's USD {excess:,.2f} overrun"
                ),
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


def _choice_checkpoints(
    choices: dict[str, list[ChoiceOption]], decided: dict[str, str]
) -> list[HitlCheckpoint]:
    """Turn the candidates a specialist considered into decisions on offer.

    The specialist has already picked a sensible default, so these are never
    blocking: a traveller who ignores them still gets a complete plan.
    """
    checkpoints = []
    for city, options in sorted(choices.items()):
        if len(options) < 2:
            continue  # nothing to decide between
        key = f"accommodation.stayChoice.{city}"
        selected = decided.get(key) or next((o.id for o in options if o.recommended), None)
        checkpoints.append(
            HitlCheckpoint(
                id=f"choose-stay-{city}",
                type="confirm_choice",
                title=f"Choose where to stay in {city}",
                detail=f"{len(options)} options the specialist compared.",
                status="approved" if key in decided else "pending",
                options=options,
                selected=selected,
                preferenceKey=key,
            )
        )
    return checkpoints


def _escalation_detail(overrun_pct: float, unresolved: bool, stalled: bool, max_rounds: int) -> str:
    """Say which of the two very different failures happened.

    Running out of rounds means the negotiation was still moving; stalling means
    it was not, and another round would not have helped.
    """
    if stalled:
        return (
            "The specialists involved had nothing further to give, so the negotiation "
            "stopped early. This needs a change to the brief rather than another round."
        )
    if unresolved:
        return f"Agents did not converge within {max_rounds} rounds."
    return f"Plan is {overrun_pct:.2f}% over budget."


def _build_hitl(
    brief: TripBrief,
    overrun_pct: float,
    unresolved: bool,
    stalled: bool,
    max_rounds: int,
    *,
    decided: bool = False,
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
                detail=_escalation_detail(overrun_pct, unresolved, stalled, max_rounds),
                # Answered by the traveller at the pause, so it stops asking. The
                # overrun itself is still on the budget bar either way.
                status="approved" if decided else "pending",
            )
        )
    return items


@dataclass
class TripRun:
    """One run's dependencies, injected per invoke rather than captured.

    The graph is compiled once for the process, so nothing about a particular
    trip may be closed over: specialists, ports, memory, the round limit and the
    decisions all arrive here instead (items 2.1 and 2.3 of
    `docs/framework-alignment.md`).
    """

    specialists: list[Any]
    by_name: dict[str, Any]
    tools: ToolGateway
    mem: Any
    max_rounds: int
    decisions: dict[str, str]
    # Passing specialists explicitly skips supervisor delegation. This is the
    # deterministic seam a test uses, so it never depends on a model choosing to
    # call every tool.
    deterministic: bool


def _run_context(options: OrchestratorOptions | None) -> TripRun:
    options = options or OrchestratorOptions()
    specialists = options.specialists or ALL_SPECIALISTS
    if not specialists:
        raise ValueError("The orchestrator requires at least one specialist.")
    by_name = {s.name: s for s in specialists}
    if len(by_name) != len(specialists):
        raise ValueError("Specialist names must be unique.")
    if options.max_rounds < 1:
        raise ValueError("max_rounds must be a positive integer.")
    return TripRun(
        specialists=specialists,
        by_name=by_name,
        tools=options.tools or create_tool_gateway(),
        mem=options.mem or default_memory,
        max_rounds=options.max_rounds,
        decisions=options.decisions or {},
        deterministic=options.specialists is not None,
    )


def _escalation_payload(state: State, rounds_left: bool) -> dict[str, Any]:
    """What the pause shows, and the answers it offers.

    Only the options that can actually be carried out are offered: "revise" needs a
    round to spend, and once the limit is reached the only honest answer is to keep
    the plan and see the overrun.
    """
    plan = state["plan"]
    return {
        "kind": "escalation",
        "title": "This needs your call",
        "detail": plan.brief.destination
        + f" · {plan.overrunPct:+.2f}% against budget"
        + (" · conflicts unresolved" if state["conflicts"] else ""),
        "overrunPct": plan.overrunPct,
        "options": [
            {"value": "accept", "label": "Keep this plan as it is"},
            *(
                [
                    {
                        "value": "revise",
                        "label": "Send the costliest sections back for one more round",
                    }
                ]
                if rounds_left
                else []
            ),
        ],
    }


# One checkpointer for the process. A thread is deleted as soon as a run finishes
# without pausing, so this stays bounded; a paused run keeps its thread until the
# traveller answers.
#
# The contract types are listed explicitly rather than relying on the permissive
# default: langgraph warns on every unregistered type it deserializes and will
# block them in a future version. This is also the honest list of what crosses a
# checkpoint boundary.
_CHECKPOINTER = InMemorySaver(
    serde=JsonPlusSerializer(
        allowed_msgpack_modules=[
            AgentProposal,
            ChoiceOption,
            HitlCheckpoint,
            NegotiationRound,
            ProposalItem,
            RevisionRequest,
            SpecialistTrace,
            TripBrief,
            TripPlan,
            TripSection,
        ]
    )
)


def _thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _agent_context(
    run: TripRun, brief: TripBrief, round_no: int, extras: dict[str, Any]
) -> AgentContext:
    """A context for one specialist invocation.

    `extras` is fresh per invocation, so what a specialist reports back is exactly
    what it produced: the caller turns that into state, instead of diffing a
    run-long accumulator.
    """
    return AgentContext(
        tripId=brief.tripId, round=round_no, tools=run.tools, mem=run.mem, extras=extras
    )


def _write_progress(event: ProgressEvent) -> None:
    """Put one event on the run's custom stream.

    Called from the node's own thread. A no-op when the graph is invoked rather
    than streamed, which is why `run_orchestrator` carries no progress plumbing
    at all.
    """
    get_stream_writer()(event)


def create_orchestrator_graph():
    """Build the trip graph.

    Every node reads the run it is serving from `Runtime.context`, so one
    compiled graph serves every trip (see `_GRAPH`).
    """

    def invoke_specialist(run: TripRun, specialist, brief, round_no, revision=None):
        """Run one specialist: its proposal, and what it reported.

        The second half is what the specialist wrote to its context's `extras` --
        fresh per call, so it is exactly this specialist's diagnostics and
        candidates, ready to become state.

        Deliberately does no reporting of its own: this is the unit that runs off
        the node's thread, and a stream writer resolves its config from a context
        variable that does not cross threads. The node owns every progress write.
        """
        produced: dict[str, Any] = {}
        started = time.perf_counter()
        proposal = specialist.invoke(
            brief, _agent_context(run, brief, round_no, produced), revision
        )
        stamp_duration(produced, round(time.perf_counter() - started, 3))
        return proposal, produced

    def run_one(run: TripRun, specialist, brief, round_no, revision=None):
        """Run one specialist on this thread, reporting its progress as it goes."""
        _write_progress(ProgressEvent("agent_started", specialist.name, round_no))
        try:
            result = invoke_specialist(run, specialist, brief, round_no, revision)
        except Exception as error:
            _write_progress(ProgressEvent("agent_failed", specialist.name, round_no, str(error)))
            raise
        _write_progress(ProgressEvent("agent_completed", specialist.name, round_no))
        return result

    def collect(reported: list[dict[str, Any]]) -> dict[str, Any]:
        """Fold per-specialist reports into one state update."""
        traces: list[SpecialistTrace] = []
        choices: dict[str, list[ChoiceOption]] = {}
        for produced in reported:
            traces += produced.get("traces", [])
            choices = {**choices, **produced.get("stay_choices", {})}
        return {"traces": traces, "stay_choices": choices}

    def deterministic_dispatch(run: TripRun, brief: TripBrief) -> tuple[list, dict[str, Any]]:
        """Run every specialist concurrently.

        They are independent and pure over `(brief, context)`, and the reports
        come back as values rather than into a shared accumulator, so nothing
        depends on the order they finish in -- the caller restores registration
        order. With no model configured this is the path a deployment runs, so its
        latency is the plan's latency.

        Progress is reported here rather than inside the worker, for the reason in
        `invoke_specialist`: a writer captured from the graph does not work off the
        node's thread. Each specialist still gets its `agent_started`, and its
        `agent_completed` lands as that future resolves, so the UI's rows still
        update while the others run.
        """
        for specialist in run.specialists:
            _write_progress(ProgressEvent("agent_started", specialist.name, 1))

        results: dict[str, tuple] = {}
        with ThreadPoolExecutor(max_workers=len(run.specialists)) as pool:
            futures = {pool.submit(invoke_specialist, run, s, brief, 1): s for s in run.specialists}
            for future in as_completed(futures):
                specialist = futures[future]
                try:
                    results[specialist.name] = future.result()
                except Exception as error:
                    _write_progress(ProgressEvent("agent_failed", specialist.name, 1, str(error)))
                    raise
                _write_progress(ProgressEvent("agent_completed", specialist.name, 1))

        ordered = [results[s.name] for s in run.specialists if s.name in results]
        return [proposal for proposal, _ in ordered], collect([p for _, p in ordered])

    def dispatch(state: State, runtime: Runtime[TripRun]) -> State:
        run = runtime.context
        brief = state["brief"]
        if run.deterministic:
            proposals, reported = deterministic_dispatch(run, brief)
            return {
                "round": 1,
                "max_rounds": run.max_rounds,
                "proposals": proposals,
                **reported,
            }
        try:
            # The delegation tools emit on the nested agent's custom stream, which
            # `dispatch_with_supervisor` forwards here; the tools themselves run on
            # a thread pool, so this is the hop that keeps the writes on our thread.
            # What they produced comes back as the nested agent's state, which is
            # why the result is an outcome and not just a list of proposals.
            outcome = dispatch_with_supervisor(
                run.specialists, brief, _agent_context(run, brief, 1, {}), _write_progress
            )
            proposals = outcome.proposals
            reported: dict[str, Any] = {
                "traces": outcome.traces,
                "stay_choices": outcome.stay_choices,
            }
            # The supervisor may legitimately skip a specialist. Fill the gaps so
            # the plan always has all five sections rather than silently losing one.
            missing = [s for s in run.specialists if s.name not in {p.agent for p in proposals}]
            if missing:
                by_agent = {p.agent: p for p in proposals}
                backfilled = []
                for specialist in missing:
                    proposal, produced = run_one(run, specialist, brief, 1)
                    by_agent[specialist.name] = proposal
                    backfilled.append(produced)
                proposals = [by_agent[s.name] for s in run.specialists]
                filled = collect(backfilled)
                reported = {
                    "traces": [*reported["traces"], *filled["traces"]],
                    "stay_choices": {**reported["stay_choices"], **filled["stay_choices"]},
                }
        except Exception as error:  # noqa: BLE001
            print(f"[supervisor] Delegation unavailable; using deterministic dispatch: {error}")
            proposals, reported = deterministic_dispatch(run, brief)
        return {"round": 1, "max_rounds": run.max_rounds, "proposals": proposals, **reported}

    def detect(state: State) -> State:
        conflicts = detect_conflicts(state["proposals"], state["brief"])
        history = list(state.get("negotiation", []))
        history.append(NegotiationRound(round=state["round"], conflicts=conflicts))
        return {"conflicts": conflicts, "negotiation": history}

    def revise(state: State, runtime: Runtime[TripRun]) -> State:
        run = runtime.context
        round_no = state["round"] + 1
        brief = state["brief"]
        requests = state["conflicts"]
        by_agent = {c.targetAgent: c for c in requests}

        def deterministic_revision() -> tuple[list[AgentProposal], dict[str, Any]]:
            out: list[AgentProposal] = []
            reported: list[dict[str, Any]] = []
            for proposal in state["proposals"]:
                request = by_agent.get(proposal.agent)
                specialist = run.by_name.get(proposal.agent)
                if request is None or specialist is None:
                    out.append(proposal)
                    continue
                revised, produced = run_one(run, specialist, brief, round_no, request)
                out.append(revised)
                reported.append(produced)
            return out, collect(reported)

        history = list(state.get("negotiation", []))
        if history:
            history[-1] = history[-1].model_copy(
                update={"revised": [r.targetAgent for r in requests]}
            )

        if run.deterministic:
            proposals, reported = deterministic_revision()
        else:
            try:
                outcome = revise_with_supervisor(
                    run.specialists,
                    state["proposals"],
                    requests,
                    brief,
                    _agent_context(run, brief, round_no, {}),
                    _write_progress,
                )
                proposals = outcome.proposals
                reported = {"traces": outcome.traces, "stay_choices": outcome.stay_choices}
            except Exception as error:  # noqa: BLE001
                print(
                    "[supervisor] Revision delegation unavailable; using deterministic "
                    f"routing: {error}"
                )
                proposals, reported = deterministic_revision()

        # A revision that moved no money for any specialist it targeted has
        # nothing further to give. Re-asking would spend the remaining rounds
        # producing an identical plan, so record it and stop.
        before = {p.agent: cost_of(p) for p in state["proposals"]}
        after = {p.agent: cost_of(p) for p in proposals}
        targeted = [r.targetAgent for r in requests]
        stalled = bool(targeted) and all(
            before.get(agent) == after.get(agent) for agent in targeted
        )
        if stalled and history:
            history[-1] = history[-1].model_copy(update={"stalled": True})

        return {
            "round": round_no,
            "proposals": proposals,
            "negotiation": history,
            "stalled": stalled,
            # Consumed: the traveller is asked at most once per run.
            "escalation_revise": False,
            **reported,
        }

    def build_plan(state: State, runtime: Runtime[TripRun]) -> State:
        run = runtime.context
        brief, proposals, conflicts = state["brief"], state["proposals"], state["conflicts"]
        unresolved = len(conflicts) > 0
        conflicting = {c.targetAgent for c in conflicts}
        sections = [
            TripSection(
                id=p.agent,
                label=run.by_name[p.agent].label if p.agent in run.by_name else p.agent,
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
                hitl=[
                    *_build_hitl(
                        brief,
                        overrun_pct,
                        unresolved,
                        state.get("stalled", False),
                        state["max_rounds"],
                        decided=state.get("escalation_decided", False),
                    ),
                    *_choice_checkpoints(state.get("stay_choices", {}), run.decisions),
                ],
                negotiation=state.get("negotiation", []),
                traces=state.get("traces", []),
            )
        }

    def await_decision(state: State, runtime: Runtime[TripRun]) -> State:
        """Pause where the plan already says a human must decide (item 3.2).

        Runs after `build_plan`, so the traveller answers with the plan in front of
        them. The answer arrives as `interrupt()`'s return value: accept leaves the
        plan as it is, revise sends the conflicts already detected back to their
        owners -- one or two specialists, not five.

        Idempotent across a resume, because `interrupt` re-executes its node from the
        top. Bounded, because "revise" is only offered while the round limit has room
        and is consumed once: the escalation is answered at most once per run.
        """
        plan = state.get("plan")
        if plan is None or state.get("escalation_decided"):
            return {}
        escalating = plan.overrunPct > ESCALATION_OVERRUN_PCT or bool(state["conflicts"])
        if not escalating:
            return {}

        # A revision is offered only when it could still help. By construction the
        # pause is reached only when it cannot: the route here means no conflicts
        # remain, the round limit is spent, or the negotiation stalled (a revision
        # that moved no money). Offering "revise" at that point would be a button
        # that does nothing, so the honest answer set is to keep the plan and to know
        # why it is over.
        rounds_left = (
            state["round"] < state["max_rounds"]
            and bool(state["conflicts"])
            and not state.get("stalled")
        )
        answer = interrupt(_escalation_payload(state, rounds_left))
        if answer == "revise" and rounds_left:
            return {"escalation_decided": True, "escalation_revise": True}
        # Accepted: mark it here rather than rebuilding, so the plan the traveller
        # was looking at is the plan that becomes final.
        answered = plan.model_copy(
            update={
                "hitl": [
                    checkpoint.model_copy(update={"status": "approved"})
                    if checkpoint.type == "escalation"
                    else checkpoint
                    for checkpoint in plan.hitl
                ]
            }
        )
        return {"escalation_decided": True, "escalation_revise": False, "plan": answered}

    def route_after_decision(state: State) -> str:
        return "revise_conflicts" if state.get("escalation_revise") else END

    def route_after_detection(state: State) -> str:
        if not state["conflicts"] or state["round"] >= state["max_rounds"]:
            return "build_plan"
        if state.get("stalled"):
            return "build_plan"
        return "revise_conflicts"

    graph = StateGraph(State)
    graph.add_node("dispatch_specialists", dispatch)
    graph.add_node("detect_conflicts", detect)
    graph.add_node("revise_conflicts", revise)
    graph.add_node("build_plan", build_plan)
    graph.add_node("await_decision", await_decision)
    graph.add_edge(START, "dispatch_specialists")
    graph.add_edge("dispatch_specialists", "detect_conflicts")
    graph.add_conditional_edges(
        "detect_conflicts", route_after_detection, ["revise_conflicts", "build_plan"]
    )
    graph.add_edge("revise_conflicts", "detect_conflicts")
    # The pause comes after the plan is built, so the decision is made with the plan
    # in front of the traveller rather than blind.
    graph.add_edge("build_plan", "await_decision")
    graph.add_conditional_edges("await_decision", route_after_decision, ["revise_conflicts", END])
    return graph.compile(checkpointer=_CHECKPOINTER)


# One compile for the process: nothing about a run is captured in the graph, so
# there is nothing to rebuild per request (item 2.3).
_GRAPH = create_orchestrator_graph()


def _checked_brief(brief: TripBrief) -> None:
    problem = brief_problem(brief)
    if problem:
        # Refused before any specialist runs, so the caller gets one reason
        # instead of a graph that fails four agents deep.
        raise ValueError(problem)


def run_orchestrator(brief: TripBrief, options: OrchestratorOptions | None = None) -> TripPlan:
    """Plan a brief, discarding progress.

    The programmatic path: chat, decisions and tests call this. A caller that wants
    to watch the specialists work iterates `run_orchestrator_stream`. A run that
    reaches an escalation cannot be answered here -- use the stream, which exposes
    the pause and can resume it.
    """
    _checked_brief(brief)
    thread_id = _new_thread_id(brief)
    result = _GRAPH.invoke(
        {"brief": brief}, context=_run_context(options), config=_thread_config(thread_id)
    )
    _CHECKPOINTER.delete_thread(thread_id)
    plan = result.get("plan")
    if plan is None:
        raise RuntimeError("The orchestrator graph finished without a trip plan.")
    return plan


class PlanStream:
    """One planning run as progress events, and the plan it produced.

    A run is a single pass, so the plan cannot be the generator's return value and
    still be readable by the caller that is consuming the events: iterate the
    stream, then read `plan`. Both are None until the iterator is exhausted, and the
    stream can only be consumed once.

    `interrupt` is set when the run paused for a human. The thread is kept so
    `resume` can continue it; a run that did not pause has its checkpoint dropped,
    which is what keeps the in-process saver bounded.

    The generator body runs on the consumer's thread, which is the point: the
    specialists report through the graph's custom stream (and, under the supervisor,
    from a thread pool), so a consumer never has to know which thread a specialist
    ran on. `ui/live.py` existed to patch exactly that, and is gone.
    """

    def __init__(
        self,
        brief: TripBrief,
        run: TripRun,
        *,
        resume: Any = None,
        thread_id: str | None = None,
    ) -> None:
        self._brief = brief
        self._run = run
        self._resume = resume
        self.thread_id = thread_id or _new_thread_id(brief)
        self._consumed = False
        self.plan: TripPlan | None = None
        self.interrupt: dict[str, Any] | None = None

    def __iter__(self) -> Iterator[ProgressEvent]:
        if self._consumed:
            # Iterating again would silently run the whole graph a second time:
            # five specialists, up to three rounds, and their model calls.
            raise RuntimeError("A PlanStream drives one run and can only be consumed once.")
        self._consumed = True

        starting = (
            Command(resume=self._resume) if self._resume is not None else {"brief": self._brief}
        )
        for mode, payload in _GRAPH.stream(
            starting,
            context=self._run,
            config=_thread_config(self.thread_id),
            stream_mode=["updates", "custom"],
        ):
            if mode == "custom":
                yield payload
                continue
            if not isinstance(payload, dict):
                continue
            if "__interrupt__" in payload:
                self.interrupt = payload["__interrupt__"][0].value
                continue
            # Any node may publish the plan: `build_plan` builds it, and
            # `await_decision` publishes the answered copy on the way out.
            for update in payload.values():
                if isinstance(update, dict) and "plan" in update:
                    self.plan = update["plan"]

        if self.interrupt is None:
            # Nothing to resume, so the checkpoint is only memory. A paused run keeps
            # its thread until the traveller answers.
            _CHECKPOINTER.delete_thread(self.thread_id)


def forget_thread(thread_id: str) -> None:
    """Drop a paused run's checkpoint.

    Called when a pause is superseded -- a traveller who asks something else instead
    of answering it -- so the in-process saver does not accumulate threads nobody
    will ever resume.
    """
    _CHECKPOINTER.delete_thread(thread_id)


def _new_thread_id(brief: TripBrief) -> str:
    """A fresh thread per run.

    The checkpointer accumulates state per thread, and the reducer channels
    (`traces`, `stay_choices`) would otherwise carry a previous run's entries into
    the next one on the same trip.
    """
    return f"{brief.tripId}-{uuid4().hex[:12]}"


def run_orchestrator_stream(
    brief: TripBrief,
    options: OrchestratorOptions | None = None,
    *,
    resume: Any = None,
    thread_id: str | None = None,
) -> PlanStream:
    """Plan a brief, reporting each specialist as it starts, finishes or fails.

    Pass `resume` with the paused run's `thread_id` to answer an escalation.
    """
    _checked_brief(brief)
    return PlanStream(brief, _run_context(options), resume=resume, thread_id=thread_id)
