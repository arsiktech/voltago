# Backend Engine Notes

The Python standard-library data path lives in [`server.py`](../server.py), with
shared evidence rules in [`station_truth.py`](../station_truth.py) and community
annotations in [`community.py`](../community.py). Live `/api/chargers` responses
include diagnostics under `meta.engine`.

## Product and implementation contracts

- No invented chargers, prices, availability, reviews or start methods. Live,
  official, directory, community, cached and inferred evidence remain distinct.
  Stale or unverified evidence cannot become live or route-ready. Community
  reports may annotate or downgrade, never grant live authority. Charge and
  parking claims have separate provenance; network calls and fallbacks stay bounded.
- Python backend, scripts and tests use only the standard library. iOS uses
  native frameworks only. Frontend dependencies remain limited and the list
  stays usable if maps fail. Both clients share the station-truth contract.
- Intelligence is rebuilt into a candidate and must pass delta/canary checks
  before promotion. Preserve user data and source evidence. Generated QA
  receipts stay local and ignored; generators produce evidence, never hand-edited
  results or weakened assertions. Live work and progress are tracked privately.
- `PaywallConfig.enabled` remains false until launch/pricing prerequisites are
  approved. StoreKit supplies any future prices and trial terms. Safety warnings,
  honesty labels and fail-closed behavior are never paywalled.

Worldwide directory support is a local candidate, not proof of production rollout
or live coverage. Each live integration retains its actual regional scope.
Finding a charger does not authorize provider login, charging or payment actions.

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

Published directory/community tariffs remain source claims, even if their amount
is zero. Enrichment keeps them as they are, so the apps' free and price filters
still show listed free leads as leads. Agent-facing (MCP) answers add the label:
without affirmative, non-stale charging-price verification, `price_now_eur_kwh`
is null and the label says `Listed` with the evidence tier and source year.
Status overlays cannot promote inherited price claims. Web/iOS price and
vehicle-cost views keep their existing explicit price-verification gates.

## Retired station IDs

The refresh gate (`scripts/qa_ingestion_delta.py`) requires every baseline station ID to
survive a refresh, because saved references and community history point at it. Normal
survival paths are an unchanged ID, an ID carried forward by a unique stable source code
within 250 m (`station_identity.py`), or a proven merge. When none applies and the change
is real, such as an upstream coordinate correction that changes a coordinate-derived ID,
record it in [`data/station_id_retirements.json`](../data/station_id_retirements.json).

Each entry names the retired `id`, an optional `successor_id`, `retired_on`, `reason`,
`evidence` and `approved_by`, and is reviewed in Git like any other data change. The gate
honors an entry only while it still checks out. With a `successor_id`, that ID must be in
the rebuilt data and absent from the baseline, so a retirement cannot hide a loss behind a
station the baseline already had. Without one, the entry records a genuine removal.

The gate stays strict otherwise. A malformed list, or a `--retirements` path that does not
exist, fails the candidate. Every other missing ID still counts, and `runtime_refresh.py`
still requires zero. The report lists `retired_ids`, `retirements_unmet` (entries the data
did not support) and `retirements_stale` (entries whose ID is no longer gone), so finished
entries can be removed. A retirement does not repoint saved references to the old ID. Check
what references it before adding an entry.

## Regional open-data registries

Catalonia (ICAEN, Generalitat open licence), Navarra (IDENA, CC BY 4.0) and Castilla y
León (CC BY 4.0, last updated 2022) publish charger locations, operators, connectors and
power. They are fetched once a day by anonymous GET and stored as corroboration-only
official rows. They carry no tariff and no live status, and a missing price is never read as
free. Each registry lists every charger post separately, so posts of one operator within
15 m form one station. On production data (2026-10-24) they corroborated 1,389 existing
stations, added 399 new ones, and moved the single-source share from 86.7% to 80.1%.

## Etecnic public charger map

The JSON behind Etecnic's public map is fetched by anonymous GET twice a day. Etecnic's
aviso legal reserves redistribution; the owner approved collection on 2026-10-24 under the
12 Oct source policy and accepted that risk. The grant `etecnic-owner-policy-reuse` in
[`data/source_policy_exclusions.json`](../data/source_policy_exclusions.json) is the
revocation gate. Posts within 15 m form one station, stations outside Spain are dropped,
and amperes become kW by phase count. The socket status has no source timestamp, so it is
kept as fetch-time evidence and never becomes live availability. Connector types are not
published, so connectors stay unlabelled.

## Zunder public location map

The public map page is fetched by anonymous GET once a day; only sites its legend marks
Activo are kept (light-blue "En construcción" sites are skipped), and sites outside Spain are
dropped. Zunder's terms were not reviewed; the owner approved collection on 2026-10-24 under
the 12 Oct source policy, recorded as `zunder-owner-policy-reuse`. Per-site detail adds
connector type and power. Zunder answers HTTP 429 after a handful of quick requests, so at
most 5 new details are fetched per refresh, 4 s apart, stopping at the first rate-limit
reply, and kept for 30 days. Available and in-use counts have no timestamp and stay
fetch-time evidence. Unrecognised connector types add to the port count only.

## Crawlable pages

`scripts/seo_pages.py` serves server-rendered Spanish pages for search engines from the same
station data, with no JavaScript: `/cargadores` (cities), `/cargadores/<city>` (cities with
5+ stations) and `/cargador/<name>-<hash>` (stations that two or more source families list,
or that a source prices or marks free). `/sitemap.xml` is generated from the same index. The
station URL ends with a hash of the station ID, so it stays stable across refreshes. Pages
show each price with its source and data date, never live availability, and link to the app
for live status. Owner decisions 2026-10-24.

## Public-source transport

Public collection defaults to GET. The owner approved narrow read-only POST
queries for MapaREVE locations and the Feníe marker list. The shared transport
checks their exact HTTPS origin/path, body, request budget and timeout before
connecting, and never follows their POST redirects. Other non-GET requests
remain blocked. Runtime MapaREVE responses can carry live evidence; Feníe is a
cached, attributed, Verify-first snapshot. Its redistribution basis is
owner-attested, not independently verified. Failed sources retain warnings.

A MapaREVE refusal (HTTP 403 or 429) is respected, not evaded. The server then
sends no MapaREVE request for `VOLTA_MAPAREVE_BLOCK_PAUSE` seconds (default
6 h), then lets exactly one probe through. A refused probe doubles the pause, up
to `VOLTA_MAPAREVE_BLOCK_PAUSE_MAX` (default 24 h). A timed-out probe waits one
more pause, and a successful probe clears the refusal. While paused, live
responses serve the cache with a `MapaReveRefused` warning, and
`/api/source-health` reports `mapareve_refusal`. Page requests run two at a time.

The [structured source policy](../data/source_policy_exclusions.json) controls
exclusions and evidence-kind exceptions, including Consum's excluded locator.
The narrow Repsol, Electromaps and Chargemap price annotations remain Spain-only,
anonymous GET evidence attached after existing-row dedupe/merge. They create no
new pins, primary prices or live authority; see [price evidence](price-evidence.md).
Collection permission is not a provider reuse licence. Public visibility does
not remove a source-rights hold. Authenticated scraping, login bypass, app-token
extraction, traffic interception and CAPTCHA bypass remain excluded.

[`scripts/source_transport.py`](../scripts/source_transport.py) enforces the
exact HTTPS host/path/body, timeouts, redirect prohibition and rolling budgets.
Only the two named read-only POST query families are allowed; every other
non-GET collection request raises `SourcePolicyBlocked`. The exceptions do not
permit credentials, arbitrary POSTs, provider sessions, charging or payments,
and do not lift source exclusions. New transport permissions need a separate
owner decision and reviewed guard changes.

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
