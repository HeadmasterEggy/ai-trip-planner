# Class model

The static structure, as Mermaid class diagrams sharing one namespace. Mermaid rather than exported
images: GitHub renders it inline, and a diagram that drifts from the code is worse than none.

Adapted from the TypeScript implementation's design model. The shapes are the same; the names
follow the Python modules.

## Notation

| Line | Meaning | Reads as |
|---|---|---|
| dashed, hollow triangle | realisation | class implements a protocol |
| solid, filled diamond | composition | whole ◆ part; the part cannot outlive the whole |
| solid, hollow diamond | aggregation | whole ◇ part, shared; the part has its own lifetime |
| solid, open arrow | association | source holds a stored reference |
| dashed, open arrow | dependency | transient use — parameter, return or local; no stored field |

---

## Diagram 1 — Structural spine

The load-bearing pieces: the entry point, the chat layer, the orchestrator, the supervisor and the
two protocols injected into every specialist.

```mermaid
classDiagram
  direction TB
  class StreamlitApp {
    +render_plan(plan) None
    +plan_trip(message, brief) None
  }
  class TripChat {
    +run_trip_chat(request, options) ChatResponse
    -_extract_patch(message, current) BriefPatch
  }
  class OrchestratorGraph {
    -max_rounds: int
    +run_orchestrator(brief, options) TripPlan
    +detect_conflicts(proposals, brief) RevisionRequest[]
  }
  class Supervisor {
    +dispatch_with_supervisor(...) AgentProposal[]
    +revise_with_supervisor(...) AgentProposal[]
  }
  class Specialist {
    <<protocol>>
    +name: str
    +label: str
    +invoke(brief, ctx, revision) AgentProposal
  }
  class ToolGateway {
    +maps: MapsPort
    +booking: BookingPort
  }
  class MemoryStore {
    <<protocol>>
    +get_long_term(user_id) UserPreference[]
    +append_short_term(trip_id, turn) None
  }

  StreamlitApp --> TripChat : one turn
  TripChat --> OrchestratorGraph : re-plans
  OrchestratorGraph --> Supervisor : delegates dispatch
  OrchestratorGraph o-- Specialist : 1..*
  Supervisor ..> Specialist : invokes via typed tools
  OrchestratorGraph --> ToolGateway
  OrchestratorGraph --> MemoryStore
```

`Supervisor` chooses *which* specialists run. It never receives the brief as something it can
rewrite: the tools capture it when they are built.

---

## Diagram 2 — Domain model

The values carried between the UI, the orchestrator and the specialists. Every field is either data
a specialist plans against or something the UI renders.

```mermaid
classDiagram
  direction TB
  class TripBrief {
    +tripId: str
    +userId: str
    +destination: str
    +dates: tuple~str,str~
    +groupSize: int
    +budgetTotal: float
    +nationality: str
  }
  class ProposalItem {
    +kind: str
    +detail: str
    +estCost: float
    +day: int
    +startTime: str
    +endTime: str
    +location: str
  }
  class AgentProposal {
    +agent: AgentName
    +summary: str
    +assumptions: str[]
    +conflictsWith: str[]
  }
  class RevisionRequest {
    +tripId: str
    +targetAgent: AgentName
    +reason: str
    +constraints: str[]
  }
  class NegotiationRound {
    +round: int
    +revised: str[]
  }
  class TripSection {
    +id: AgentName
    +label: str
    +summary: str
    +status: SectionStatus
    +estCost: float
  }
  class HitlCheckpoint {
    +id: str
    +type: HitlType
    +title: str
    +detail: str
    +status: CheckpointStatus
  }
  class TripPlan {
    +round: int
    +budgetTotal: float
    +estTotal: float
    +overrunPct: float
  }
  class AgentName {
    <<enumeration>>
    itinerary
    transport
    accommodation
    destination-guide
    dining
  }
  class SectionStatus {
    <<enumeration>>
    planning
    draft
    needs_you
    confirmed
  }

  TripPlan *-- TripBrief : snapshot
  TripPlan *-- "1..*" TripSection
  TripPlan *-- "0..*" HitlCheckpoint
  TripPlan *-- "0..*" NegotiationRound
  NegotiationRound *-- "0..*" RevisionRequest
  TripSection o-- "0..1" AgentProposal
  AgentProposal *-- "1..*" ProposalItem
  AgentProposal --> AgentName
  TripSection --> SectionStatus
```

`TripPlan` composes a copy of the `TripBrief` rather than pointing at a live one: a plan answers one
specific brief, and a later edit must not silently invalidate it.

`ProposalItem` carries `day`, `startTime`, `endTime` and `location` as structured fields precisely
so conflict detection never has to parse a human-readable `detail`.

---

## Diagram 3 — Specialists and orchestration

```mermaid
classDiagram
  direction TB
  class Specialist {
    <<protocol>>
    +invoke(brief, ctx, revision) AgentProposal
  }
  class FunctionSpecialist {
    +name: str
    +label: str
    +fn: Callable
  }
  class ItinerarySpecialist {
    +travel_conflicts(draft, ctx) str[]
  }
  class TransportSpecialist
  class AccommodationSpecialist {
    +split_stay(brief) StaySegment[]
    +stay_cost(option, nights, rooms) float
  }
  class DestinationGuideSpecialist
  class DiningSpecialist
  class OrchestratorGraph {
    +detect_conflicts(proposals, brief) RevisionRequest[]
  }
  class Budget {
    +cost_of(proposal) float
    +assess_budget(amounts, total) tuple
    +NEGOTIATION_OVERRUN_PCT: float
    +ESCALATION_OVERRUN_PCT: float
  }
  class StructuredInvoker {
    +create_structured_invoker(task, schema, name) Callable
  }

  Specialist <|.. FunctionSpecialist
  FunctionSpecialist <|-- ItinerarySpecialist
  FunctionSpecialist <|-- TransportSpecialist
  FunctionSpecialist <|-- AccommodationSpecialist
  FunctionSpecialist <|-- DestinationGuideSpecialist
  FunctionSpecialist <|-- DiningSpecialist
  OrchestratorGraph *-- Budget
  ItinerarySpecialist ..> StructuredInvoker
  DestinationGuideSpecialist ..> StructuredInvoker
  DiningSpecialist ..> StructuredInvoker
```

Transport and accommodation have no line to `StructuredInvoker`: they are deterministic calculators,
so no model is ever in a position to price a stay or a fare.

---

## Diagram 4 — Ports and adapters

```mermaid
classDiagram
  direction TB
  class MapsPort {
    <<protocol>>
    +route(frm, to, date) RouteLeg[]
    +places(near, category) Place[]
  }
  class BookingPort {
    <<protocol>>
    +search_stays(city, check_in, check_out, guests) StayOption[]
    +search_flights(frm, to, depart, ret, passengers) FlightOption[]
  }
  class MemoryStore {
    <<protocol>>
    +get_short_term(trip_id) ChatTurn[]
    +append_short_term(trip_id, turn) None
    +get_long_term(user_id) UserPreference[]
    +set_long_term(user_id, pref) None
    +promote(trip_id, user_id, key) None
  }
  class MapsAdapter {
    -_mock_enabled() bool
  }
  class MockBooking
  class InMemoryStore
  class ToolGateway {
    +maps
    +booking
  }
  class AgentContext {
    +tripId: str
    +round: int
    +tools: ToolGateway
    +mem: MemoryStore
  }

  MapsPort <|.. MapsAdapter
  BookingPort <|.. MockBooking
  MemoryStore <|.. InMemoryStore
  ToolGateway o-- MapsPort
  ToolGateway o-- BookingPort
  AgentContext --> ToolGateway
  AgentContext --> MemoryStore
```

`AgentContext` is a specialist's only route out. Nothing imports a concrete adapter, which is why a
test passes a fake and why `USE_MOCK_TOOLS` is a switch rather than a code change.

---

## Key associations

Read *source → target*.

| Source | Target | Type | Mult. | Meaning |
|---|---|---|---|---|
| `StreamlitApp` | `TripChat` | dependency | → 1 | one turn per interaction; no stored planner |
| `TripChat` | `OrchestratorGraph` | association | 1 → 1 | every message re-plans |
| `OrchestratorGraph` | `Specialist` | aggregation | 1 → 1..* | module-level singletons; the graph does not own their lifetime |
| `OrchestratorGraph` | `Budget` | composition | 1 → 1 | private collaborator with no independent identity |
| `OrchestratorGraph` | `ToolGateway` / `MemoryStore` | association | 1 → 1 | injected into every `AgentContext` |
| `Supervisor` | `Specialist` | dependency | → 1..* | reached through typed tools, never stored |
| `TripPlan` | `TripBrief` | composition | 1 → 1 | embeds an immutable snapshot |
| `TripPlan` | `TripSection` | composition | 1 → 1..* | one per specialist |
| `TripPlan` | `NegotiationRound` | composition | 1 → 0..* | why the plan looks the way it does |
| `TripSection` | `AgentProposal` | aggregation | 1 → 0..1 | kept for drill-down |
| `AgentProposal` | `ProposalItem` | composition | 1 → 1..* | a proposal is its line items |
| `ToolGateway` | `MapsPort` / `BookingPort` | aggregation | 1 → 1 | one port of each kind |

## Protocols and implementations

| Protocol | Implemented by | Note |
|---|---|---|
| `Specialist` | the five `FunctionSpecialist` instances | one `invoke` serves both planning and revision |
| `MapsPort` | `MapsAdapter` | fixtures by default, OpenStreetMap when `USE_MOCK_TOOLS=false` |
| `BookingPort` | `MockBooking` | no live provider; fixtures are labelled fictional |
| `MemoryStore` | `InMemoryStore` | process-local for now; a durable store keeps the same signature |

## Design rationale

- **`Specialist` is one protocol with one method.** The orchestrator iterates specialists and calls
  `invoke` without knowing whether a model is involved. That is what lets transport and
  accommodation stay deterministic while the other three reason.
- **Revision reuses the same entry point.** A separate `revise()` would let the two paths drift;
  `revision` is just an optional argument.
- **Budget is composed, not injected.** Thresholds are policy, not configuration, and splitting the
  arithmetic out keeps cost decisions in one place.
- **Ports sit with the contracts, not the adapters.** The core names an interface; only `tools/`
  names an HTTP client, so an external API can change without touching a specialist.
- **`NegotiationRound` is part of the plan.** Round state would otherwise be discarded, and it is
  the only answer to "why is this section marked needs-you".
- **The supervisor holds no trip state.** Its tools close over the brief, so its freedom is
  delegation only — the deterministic rules downstream validate the request, not a paraphrase.
