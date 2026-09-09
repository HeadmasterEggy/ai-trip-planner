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
from langchain.tools import tool
from pydantic import BaseModel, Field

from .contracts import AgentProposal, RevisionRequest, TripBrief
from .models import create_routed_chat_model
from .ports import AgentContext

DISPATCH_PROMPT = (
    "You are the trip-planning supervisor. Decide which specialist tools are needed for the "
    "requested plan, delegate a bounded objective to each, and do not perform specialist work "
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


class _Delegation(BaseModel):
    objective: str = Field(description="The bounded planning objective for this specialist.")


def _tool_name(prefix: str, agent: str) -> str:
    return f"{prefix}_{agent.replace('-', '_')}_specialist"


def create_supervisor_tools(
    specialists: list[Any],
    brief: TripBrief,
    ctx: AgentContext,
    on_proposal: Callable[[AgentProposal], None],
    on_progress: Callable[[str, str, int, str | None], None],
) -> list[Any]:
    """One typed tool per specialist, bound to this run's brief and context."""
    tools = []
    for specialist in specialists:

        def make(specialist=specialist):
            @tool(
                _tool_name("ask", specialist.name),
                args_schema=_Delegation,
                description=(
                    f"Delegate a bounded task to the {specialist.label} specialist. "
                    "Use this when its domain is needed for the trip plan."
                ),
            )
            def delegate(objective: str) -> str:
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
    """One immutable, typed tool per pending revision request."""
    by_name = {s.name: s for s in specialists}
    tools = []
    for request in requests:
        specialist = by_name.get(request.targetAgent)
        if specialist is None:
            continue

        def make(specialist=specialist, request=request):
            @tool(
                _tool_name("revise", request.targetAgent),
                args_schema=_Delegation,
                description=(
                    f"Send the validated conflict and constraints to the {specialist.label} "
                    "specialist. The request is immutable and already targets this specialist."
                ),
            )
            def delegate(objective: str) -> str:
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
    model = create_routed_chat_model("itinerary")
    if model is None:
        raise RuntimeError("Supervisor requires a configured routed chat model.")

    collected: dict[str, AgentProposal] = {}
    tools = create_supervisor_tools(
        specialists, brief, ctx, lambda p: collected.__setitem__(p.agent, p), on_progress
    )
    supervisor = create_agent(model=model, tools=tools, system_prompt=DISPATCH_PROMPT)
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
    model = create_routed_chat_model("itinerary")
    if model is None:
        raise RuntimeError("Revision supervisor requires a configured routed chat model.")

    tools = create_revision_tools(
        specialists, requests, brief, ctx, lambda p: revised.__setitem__(p.agent, p), on_progress
    )
    revised: dict[str, AgentProposal] = {}
    if not tools:
        return proposals

    supervisor = create_agent(model=model, tools=tools, system_prompt=REVISION_PROMPT)
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
