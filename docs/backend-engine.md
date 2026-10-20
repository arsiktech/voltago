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

## Endolla and Spirii status overlay

The design checkpoint chose a shared runtime overlay in
[`scripts/public_live_status.py`](../scripts/public_live_status.py). Offline-only
collection leaves status stale. A separate endpoint or ranking would diverge from
`live_response`, HTTP discovery and the read-only MCP tools. The overlay uses the
existing connector summaries, verification, decisions, route filters and ranking.
Source caches never share mutable state. Both clients consume the standard live
rows and attributed source disclosure. Local verification does not activate a release.

Endolla joins **location ID + station ID + port ID**. Ports without a static
location remain off-map. Existing bundle rows attach by `raw.endolla_location_id`
without changing their IDs, coordinates, price, access or parking claims. Existing
conditional Endolla prices retain their original port subset. New static sites
use `live_endolla_<location-id>`, following the existing source-prefixed live-ID
convention; names and coordinates do not affect that ID. Spirii sites use
`live_spirii_<site-idG>` and retain full point `idG` and external EVSE identifiers.
The static address must explicitly have `countryCode=ES`.
The observed Spirii table contained 32 Spanish sites and 102 charging points.
Six static sites had no charging points, so only 26 sites contributed live rows.

A shared dataset URL does not establish site identity. An independent match needs
an external EVSE ID or corroborated operator, coordinates and the existing name
check. Coordinates must remain within the existing 120-metre identity guard;
operator/name matches use the stricter 80-metre guard. Multiple candidates or
conflicting coordinates hold the overlay. Fresh conflicting status becomes
unknown. A failed feed cannot overwrite independent fresh status. Attachment
retains separate charging-price and parking verification from the original
source. Live status never upgrades a directory price claim. Replaced hardware
recomputes connector compatibility, power and speed before filtering or routing.

Each source has one refresh in flight, a 60-second status cache, a 24-hour static
cache, bounded waits, and exponential failure backoff from 60 to 900 seconds.
`force` cannot bypass these limits. Transport permits only the four exact anonymous
GET URLs in `scripts/source_transport.py`, with no redirects, credentials or other
provider actions. Each endpoint has a 24-request/10-minute process budget, a
5-second socket timeout and 8 MB compressed/expanded byte bounds. Nothing fetches
at import or during source-health inspection.

Status validity requires successful validation within 180 seconds, usable static
metadata, and no refresh failure. Spirii additionally requires publication within
300 seconds. Old point `lastUpdated` / Endolla `last_updated` values represent
last changes, not heartbeats. They remain in connector `last_changed_at` and
`source_last_updated`; fetch and publication timestamps remain separate. Shared
freshness scoring and MCP use publication/validation evidence for these feeds,
without rewriting the transition time in `last_seen`.

Spirii uses different DATEX II v3 wrappers for table and status publications.
Gzip decoding is bounded. `If-Modified-Since` uses the actual returned
`Last-Modified`; a 304 can revalidate an existing entity but preserves its fetch,
publication and transition times. A missing cache cannot accept a 304. An expired
publication remains stale after a 304. Endolla supplies no explicit publication
time, so that field remains null. Its successful response validation, HTTP
Date/Age checks when present and bounded local cache establish snapshot freshness;
no publication SLA is inferred.

Failures preserve previous location evidence but set connector status to unknown
and revoke live authority. Feed-bearing assembled responses are rebuilt from the
current independent caches on every request; an older assembled snapshot cannot
bypass freshness or identity checks. Outages degrade `/api/chargers` without
breaking the response.

Attribution remains in standard `sources[]`, including licence URL and transformation.
Endolla requires Ajuntament de Barcelona / Endolla attribution and CC BY 4.0.
Spirii retains Spirii and each actual operator; the recorded owner approval is not
represented as a named open-data licence. Revoking either structured grant in
`data/source_policy_exclusions.json` disables its runtime collection. Spirii tariff
subtrees are discarded before caching and never interpreted. Endolla
`payment_required` cannot create a price or free flag. The overlay imports no new
charging or parking price claim.

`/api/source-health` and `/api/source-strategy` expose feed cache health, matched
and unlocated counts, attribution, licences and scope. Offline reduced-payload
fixtures and integration checks live in
[`test_public_live_status.py`](../tests/test_public_live_status.py). Legacy suites
explicitly disable the new fetcher; the new suite exercises the real parsers,
cache and shared integration with fixture transport.
