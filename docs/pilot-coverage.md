# Paired pilot coverage inventory

`scripts/qa_pilot_coverage.py` produces an offline JSON inventory for VG-07. It never
fetches sources, imports the server, recomputes tariffs, ranks chargers, changes
admission, or declares the pilot accepted. Exit 0 means the report was written.
`runtime_acceptance` is always false. Thresholds, runtime/browser verification and
regional completeness need separate evidence.

## Evidence modes and inputs

Pass an exact source Git revision and an explicit timezone-aware `--as-of` clock.
Do not substitute the report generation time for a source observation date.

* `--mode admitted-bundle` consumes a root array, `{ "records": [...] }`, or
  `{ "stations": [...] }`. Admission is caller-declared; this tool does not validate
  a refresh receipt. All current-price and fresh-live counts are zero in this mode,
  even if a retained row contains authority flags.
* `--mode served-capture` consumes saved `/api/chargers` rows as a root array or
  `{ "stations": [...], "leads": [...] }`. `--observed-at` is mandatory. Preserve
  the full response envelope to retain warnings, refusals, cache and partial flags.
  An optional `--bundle` hashes the associated retained bundle separately. The
  association and source revision are caller-declared, not runtime attestations.
* MCP projections and arbitrary station matrices are not accepted. They lack the
  canonical price/verification fields. A capped 150-row extract is not a census.
  Known count contradictions raise an error; `total_matches` greater than captured
  rows, partial/truncated/has-more flags prevent a complete-input claim.
* Exact duplicate copies of a canonical ID count once. Differing copies of the same
  ID are rejected rather than letting file order choose an answer.

There is no report based on actual retained/served regional data committed with
this tooling. Test fixtures establish contracts only.

## Region and denominator rules

Default rectangles, inclusive on all four edges, use `south, west, north, east`:

| Region | Bbox | Meaning |
| --- | --- | --- |
| Tenerife | `[27.99, -16.95, 28.61, -16.10]` | Bounded island-area inventory, not an administrative boundary or proof of complete island coverage |
| Valencia | `[39.29, -0.62, 39.65, -0.134]` | Existing city/surroundings audit area, not the whole province |

Override both together with `--regions regions.json`:

```json
{
  "tenerife": {"label": "Reviewed Tenerife area", "bbox": [27.99, -16.95, 28.61, -16.10], "scope": "Bounded inventory only"},
  "valencia": {"label": "Valencia pilot", "bbox": [39.29, -0.62, 39.65, -0.134], "scope": "City and surroundings, not province"}
}
```

The tool does no coordinate clustering, source-identity reconciliation or alias
merging. Without a reviewed location map, `unique_locations` counts distinct
canonical station IDs and explicitly names this proxy.
`unique_physical_locations` is null. Coincident coordinates do not prove identity.

For a reviewed physical-location denominator, supply `--location-map locations.json`:

```json
{
  "basis": "Describe the retained site-identity review and its evidence",
  "location_ids": {"canonical-station-id": "reviewed-physical-location-id"}
}
```

Every canonical ID inside either region must be mapped. All IDs are strings. The
map and its review basis are caller-supplied evidence, not independently checked
identity proof. Within a reviewed group, counts use one location and conflicting
prices withhold current/live authority. Multirow groups do not sum hardware:
connector totals become unknown to avoid double counting. No second dedupe engine
is introduced.

Each regional metric includes its numerator, the same explicit location
denominator, and a fraction. Empty denominators yield null fractions. Source
breakdowns can overlap, so their sum is not a location denominator.

`source_listing_references` counts distinct location plus exact source URL pairs,
not known upstream listing IDs. Generic map links can represent many listings;
this count cannot establish an upstream census. Canonical rows, retained source
references, EVSE and connectors are separate units.

The runtime currently overloads `total_evse` with connector totals in
`server.attach_connector_summary`. Therefore `evse_total` is null;
`reported_total_evse_sum` preserves only the source field sum and is not a proven
EVSE denominator. Connector counts use published `connector_count` or
`connector_summary.total`, never the number of type labels. Unknown and conflicting
multirow hardware prevent a total while keeping a known subtotal.

## Authority and clocks

Numeric sourced prices require a real nonnegative finite `price_min`, including
explicit numeric zero, and a source reference. Missing/null prices and `is_free`
flags do not imply zero. These counts describe retained source claims, not current
price acceptance. Structured hourly/minimum/component tariffs, windows and
conditional prices have a separate, possibly overlapping count. The report does
not parse or convert them. Unparsed source text has its own `source_text_only_locations`
count and never establishes a tariff. Contact-only text stays price-unknown.
`unknown_primary_price_locations` excludes structured conditional price evidence,
not unparsed text. This can overlap with additive-only annotation locations.

Verified current price consumes the server's `current_price_min` and affirmative
`verification.price_verified == true`, with a live/official verification level,
EUR currency, a source reference and an independent fresh price clock. A dated
`charge_claim.checked_at` or `source_updated_at` takes precedence over station time.
Otherwise only non-status-overlay `last_seen` supplies the price clock. Status-only
overlays require their separate `live_claim_verification.price_verified` and a
price claim clock. They cannot upgrade an inherited price.

Fresh live evidence requires captured served rows, affirmative
`verification.live_verified == true`, `verification.level == "live"`, non-lead,
non-stale, non-conflicting evidence and a source reference. `live_status.valid`
must be true when that object exists. Publication/validation dates govern public
status overlays; other rows use `last_seen`. Source fetch time alone does not
establish a telemetry observation. No occupancy count is required to be positive:
fresh busy/unavailable telemetry is still live evidence, not route-ready proof.

Etecnic/Zunder map snapshots, snapshot integration modes and explicit
`live_status_integrated == false` are never live or verified-current-price evidence.
Their socket availability is retained source/fetch-time evidence only. Existing
structured source-policy exclusions are reused, not reimplemented. Excluded rows
remain visible in inventory and exclusion counters but cannot supply authority.

The default age bound is a conservative 180 minutes for both live and current
price evidence, matching the existing general runtime freshness boundary. Override
with `--live-max-age-seconds` and `--price-max-age-seconds` when a documented source
contract requires a stricter bound. Unknown, stale, future and conflicting evidence
fails closed. The report preserves original source, acquisition, observation,
publication and status-validation clocks and additive price annotations separately.
A fresh annotation fetch never updates its `source_updated_at` or creates primary
price authority. Retained annotation counts include old annotations; inspect their
original clocks before making freshness claims.

`map_eligible` is counted only when explicitly present as a boolean. Missing
eligibility stays unknown. Presence in an API result is reported separately from
map eligibility. This tool does not duplicate the client/server map gates.

## Reproduction

Use the same revision, region config and `--as-of` for the paired admitted and
captured reports. Paths below are placeholders for actual retained files, not
sample data. Keep outputs outside source control.

```bash
python3 -m unittest discover -s tests -p 'test_pilot_coverage.py' -v

python3 scripts/qa_pilot_coverage.py \
  --mode admitted-bundle --data /path/to/retained-bundle.json \
  --source-revision EXACT_40_CHARACTER_GIT_SHA \
  --as-of 2026-10-28T12:00:00Z \
  --out qa/pilot-coverage-admitted.json

python3 scripts/qa_pilot_coverage.py \
  --mode served-capture --data /path/to/captured-api-response.json \
  --bundle /path/to/retained-bundle.json \
  --source-revision EXACT_40_CHARACTER_GIT_SHA \
  --observed-at 2026-10-28T11:59:00Z --as-of 2026-10-28T12:00:00Z \
  --out qa/pilot-coverage-served.json
```

A single capture may cover only one region. Its other regional inventory will be
empty, not evidence of zero chargers there. Compare separately retained complete
regional responses against the same bundle and clock. Do not concatenate responses
with differing row versions or discard their envelopes to manufacture completeness.

`data_sha256` hashes exact admitted/associated-bundle bytes.
`observed_response_sha256` hashes exact capture bytes and is null for bundle mode.
`row_content_sha256` is a separate canonical content hash, not either file hash.
The report also binds its reporter, source policy, region config and optional
location map. All source dates and failures/refusals remain inspectable in the JSON.
