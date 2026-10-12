# Backend Engine Notes

The Python standard-library data path lives in [`server.py`](../server.py), with
shared evidence rules in [`station_truth.py`](../station_truth.py) and community
annotations in [`community.py`](../community.py). Live `/api/chargers` responses
include diagnostics under `meta.engine`.

## Engine Contract

- Route-ready requires a non-stale, non-lead row, `decision.level == "go"`,
  affirmative `verification.live_verified == true`, no contradictory verification
  level, and at least one available connector/status. Missing or null verification
  does not grant live authority.
- Leads remain source-backed suggestions. Corroboration can raise confidence that
  a station exists or has a public price claim, but it never creates live
  verification.
- Live cache keys use a snapped bbox envelope plus route filters. The default
  viewport keeps the legacy filter-only key, but explicit/geo viewports no longer
  reuse a narrow fetch for a wider area with the same center.

[`contracts/station_truth_cases.json`](../contracts/station_truth_cases.json)
exercises these boundaries in Python, TypeScript, and Swift. When changing a truth
rule, update the shared cases and check all affected clients; a presentation label
must not independently promote a station to route-ready.

## Public-source transport

Public collection defaults to GET. The owner approved narrow read-only POST
queries for MapaREVE locations and the Feníe marker list. The shared transport
checks their exact HTTPS origin/path, body, request budget and timeout before
connecting, and never follows their POST redirects. Other non-GET requests
remain blocked. Runtime MapaREVE responses can carry live evidence; Feníe is a
cached, attributed, Verify-first snapshot. Failed sources retain warnings.

Permitted Gaia public pages can supply independent live evidence. A failed source
must retain an explicit warning; cached primary-source rows remain stale, and
source-backed leads remain non-live. Charging and parking claims retain separate
provenance. The focused transport/fallback cases are in
[`test_filter_ranking_correctness.py`](../tests/test_filter_ranking_correctness.py).

## Diagnostics

`meta.engine` exposes:

- `version`: engine contract version.
- `elapsed_ms`: total request processing time.
- `viewport`: resolved bbox, source, clamp state, and cache key.
- `filters`: include-leads, price cap, Open now, and fast filters.
- `counts`: live rows, leads, route-ready rows, lead tiers, map-eligible leads,
  and drop counters.
- `stages`: bounded timing rows for viewport resolution, MapaREVE, Gaia,
  lead loading, Electromaps, and enrichment when those stages run.
- `honesty`: machine-readable reminders for the route-ready, lead, and cache rules.

`/api/source-health` also exposes the engine version and rule summary so QA and
clients can discover the contract without issuing a live charger fetch.
