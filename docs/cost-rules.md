# Cost and lodging rules

The conventions the deterministic specialists follow. They are written down because a model must
never be in a position to invent any of them, and because a plan is only comparable across rounds
if the arithmetic is stable.

Booking fixtures are fictional. Nothing here reserves anything or takes payment.

## Money

| Rule | Where |
| --- | --- |
| `estCost` is USD for the **whole trip**, not per person | `contracts.ProposalItem` |
| Totals are summed in integer cents | `budget.sum_usd` |
| Percentages are not rounded before a policy check — 10.004% is over 10% | `budget.assess_budget` |
| A negative, infinite or missing cost is treated as 0, not an error | `budget._safe_cost` |

That last rule matters: one specialist returning nonsense must degrade its own section to $0, not
take down the plan. The proposal detail still records what it proposed.

## Lodging

| Item | Convention |
| --- | --- |
| Nightly rate | `pricePerNightUsd` is per room per night; fixtures assume tax included |
| Room count | Two guests per room, so `ceil(groupSize / 2)`; `individual` allocation uses `groupSize` |
| Nights | Check-in night is charged, check-out night is not; dates must be real and at least one night apart |
| Multi-city | Cities are split on `&` in the given order, nights divided evenly with the remainder going to the earlier cities |
| Proposal cost | Only the selected stay per segment enters `estCost`; rejected candidates go in `assumptions` so nothing is counted twice |

The multi-city split is why a seven-night Tokyo & Kyoto trip costs four Tokyo nights plus three
Kyoto nights, rather than seven nights at one city's rate.

## Flights

`priceUsd` covers all passengers, and already includes both legs when a return date is given.
Multiplying by group size or by two would double-count.

## Preferences

Read from long-term memory as `UserPreference` records:

| Key | Value | Default |
| --- | --- | --- |
| `accommodation.roomAllocation` | `shared` / `individual` | `shared` |
| `accommodation.minRating` | `0`–`10` as a string (a review score, not stars) | `0` |
| `accommodation.freeCancellation` | `true` / `false` | `false` |

Confirmed preferences survive every negotiation round. Soft defaults do not.

## Selection and negotiation

1. Filter out malformed quotes and anything failing a confirmed preference.
2. First round: cheapest candidate rated 8.0+ with free cancellation. If none qualifies, the
   cheapest that satisfies the confirmed preferences.
3. A budget revision takes the cheapest eligible candidate. The soft rating and cancellation
   defaults give way; explicitly confirmed preferences do not.
4. A revision never changes group size, dates or room allocation. Where nothing cheaper exists the
   lowest viable price is kept and reported honestly, rather than inventing a discount.
5. A revision the specialist cannot act on — a time or geography constraint it has no lever for —
   keeps the original proposal and says so, rather than claiming the conflict is resolved.

## Budget thresholds

- `NEGOTIATION_OVERRUN_PCT = 0` — any real overrun enters the negotiation loop.
- `ESCALATION_OVERRUN_PCT = 10` — above 10% escalates to a human. Exactly 10% does not.
- An unresolved conflict after the final round escalates as well, even under 10%.

Both thresholds are applied at final assessment. There is no early-escalation path.

**Known limitation.** With `NEGOTIATION_OVERRUN_PCT = 0`, any overrun re-triggers a revision — but
once transport and accommodation have taken their cuts they have nothing further to give, so the
graph spends its remaining rounds re-asking and then escalates. The `Negotiation` view surfaces
this as the same conflict recurring every round. See the roadmap.
