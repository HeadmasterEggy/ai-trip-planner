# Roadmap

The near-term target is a reliable single-user workspace: describe a trip, see what the specialists
negotiated, act on what needs a decision, and take the plan with you.

## Done

1. Five specialists behind one `Specialist` protocol, three model-backed and two deterministic.
2. LangGraph orchestration with conflict detection, targeted revision and a round limit.
3. Supervisor delegation through typed tools, with deterministic dispatch as the fallback.
4. Conversational intake: a message becomes an explicit patch of a partial trip (`BriefPatch`), in
   English or Chinese. A turn plans only once nothing required is missing; otherwise it asks for the
   next field, so an empty session holds no trip at all and the first screen is a greeting.
5. A UI that shows the negotiation — day-by-day timeline, budget breakdown, round-by-round record.
6. Executable HITL: a stay choice is offered, recorded as a confirmed preference and re-planned
   around, and it outranks a later budget revision.
7. Budget negotiation that asks for the actual shortfall, apportioned by share of the spend, and
   stops as soon as a revision changes nothing rather than spending the remaining rounds.
8. Brief feasibility checked once, up front (`contracts.brief_problem` and `draft_problem`): the
   form, chat intake and the orchestrator all ask it, so an unplannable brief is refused with a
   reason instead of failing inside a specialist after the other four have run.
9. Framework alignment: the orchestration now matches the documented patterns — role-based model
   routing, per-run injection with one agent and one graph per process, worker results in graph
   state, official resilience middleware, progress as a stream, and a checkpointer that pauses where
   the plan escalates. [Framework alignment](framework-alignment.md) records each step and the three
   assumptions it corrected.
10. A run says what each section cost: which route would answer it and how long it took, in the
   reasoning view and in the exported plan.
11. An opening screen that assumes nothing: no prefilled demo trip, no example chips, and a
    conversation that collects the trip one answer at a time and asks for whatever is missing.
12. A dark navigation rail: brand, search, `New chat`, the trip's panels behind it, and this
    session's trips and chats as clickable rows. Every row does something — there is no Explore or
    Saved, because there is nothing behind them.

## Next

1. **Durable memory.** `InMemoryStore` loses everything on restart, and `promote()` is what would
   let a stated dietary preference survive into the next session — dining currently reports "no
   confirmed dietary preferences" because long-term memory is always empty. The rail's conversation
   list is the other half of this: it is session state, so a refresh loses the history, and a
   durable store is what it would eventually read from.
2. **Real place and booking data.** `USE_MOCK_TOOLS=false` already switches maps to OpenStreetMap.
   Booking has no live provider; fixtures are clearly labelled as fictional and must stay that way
   until one exists.
3. **Richer trip surface.** An editable day timeline and a map view, with route, time and budget
   checks applied to edits.
## Out of scope

Multi-user editing, social features, payments and booking fulfilment.
