# Module: Intelligence Ingestion

[scripts/ingest_sources.py](../../../scripts/ingest_sources.py) builds the
source-backed cheap/free intelligence database. It is not a live availability
path; it produces leads with evidence, confidence, and provenance.

## Responsibilities

- Fetch and cache public/open source data.
- Normalize price, connector, city/province, parking, access, and source fields.
- Add only free, cheap, or useful price-unknown charger leads.
- Dedupe exact and cross-source duplicates conservatively.
- Preserve conflicting or mixed source claims instead of silently flattening
  them.
- Exclude policy-blocked primary and nested provenance before output.

## Key Files

- [scripts/ingest_sources.py](../../../scripts/ingest_sources.py) - builder.
- [scripts/auto_refresh.py](../../../scripts/auto_refresh.py) - exact-candidate,
  transactional unattended orchestrator with clean-checkout/lock/Git guards.
- [scripts/refresh_intelligence.sh](../../../scripts/refresh_intelligence.sh) -
  compatibility shim to the orchestrator.
- [scripts/health_probe.py](../../../scripts/health_probe.py) - no-redirect API,
  local-DB freshness, and optional real-receipt hash probe.
- [scripts/qa_ingestion_delta.py](../../../scripts/qa_ingestion_delta.py) -
  canonical-vs-rebuilt safety gate.
- [scripts/qa_ingestion_canary.py](../../../scripts/qa_ingestion_canary.py) -
  canary and history writer.
- [scripts/source_policy.py](../../../scripts/source_policy.py) and
  [data/source_policy_exclusions.json](../../../data/source_policy_exclusions.json) -
  shared fail-closed source-origin policy.
- [data/free_intelligence.json](../../../data/free_intelligence.json) - current
  built database.
- [data/cache](../../../data/cache) - local ingestion HTTP cache.

## Public API

- `build_payload() -> dict`
- `add_record(records, ...) -> None`
- `dedupe(records) -> list`
- `merge_cross_source_duplicates(records, radius_km=0.04) -> (records, count)`
- Active source functions: `source_valencia_municipal`, `source_lidl`,
  `source_osm`, `source_openchargemap`, `source_barcelona_endolla`,
  `source_madrid_official`, `source_zaragoza_official`, and
  `source_moraleja_green` (including narrow Red MOVEM enrichment on permitted OSM rows).
- Dormant/permission-gated historical functions include `source_consum`,
  `source_electromaps_mapi`, `source_chargemap_mappy`,
  `source_electromaps_probe`, and `source_electromaps_photo_second_pass`; none are in
  `ACTIVE_SOURCE_ADAPTERS`.

## Internal Structure

The builder appends normalized records, runs exact coordinate dedupe, then runs
cross-source duplicate merging with guardrails. Same-source nearby rows are not
automatically collapsed. Different-source rows are not merged when price claims
disagree by more than EUR0.02/kWh. The chosen base row keeps higher reliability
identity while source lists, connector labels, price, parking, and claim-conflict
metadata are merged conservatively. The final payload is partitioned against the
structured source-origin policy; a rebuilt candidate containing an excluded row
fails the delta gate.

## Dependencies

- **Uses:** Python stdlib only, public web/API endpoints, `data/cache`.
- **Used by:** `data/free_intelligence.json`, overview API, live API leads,
  ingestion canaries.

## Notable Patterns / Gotchas

- The script currently still has some Valencia-era source functions; its output
  is national because `add_record()` gates against `SPAIN_BBOX`.
- Do not infer activity from a function definition. `ACTIVE_SOURCE_ADAPTERS` and the
  structured source policy are the executable source-of-truth; commercial adapters
  stay dormant until their documented written-permission gates are met.
- Treat the canonical file's own metadata as current; counts change with verified
  rebuilds and should not be duplicated in documentation.
- `auto_refresh.py --dry-run` builds and gates without promotion. Real mode requires
  a clean dedicated `main` checkout and transactionally promotes exactly four
  artifacts: the DB, canary history, completeness report, and Valencia audit.
  It verifies the isolated commit parent and exact blobs against that four-path
  allowlist. Push remains opt-in and pins/verifies the created commit rather than
  following a moving branch tip.
- Caches and source probes are useful local evidence but not shipped in the
  Docker image.
