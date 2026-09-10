# Roadmap

The near-term target is a reliable single-user workspace: describe a trip, see what the specialists
negotiated, act on what needs a decision, and take the plan with you.

## Done

1. Five specialists behind one `Specialist` protocol, three model-backed and two deterministic.
2. LangGraph orchestration with conflict detection, targeted revision and a round limit.
3. Supervisor delegation through typed tools, with deterministic dispatch as the fallback.
4. Conversational intake: a message becomes an explicit brief patch, in English or Chinese.
5. A UI that shows the negotiation — day-by-day timeline, budget breakdown, round-by-round record.
6. Executable HITL: a stay choice is offered, recorded as a confirmed preference and re-planned
   around, and it outranks a later budget revision.
7. Budget negotiation that asks for the actual shortfall, apportioned by share of the spend, and
   stops as soon as a revision changes nothing rather than spending the remaining rounds.
8. Brief feasibility checked once, up front (`contracts.brief_problem`): the form, chat intake and
   the orchestrator all ask it, so an unplannable brief is refused with a reason instead of failing
   inside a specialist after the other four have run.

## Next

1. **Durable memory.** `InMemoryStore` loses everything on restart, and `promote()` is what would
   let a stated dietary preference survive into the next session — dining currently reports "no
   confirmed dietary preferences" because long-term memory is always empty.
2. **Real place and booking data.** `USE_MOCK_TOOLS=false` already switches maps to OpenStreetMap.
   Booking has no live provider; fixtures are clearly labelled as fictional and must stay that way
   until one exists.
3. **Richer trip surface.** An editable day timeline and a map view, with route, time and budget
   checks applied to edits.
4. **Framework alignment.** The orchestration is the documented subagents + custom-workflow shape,
   but four things are hand-rolled: dependency injection, result flow, human-in-the-loop and retry.
   [Framework alignment](framework-alignment.md) has the strategy, the risks and the order.

## Out of scope

Multi-user editing, social features, payments and booking fulfilment.
