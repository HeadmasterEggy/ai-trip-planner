"""Applying a traveller's decision.

A checkpoint is only worth showing if acting on it changes something. A
decision is written into long-term memory as a confirmed preference, and the
plan is then produced again from the same brief -- so the specialists honour it
through the seam they already read, rather than the graph special-casing a
choice after the fact.

That also means a decision survives the next message: it is a stated
preference, not a one-off override.
"""

from __future__ import annotations

from .contracts import TripBrief, TripPlan, UserPreference
from .memory import memory as default_memory
from .workflow import OrchestratorOptions, run_orchestrator


def collect_decisions(plan: TripPlan) -> dict[str, str]:
    """The choices already settled on this plan, as preference key -> value."""
    return {
        checkpoint.preferenceKey: checkpoint.selected
        for checkpoint in plan.hitl
        if checkpoint.preferenceKey and checkpoint.selected and checkpoint.status == "approved"
    }


def apply_decision(
    plan: TripPlan,
    checkpoint_id: str,
    option_id: str,
    options: OrchestratorOptions | None = None,
) -> TripPlan:
    """Record one decision and re-plan the same brief with it in place."""
    checkpoint = next((c for c in plan.hitl if c.id == checkpoint_id), None)
    if checkpoint is None:
        raise KeyError(f"No checkpoint {checkpoint_id!r} on this plan.")
    if checkpoint.preferenceKey is None:
        raise ValueError(f"Checkpoint {checkpoint_id!r} carries no decision to record.")
    if option_id not in {option.id for option in checkpoint.options}:
        # Refuse an option this checkpoint never offered, rather than writing a
        # preference no specialist will match and silently changing nothing.
        raise ValueError(f"{option_id!r} is not an option on {checkpoint_id!r}.")

    options = options or OrchestratorOptions()
    mem = options.mem or default_memory
    options.mem = mem
    mem.set_long_term(
        plan.brief.userId,
        UserPreference(key=checkpoint.preferenceKey, value=option_id, source="chat_confirmed"),
    )

    decided = {**collect_decisions(plan), checkpoint.preferenceKey: option_id}
    options.decisions = decided
    return run_orchestrator(TripBrief(**plan.brief.model_dump()), options)
