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

## Next

1. **Make budget negotiation effective.** Currently any overrun re-triggers a revision, but once
   transport and accommodation are at their floor there is nothing left to give, so the graph burns
   its remaining rounds and escalates. The `Negotiation` view now diagnoses this — the same conflict
   recurring every round — which makes it the obvious next thing to fix. Options: let a specialist
   signal "at my floor", apportion the cut by share of the overrun, or escalate early instead of
   spinning.
2. **Durable memory.** `InMemoryStore` loses everything on restart, and `promote()` is what would
   let a stated dietary preference survive into the next session — dining currently reports "no
   confirmed dietary preferences" because long-term memory is always empty.
3. **Real place and booking data.** `USE_MOCK_TOOLS=false` already switches maps to OpenStreetMap.
   Booking has no live provider; fixtures are clearly labelled as fictional and must stay that way
   until one exists.
4. **Richer trip surface.** An editable day timeline and a map view, with route, time and budget
   checks applied to edits.

## Out of scope

Multi-user editing, social features, payments and booking fulfilment.
