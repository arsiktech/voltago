# Module: Data Honesty Engine

The data-honesty engine is a set of functions in [server.py](../../../server.py)
that decide whether a row can be shown, what it claims, and whether a driver
should route to it now.

## Responsibilities

- Reject nonexistent, removed, dead-link, unavailable, or out-of-order rows.
- Keep charge and parking evidence separate.
- Classify live/official/directory/community confidence.
- Decide `go`, `check`, `verify`, `lead`, or `avoid`.
- Handle time-of-use tariffs without turning "cheap later" into "go now".
- Apply independent-source corroboration without changing liveness.

## Key Files

- [server.py](../../../server.py) - `reject_station_reason()`,
  `ensure_claim_contract()`, `verification_profile()`, `decision_for()`,
  `recommendation_score()`, pricing-window helpers, parking helpers, and
  corroboration helpers.
- [frontend/src/lib/format.ts](../../../frontend/src/lib/format.ts) - client-side
  mirror for decision display, source tier, filters, availability, tariff chips,
  and fail-closed data choice.
- [tests/test_corroboration.py](../../../tests/test_corroboration.py) - verifies
  corroboration never flips liveness.
- [tests/test_pricing_windows.py](../../../tests/test_pricing_windows.py) -
  verifies time-window and source-contract behavior.
- [tests/test_parking_classification.py](../../../tests/test_parking_classification.py) -
  parking classification coverage.

## Public API

- `reject_station_reason(item) -> str | None`
- `normalize_parking_profile(item) -> dict`
- `primary_source_for(item) -> dict | None`
- `ensure_claim_contract(item) -> None`
- `verification_profile(item, live: bool) -> dict`
- `decision_for(item, live: bool) -> dict`
- `attach_pricing_summary(item, price_cap=0.25) -> dict`
- `apply_corroboration(item) -> dict`
- `recommendation_score(item) -> float`

## Internal Structure

Rows are enriched in layers. First, unsafe rows are rejected. Then source URLs
are normalized and ranked. Claim contracts are attached or repaired. Pricing
windows are summarized before decision-making so a station can remain visible as
`Cheap later` without being route-ready. The decision engine treats availability,
Tesla connector compatibility, price cap, stale timestamps, access restrictions,
and parking risk as separate gates.

## Dependencies

- **Uses:** station dictionaries from live adapters and intelligence leads.
- **Used by:** `live_response()`, `overview_response()`, React display helpers,
  iOS presentation helpers, Python unit tests, Vitest tests.

## Notable Patterns / Gotchas

- `verification.live_verified` is based on actual live source class, not source
  count.
- `claim_conflict.level = mixed_sources` is intentional when charge price and
  parking condition come from different public sources.
- Parking risk can downgrade a live cheap station from `Go now` to `Check
  parking`.
- Time-of-use pricing can keep a station on the map even when it is not cheap
  right now.
