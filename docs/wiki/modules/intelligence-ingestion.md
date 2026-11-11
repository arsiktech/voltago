# Module: Intelligence Ingestion

[scripts/ingest_sources.py](../../../scripts/ingest_sources.py) builds the
source-backed cheap/free intelligence database. It is not a live availability
path; it produces leads with evidence, confidence, and provenance.

## Responsibilities

- Fetch and cache public/open source data.
- Normalize price, connector, city/province, parking, access, and source fields.
- Add every located charger lead; free first and cheap next is a ranking and
  labelling rule, not an inclusion rule (paid rows keep their published price).
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
- `data/cache` - local ingestion HTTP cache (not tracked).

## Public API

- `build_payload() -> dict`
- `add_record(records, ...) -> None`
- `dedupe(records) -> list`
- `merge_cross_source_duplicates(records, radius_km=0.04) -> (records, count)`
- Active source functions are the entries of `ACTIVE_SOURCE_ADAPTERS` in
  `scripts/ingest_sources.py`, in run order: `source_valencia_municipal`,
  `source_lidl`, `source_osm`, `source_openchargemap`, `source_barcelona_endolla`,
  `source_madrid_official`, `source_zaragoza_official`, `source_dgt_nap`,
  `source_catalonia_icaen`, `source_navarra_idena`, `source_castilla_leon`,
  `source_etecnic`, `source_zunder`, `source_fenie`, `source_moraleja_green`,
  `source_canarias_tecarga`, `source_mapareve_public` (including narrow
  Red MOVEM enrichment on permitted OSM rows), `source_electromaps_mapi`,
  `source_electromaps_photo_second_pass`, `source_chargemap_mappy`, and
  `source_consum`. The last four run under the reuse grants dated 2026-11-10 in
  `data/source_policy_exclusions.json`.
- `source_electromaps_probe` is the one historical function that is not in
  `ACTIVE_SOURCE_ADAPTERS`.

## Internal Structure

The builder appends normalized records, runs exact coordinate dedupe, then runs
cross-source duplicate merging with guardrails. Same-source nearby rows are not
automatically collapsed. Different-source rows whose price claims disagree by more
than EUR0.02/kWh merge only on a same-site identity match within the radius; the
merged row then shows the highest published price and a `claim_conflict` listing
every price. The chosen base row keeps higher reliability
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
  structured source policy are the executable source-of-truth; a commercial adapter
  runs only while its reuse grant is recorded in that policy.
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
