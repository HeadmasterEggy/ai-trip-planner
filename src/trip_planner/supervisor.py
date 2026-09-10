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
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain.tools import tool

from .contracts import AgentProposal, RevisionRequest, TripBrief
from .models import create_routed_chat_model
from .ports import AgentContext

DISPATCH_PROMPT = (
    "You are the trip-planning supervisor. Decide which specialist tools are needed for the "
    "requested plan, call one tool per specialist you need, and do not perform specialist work "
    "yourself. A complete new trip plan usually needs day planning, inter-city transport, "
    "accommodation, destination guidance and dining. Do not invent or modify trip facts. Stop "
    "once the necessary specialists have returned; a deterministic workflow validates and "
    "reconciles their proposals."
)

REVISION_PROMPT = (
    "You are the trip revision supervisor. Call every provided revision tool exactly once so "
    "each validated conflict reaches its targeted owner. Do not rewrite requests, constraints or "
    "trip facts, and do not solve specialist work yourself. Stop after all revision tools return; "
    "the workflow will re-run deterministic conflict validation."
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
        ToolRetryMiddleware(max_retries=1, on_failure="error"),
        ToolCallLimitMiddleware(run_limit=tool_count * TOOL_CALLS_PER_SPECIALIST),
        # Model chain. A transient provider failure is retried and backed off
        # instead of silently downgrading every section to its fallback, which is
        # what a single timeout used to do.
        ModelRetryMiddleware(max_retries=2, backoff_factor=2.0),
        ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT),
    ]


def create_supervisor_tools(
    specialists: list[Any],
    brief: TripBrief,
    ctx: AgentContext,
    on_proposal: Callable[[AgentProposal], None],
    on_progress: Callable[[str, str, int, str | None], None],
) -> list[Any]:
    """One tool per specialist, bound to this run's brief and context.

    The tools take no arguments on purpose, and that is the point of the module:
    the supervisor's only freedom is *which* specialist runs. The brief, the
    memory store and the tool gateway are captured here, and each specialist
    derives its own prompt from the brief. An argument would be somewhere for the
    model to restate the request, and the deterministic rules downstream would
    then be checking the restatement rather than what the traveller asked for.
    """
    tools = []
    for specialist in specialists:

        def make(specialist=specialist):
            @tool(
                _tool_name("ask", specialist.name),
                description=(
                    f"Ask the {specialist.label} specialist to plan its section of the trip. "
                    "Use this when its domain is needed for the requested plan."
                ),
            )
            def delegate() -> str:
                on_progress("agent_started", specialist.name, ctx.round, None)
                try:
                    proposal = specialist.invoke(brief, ctx, None)
                except Exception as error:
                    on_progress("agent_failed", specialist.name, ctx.round, str(error))
                    raise
                on_progress("agent_completed", specialist.name, ctx.round, None)
                on_proposal(proposal)
                return f"{specialist.label}: {proposal.summary}"

            return delegate

        tools.append(make())
    return tools


def create_revision_tools(
    specialists: list[Any],
    requests: list[RevisionRequest],
    brief: TripBrief,
    ctx: AgentContext,
    on_proposal: Callable[[AgentProposal], None],
    on_progress: Callable[[str, str, int, str | None], None],
) -> list[Any]:
    """One immutable tool per pending revision request.

    Like the dispatch tools these take no arguments: the validated request is
    captured, so the supervisor routes a constraint to its owner and cannot
    rewrite one.
    """
    by_name = {s.name: s for s in specialists}
    tools = []
    for request in requests:
        specialist = by_name.get(request.targetAgent)
        if specialist is None:
            continue

        def make(specialist=specialist, request=request):
            @tool(
                _tool_name("revise", request.targetAgent),
                description=(
                    f"Send the validated conflict and constraints to the {specialist.label} "
                    "specialist. The request is immutable and already targets this specialist."
                ),
            )
            def delegate() -> str:
                on_progress("agent_started", specialist.name, ctx.round, None)
                try:
                    proposal = specialist.invoke(brief, ctx, request)
                except Exception as error:
                    on_progress("agent_failed", specialist.name, ctx.round, str(error))
                    raise
                on_progress("agent_completed", specialist.name, ctx.round, None)
                if proposal.agent != request.targetAgent:
                    raise ValueError(
                        f"Revision tool returned {proposal.agent} for {request.targetAgent}."
                    )
                on_proposal(proposal)
                return f"{specialist.label}: {proposal.summary}"

            return delegate

        tools.append(make())
    return tools


def dispatch_with_supervisor(
    specialists: list[Any],
    brief: TripBrief,
    ctx: AgentContext,
    on_progress: Callable[[str, str, int, str | None], None],
) -> list[AgentProposal]:
    """Run the supervisor tool loop and return the proposals it collected.

    Raises when no model is configured or the supervisor delegates to nobody,
    so the caller can fall back to deterministic dispatch.
    """
    model = create_routed_chat_model("supervisor")
    if model is None:
        raise RuntimeError("Supervisor requires a configured routed chat model.")

    collected: dict[str, AgentProposal] = {}
    tools = create_supervisor_tools(
        specialists, brief, ctx, lambda p: collected.__setitem__(p.agent, p), on_progress
    )
    supervisor = create_agent(
        model=model,
        tools=tools,
        system_prompt=DISPATCH_PROMPT,
        middleware=supervisor_middleware(len(tools)),
    )
    supervisor.invoke(
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
        }
    )
    if not collected:
        raise RuntimeError("Supervisor completed without delegating to a specialist.")
    # Preserve the registration order so the plan's sections stay stable.
    return [collected[s.name] for s in specialists if s.name in collected]


def revise_with_supervisor(
    specialists: list[Any],
    proposals: list[AgentProposal],
    requests: list[RevisionRequest],
    brief: TripBrief,
    ctx: AgentContext,
    on_progress: Callable[[str, str, int, str | None], None],
) -> list[AgentProposal]:
    """Route validated revision requests through a named supervisor tool loop."""
    model = create_routed_chat_model("supervisor")
    if model is None:
        raise RuntimeError("Revision supervisor requires a configured routed chat model.")

    tools = create_revision_tools(
        specialists, requests, brief, ctx, lambda p: revised.__setitem__(p.agent, p), on_progress
    )
    revised: dict[str, AgentProposal] = {}
    if not tools:
        return proposals

    supervisor = create_agent(
        model=model,
        tools=tools,
        system_prompt=REVISION_PROMPT,
        middleware=supervisor_middleware(len(tools)),
    )
    supervisor.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Delegate every pending revision request to its typed specialist tool.\n"
                        + "\n".join(r.model_dump_json() for r in requests)
                    ),
                }
            ]
        }
    )
    if len(revised) != len(tools):
        raise RuntimeError(
            f"Revision supervisor delegated {len(revised)} of {len(tools)} pending request(s)."
        )
    return [revised.get(p.agent, p) for p in proposals]
