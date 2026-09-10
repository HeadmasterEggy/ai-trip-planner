"""Supervisor delegation.

A named supervisor agent decides which specialist tools to call. It cannot
alter the validated brief, the memory store or the tool gateway: those are
captured when the tools are built, so the supervisor's only freedom is *which*
specialist runs, never *what it is told about the trip*.

That boundary is the point. A supervisor free to rewrite trip facts would make
the plan depend on a model's paraphrase of the request, and the deterministic
conflict and budget rules downstream would then be validating the paraphrase
rather than what the traveller asked for.

Every path here degrades to deterministic dispatch when no model is configured
or the loop fails, so the workflow keeps running offline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain.tools import ToolRuntime, tool

from .contracts import AgentProposal, ProgressEvent, RevisionRequest, TripBrief
from .models import create_routed_chat_model
from .ports import AgentContext, ToolGateway
from .specialists import ALL_SPECIALISTS

DISPATCH_PROMPT = (
    "You are the trip-planning supervisor. Decide which specialist tools are needed for the "
    "requested plan, call one tool per specialist you need, and do not perform specialist work "
    "yourself. A complete new trip plan usually needs day planning, inter-city transport, "
    "accommodation, destination guidance and dining. Do not invent or modify trip facts. Stop "
    "once the necessary specialists have returned; a deterministic workflow validates and "
    "reconciles their proposals."
)

REVISION_PROMPT = (
    "You are the trip revision supervisor. Call the tool for each specialist the message "
    "lists as pending, exactly once each, and call no other tool. Do not rewrite requests, "
    "constraints or trip facts, and do not solve specialist work yourself. Stop once those "
    "tools have returned; the workflow re-runs deterministic conflict validation."
)


def _tool_name(prefix: str, agent: str) -> str:
    return f"{prefix}_{agent.replace('-', '_')}_specialist"


# Bounds on the supervisor's own loop. The graph has a round limit and each
# specialist has one corrective retry; these bound the delegation loop itself,
# which otherwise runs until LangGraph's recursion limit and surfaces as an
# exception the caller has to catch.
MODEL_CALL_LIMIT = 6
TOOL_CALLS_PER_SPECIALIST = 2


def _tool_failure_message(error: Exception, request: Any) -> str:
    """Tell the model which tool failed, without echoing the exception.

    The raw text can carry provider or filesystem detail, and the model needs
    only the tool name and the fact that retrying is pointless. Dropping the
    section is safe: the workflow fills in any specialist the supervisor did not
    successfully delegate to (see `workflow.dispatch`).
    """
    return (
        f"{request.tool_call['name']} failed with {type(error).__name__}. "
        "Leave that section to the workflow and continue with the rest."
    )


def _worth_retrying(error: Exception) -> bool:
    """Retry the failures that might not repeat; not our own validation.

    The specialists raise ValueError for deterministic problems -- no eligible
    stay, a draft that failed validation, a revision they have no lever for. The
    same input produces the same failure, and under the supervisor a second
    attempt costs model calls, so those go straight to the error handler.
    """
    return not isinstance(error, ValueError)


def supervisor_middleware(tool_count: int) -> list[Any]:
    """Resilience for the supervisor loop, in the order the hooks nest.

    Order is semantic, not cosmetic: the first entry is the *outermost* wrapper
    (`_chain_tool_call_wrappers`, "first = outermost"). The error handler is
    therefore first, so it sees whatever the retry finally re-raises; with the
    two swapped, the handler would convert the failure before the retry ever
    observed an exception and the retry would be dead code. The call limits come
    last so that every retry attempt is counted against them.
    """
    return [
        # Tool chain, outside in. A failing specialist becomes an error message
        # the model can work around -- before this it took the whole fan-out with
        # it (see docs/debugging-log.md, entry 11).
        ToolErrorMiddleware(on_error=_tool_failure_message),
        ToolRetryMiddleware(max_retries=1, retry_on=_worth_retrying, on_failure="error"),
        ToolCallLimitMiddleware(run_limit=tool_count * TOOL_CALLS_PER_SPECIALIST),
        # Model chain. A transient provider failure is retried and backed off
        # instead of silently downgrading every section to its fallback, which is
        # what a single timeout used to do.
        ModelRetryMiddleware(max_retries=2, backoff_factor=2.0),
        ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT),
    ]


@dataclass
class DelegationContext:
    """What the delegation tools read for one supervisor call.

    Injected through `ToolRuntime.context`, so the tools and the agent are built
    once for the process instead of being closures over a run (item 2.1 of
    `docs/framework-alignment.md`). The supervisor still cannot alter the trip:
    this object is constructed by the caller, never by the model.
    """

    brief: TripBrief
    round: int
    tools: ToolGateway
    mem: Any
    extras: dict[str, Any]
    # name -> specialist, for the run being served.
    specialists: dict[str, Any] = field(default_factory=dict)
    # What the tools collected, read by the caller once the loop finishes.
    proposals: dict[str, AgentProposal] = field(default_factory=dict)
    # target agent -> the validated request it must act on.
    requests: dict[str, RevisionRequest] = field(default_factory=dict)


def _agent_context(run: DelegationContext) -> AgentContext:
    return AgentContext(
        tripId=run.brief.tripId,
        round=run.round,
        tools=run.tools,
        mem=run.mem,
        extras=run.extras,
    )


def _specialist_for(run: DelegationContext, agent: str) -> Any:
    specialist = run.specialists.get(agent)
    if specialist is None:
        raise ValueError(f"{agent} is not part of this run.")
    return specialist


def _ask_tool(specialist: Any) -> Any:
    """The delegation tool for one specialist: no arguments, by design.

    The supervisor's only freedom is *which* specialist runs. The brief, the
    memory store and the tool gateway arrive in `runtime.context`, and each
    specialist derives its own prompt from the brief. An argument would be
    somewhere for the model to restate the request, and the deterministic rules
    downstream would then be checking the restatement rather than what the
    traveller asked for.

    Progress goes out through `runtime.stream_writer`, the documented way for a
    tool to narrate itself. It has to be the runtime's writer and not one captured
    from the graph: `ToolNode` runs a batch of tool calls on a thread pool, and a
    captured writer resolves its config from a context variable that does not
    cross threads.
    """

    @tool(
        _tool_name("ask", specialist.name),
        description=(
            f"Ask the {specialist.label} specialist to plan its section of the trip. "
            "Use this when its domain is needed for the requested plan."
        ),
    )
    def delegate(runtime: ToolRuntime[DelegationContext]) -> str:
        run = runtime.context
        resolved = _specialist_for(run, specialist.name)
        runtime.stream_writer(ProgressEvent("agent_started", specialist.name, run.round))
        try:
            proposal = resolved.invoke(run.brief, _agent_context(run), None)
        except Exception as error:
            runtime.stream_writer(
                ProgressEvent("agent_failed", specialist.name, run.round, str(error))
            )
            raise
        runtime.stream_writer(ProgressEvent("agent_completed", specialist.name, run.round))
        run.proposals[specialist.name] = proposal
        return f"{specialist.label}: {proposal.summary}"

    return delegate


def _revise_tool(specialist: Any) -> Any:
    """The revision tool for one specialist: the request is looked up, not passed.

    The model routes a validated constraint to its owner and cannot rewrite one --
    the request is the graph's, and it arrives in the run context.
    """

    @tool(
        _tool_name("revise", specialist.name),
        description=(
            f"Send the validated conflict and constraints to the {specialist.label} "
            "specialist. Call this only for a specialist listed as pending; the "
            "request is immutable and already targets it."
        ),
    )
    def delegate(runtime: ToolRuntime[DelegationContext]) -> str:
        run = runtime.context
        request = run.requests.get(specialist.name)
        if request is None:
            return f"Nothing pending for the {specialist.label} specialist."
        resolved = _specialist_for(run, specialist.name)
        runtime.stream_writer(ProgressEvent("agent_started", specialist.name, run.round))
        try:
            proposal = resolved.invoke(run.brief, _agent_context(run), request)
        except Exception as error:
            runtime.stream_writer(
                ProgressEvent("agent_failed", specialist.name, run.round, str(error))
            )
            raise
        runtime.stream_writer(ProgressEvent("agent_completed", specialist.name, run.round))
        if proposal.agent != request.targetAgent:
            raise ValueError(f"Revision tool returned {proposal.agent} for {request.targetAgent}.")
        run.proposals[specialist.name] = proposal
        return f"{specialist.label}: {proposal.summary}"

    return delegate


# One tool per specialist for the process. The tools read the run from
# `runtime.context`, so there is nothing per-run to rebuild.
ASK_TOOLS: dict[str, Any] = {s.name: _ask_tool(s) for s in ALL_SPECIALISTS}
REVISE_TOOLS: dict[str, Any] = {s.name: _revise_tool(s) for s in ALL_SPECIALISTS}


@lru_cache(maxsize=1)
def _dispatch_agent() -> Any:
    """The delegation agent, built on first use.

    Cached rather than module-level because building it needs credentials and the
    app must still import, and run, with none. Cached at all because the tools take
    no run-specific state: the only per-run input left arrives in `context`.
    """
    model = create_routed_chat_model("supervisor")
    if model is None:
        raise RuntimeError("Supervisor requires a configured routed chat model.")
    return create_agent(
        model=model,
        tools=list(ASK_TOOLS.values()),
        system_prompt=DISPATCH_PROMPT,
        middleware=supervisor_middleware(len(ASK_TOOLS)),
        context_schema=DelegationContext,
    )


@lru_cache(maxsize=1)
def _revision_agent() -> Any:
    """The revision agent, cached for the same reason as `_dispatch_agent`."""
    model = create_routed_chat_model("supervisor")
    if model is None:
        raise RuntimeError("Revision supervisor requires a configured routed chat model.")
    return create_agent(
        model=model,
        tools=list(REVISE_TOOLS.values()),
        system_prompt=REVISION_PROMPT,
        middleware=supervisor_middleware(len(REVISE_TOOLS)),
        context_schema=DelegationContext,
    )


def _delegation_run(
    specialists: list[Any],
    brief: TripBrief,
    ctx: AgentContext,
    requests: list[RevisionRequest] | None = None,
) -> DelegationContext:
    return DelegationContext(
        brief=brief,
        round=ctx.round,
        tools=ctx.tools,
        mem=ctx.mem,
        extras=ctx.extras,
        specialists={s.name: s for s in specialists},
        requests={r.targetAgent: r for r in requests or []},
    )


def _forward_custom_events(
    agent: Any, payload: dict[str, Any], run: DelegationContext, forward: Callable[[Any], None]
) -> None:
    """Run the nested agent, passing its custom events to our stream.

    The delegation tools write to the nested run's stream; a nested run is not
    part of the outer graph's stream, so the hop has to be explicit. It happens
    here, on the node's thread, which is what keeps the write off the tool's
    thread pool.
    """
    for event in agent.stream(payload, context=run, stream_mode="custom"):
        forward(event)


def dispatch_with_supervisor(
    specialists: list[Any],
    brief: TripBrief,
    ctx: AgentContext,
    forward_progress: Callable[[ProgressEvent], None],
) -> list[AgentProposal]:
    """Run the supervisor tool loop and return the proposals it collected.

    Raises when no model is configured or the supervisor delegates to nobody, so
    the caller can fall back to deterministic dispatch.
    """
    agent = _dispatch_agent()
    run = _delegation_run(specialists, brief, ctx)
    _forward_custom_events(
        agent,
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Delegate the specialist work required to produce this trip plan.\n"
                        f"{brief.model_dump_json()}"
                    ),
                }
            ]
        },
        run,
        forward_progress,
    )
    if not run.proposals:
        raise RuntimeError("Supervisor completed without delegating to a specialist.")
    # Preserve the registration order so the plan's sections stay stable.
    return [run.proposals[s.name] for s in specialists if s.name in run.proposals]


def revise_with_supervisor(
    specialists: list[Any],
    proposals: list[AgentProposal],
    requests: list[RevisionRequest],
    brief: TripBrief,
    ctx: AgentContext,
    forward_progress: Callable[[ProgressEvent], None],
) -> list[AgentProposal]:
    """Route validated revision requests through a named supervisor tool loop."""
    if not requests:
        return proposals
    agent = _revision_agent()
    run = _delegation_run(specialists, brief, ctx, requests)
    _forward_custom_events(
        agent,
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Send each of these validated conflicts to its owner, one tool call "
                        "each, and call nothing else:\n"
                        + "\n".join(
                            f"- {r.targetAgent}: {r.reason} | {'; '.join(r.constraints)}"
                            for r in requests
                        )
                    ),
                }
            ]
        },
        run,
        forward_progress,
    )
    if set(run.proposals) != set(run.requests):
        missing = sorted(set(run.requests) - set(run.proposals))
        raise RuntimeError(
            f"Revision supervisor delegated {len(run.proposals)} of "
            f"{len(run.requests)} pending request(s); missed {', '.join(missing)}."
        )
    return [run.proposals.get(p.agent, p) for p in proposals]
