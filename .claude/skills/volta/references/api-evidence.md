# Bounded API evidence

Read when: probing `/api/chargers`, `/api/health` or `/api/intel` to prove coverage or a data change, whether against a local server or a deployed host.

## Request shape

- `/api/chargers` takes `bbox=south,west,north,east` (ordered). `lat`/`lon`/`radius_km` also work (viewport source `geo`). Unknown parameters such as `sw_lat` or `ne_lng` are ignored and silently fall back to the default Valencia viewport, so assert `meta.viewport.source == "explicit"` before reading counts.
- Probe each region at `max_price=5.0` and `max_price=0.25` with `include_leads=true`. A cheap-only empty result does not establish missing coverage. For Spain-wide claims include mainland, island and enclave regions. Omit `energy_price_max` from the REVE query when `max_price >= 1.0`; cheap mode keeps the 0.30 EUR headroom bound (`VOLTA_MAPAREVE_PRICE_QUERY_MAX`).
- The region buttons use the national overview (`overview=true`), capped at 2,500 rows by score. A bigger dataset can show fewer chargers per region than before, so compare region counts before and after a data change.
- Read `meta.engine` and `meta.viewport`. `lead_map_eligible: 0` or first-paint filters that exclude paid and unknown-price rows hide honest rows.

## Save, then parse

1. Write each response to its own JSON file; do not print bodies into the console or concatenate with a health response. Records repeat connector, tariff and provenance objects, so one viewport can be megabytes.
2. Print only: requested bbox, `len(stations)`, `len(leads)`, `meta.engine.counts`, source-stage results, cache state and warnings. Compare array lengths with declared counts programmatically before reporting a total.
3. Distinguish fetched rows, live-verified rows, and route-ready rows. A successful fetch can return many stations while few satisfy the usability rules. Route-ready requires a non-stale, non-lead row, `decision.level == "go"`, `verification.live_verified == true`, and at least one available connector (`docs/backend-engine.md`). Do not label every returned row available or safe to drive to.
4. Bind every receipt to the endpoint and source revision exercised. A loopback response proves the candidate, not the public host; repeat against the public endpoint after deployment.

## Honesty expectations

- Fresh REVE rows near a live station show live OCPI tariffs (for example "Cheap later" windows), not the snapshot lead. Check the bundle row by id for snapshot price and conflict fields, and the public response for what drivers see.
- OSM node, way and relation URLs may be map-eligible as Not live; they never become Go-now.
- Cached snapshot rows (for example Feníe) must not claim live availability or Go now. When activating a new source, inspect its cards for that.
- `/api/health` `ok: true` proves a response only. Disclose a source-access-policy-disabled cache separately.

## Performance comparisons

When measuring a performance change: compare the deployed base and the exact change in isolated checkouts, pin the same intelligence bytes and browser/network profile, and separate complete equal-work trials from partial or failed ones (compare returned station ids and pagination state before reporting a speedup). Observe preload reuse in the network log; matching URL strings in a unit test does not prove request reuse. Name the milestone exactly: a canvas element is not a rendered map, and a `volta:map-pins` `setData` is not painted pins. For concurrent pagination, inspect failure cancellation and nonconsecutive cursors separately from row order, and note that consumed pages may differ from HTTP requests. A reviewer's static verdict is not an executed fixture result.
