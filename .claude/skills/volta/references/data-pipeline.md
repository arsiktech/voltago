# Data pipeline: merge rules, delta gate, diagnosing a rejected refresh

Read when: touching `scripts/ingest_sources.py`, `scripts/station_identity.py`, `scripts/runtime_refresh.py`, `scripts/auto_refresh.py`, source adapters, or when a refresh is rejected or a charger "disappeared". `DEPLOY.md` documents the refresh flows themselves.

## Merge and dedupe rules

- Same-family rows never merge, directly or through a third row. Union-find chaining can let one registry absorb a different operator's site in the same car park. Block a union when the two clusters already share a source family; join agreeing prices and nearest pairs first.
- OpenChargeMap free-text `Town` often holds a street number or road marker ("Km 124", "2 - planta -1"). One postcode shared by every row in an exact-coordinate group outranks Town text; Town values containing digits are not localities. Otherwise the coordinate quarantine (`reject_suspicious_openchargemap_coordinate_clusters`) drops genuine free chargers.
- Every merge stays traceable. The surviving pin records `merged_ids`, and the delta gate accepts a missing id only when exactly one pin claims it AND shares a stable source id (`station_identity.identity_keys`) with the old row. Never relax `missing_ids=0` to get a refresh through.
- `record_merged_ids` also keeps absorbed rows' stable identifiers in `merged_source_identifiers` (minus the pin's own keys), and `identity_keys` reads them back. Without that, a row merged into a pin from another source family (for example a NAP registry row into a Feníe pin) shares no key with the pin and the gate counts it missing even though `merged_ids` claims it.
- Source-reported unavailable rows stay listed with `source_reported_unavailable` (source, field, value, plain summary); `record_merged_ids` carries the flag onto a merged pin (fail closed).
- Tariff conflicts: `dedupe()` and `merge_cross_source_duplicates()` use one selection helper (highest eligible tariff on a disagreement above 0.02 EUR, alternatives kept in `charge_price_claims` / `claim_conflict`). Test conflict behavior through the real build order `merge_cross_source_duplicates(dedupe(rows))`; testing the second stage alone misses a first-stage collapse to the lowest price.
- Widening intake (paid and unknown-price rows) changes dedupe and quarantine groups. Rerun the live-bundle delta on real caches before calling a coverage change deployable.
- Generated ids that change after a source merge or geocode correction: first verify each proposed identity key identifies a station, not a homepage, listing or map index (uniqueness alone does not make a generic URL an identity key). Preserve application ids only through stable keys unique in both datasets, with the conservative distance check (250 m) and one-to-one ownership. Keep fresh facts and provenance; never duplicate an old row, restore stale facts, or weaken `missing_ids=0`. Record unresolved matches as blockers.
- Count raw adapter rows separately from merged dataset rows. An ingestion count is not the number of rendered public chargers or live available bays.

## Lidl official free-policy source (retained discovery)

Public availability does not establish reuse rights. Keep the source-specific rights hold (`docs/appstore/source-rights-inventory.md`) until the exact dataset, terms and permitted use are verified; the parsing recipe is not permission to ingest, publish or merge a held candidate. If rows are ingested: the official public store list on the Lidl Spain corporate site has one link per store with postal code, street and a maps query. Match region by postal-code prefix (`38*` Canarias, `07*` Balearics, `51*`/`52*` Melilla/Ceuta) rather than a regional shortcut in an adapter, which previously missed the Canaries. Free-minutes conditions make these official-free-policy rows (confidence 90 to 98) always paired with a verify-onsite note. Geocode through a public GET geocoder or the app's `/api/geocode` and commit one row per store into the normal gated pipeline; never hand-invent coordinates.

## The delta gate

`scripts/qa_ingestion_delta.py --baseline <bundle json> --rebuilt <candidate json> --policy-exclusions data/source_policy_exclusions.json` (the flag is `--rebuilt`, not `--candidate`) passes only with `ok: true` and `delta.missing_ids == 0`. It compares against the baseline you pass: the runtime refresh compares with the CURRENT runtime bundle (under `VOLTA_INTELLIGENCE_DB`'s directory), not Git's `data/free_intelligence.json`, so a PR whose Git data passed can still be rejected at refresh time. Never override the gate, hand-edit a bundle, or promote by hand.

## Rehearse a refresh on a candidate

Do this in a scratch directory, not the working checkout:

1. `git archive <sha> scripts station_truth.py community.py data/source_policy_exclusions.json data/seed_leads.json | tar -x -C <scratch>`; copy `data/cache/*` from a checkout that has caches.
2. `python3 scripts/ingest_sources.py --out <candidate.json>` (a full adapter pass takes minutes; run it as a background job).
3. `payload, report = runtime_refresh.preserve_record_ids(live_bundle, candidate)` (importable from `scripts/runtime_refresh.py`, implemented in `scripts/station_identity.py`); write `payload` back only when `report['preserved_ids']` is non-empty.
4. Run `qa_ingestion_delta.py` as above.
5. Rehearse the candidate AND the currently deployed commit in separate scratch dirs so a pre-existing rejection is not blamed on the candidate.

## Diagnosing a rejected refresh

1. Read the run directory's `build.stdout.log` (per-adapter counts) and `delta.stdout.log` (`missing_sample`, failures); `receipt.json` holds `identity_reconciliation`.
2. For each missing id load the baseline row and the candidate, compute `identity_keys` for both, and list candidate rows within about 150 m. A candidate row sharing a stable key (OCM id, OSM path, NAP site id) is a genuine merge; anything else is a loss until traced.
3. Before saying a row left the source, parse the raw cache (`data/cache/ocm_poi.json.gz` is JSON lines; `data/cache/dgt_nap_electrolineras.xml`) and compare the EXACT id. A substring grep matches longer ids that share the prefix and returns the wrong station.
4. Trace stage by stage in the scratch copy: run the adapter, `reject_suspicious_openchargemap_coordinate_clusters`, `dedupe`, `merge_cross_source_duplicates` and `apply_source_policy_exclusions_to_payload` separately, checking the id after each. Pickle the raw adapter rows once and reuse them.
5. Classify before choosing a fix: (a) still in the raw source but an adapter filter now drops it (read the source status field, for example Feníe `estado`, against the adapter's drop rules); (b) another pin claims it in `merged_ids` but shares no `identity_keys` with it (cross-family merge without carried identifiers); (c) genuinely gone from the source. (a) and (b) are code fixes; (a) needs the owner's keep-or-remove choice. `preserve_record_ids` overwrites the candidate in place, so audit carry-overs afterwards from each row's `identity_reconciliation` (old id, `generated_id`, identifiers): count by identifier type, max distance, and list every carry-over whose site name changed.
6. Before merging a gate or merge fix, prove it on real data: ingest with the fix in the scratch copy, apply `preserve_record_ids(live_bundle, candidate)`, run the delta against the live bundle, and require `ok=true`, `missing_ids=0`, no failures and each previously lost station present by id.

Verify-by-bbox trap: before saying a named row is missing from a city map, grep the JSON for the row's name, read its lat/lon, and only then build a bbox. A wrong envelope looks identical to a missing record.

## Proving new regression tests catch the bug

Stash only source paths (`git stash push -- scripts/`) so new tests stay in place; stashing everything removes the tests too. To avoid touching the tree, load the old module from git: `git show <base>:scripts/ingest_sources.py`, create `types.ModuleType('ingest_sources')`, register it in `sys.modules` BEFORE `exec(compile(src, ...), mod.__dict__)` (its dataclasses look the module up during exec and crash otherwise), then run the new test file and count failures plus errors.

## Porting a fix from another worktree onto current main

1. Create a fresh branch worktree from `origin/main`; never commit inside someone else's tree.
2. Copy only the new test file first and run it on unchanged main. Continue only if it fails there; if main already passes, report that and open no PR.
3. Export the fix as a patch of named files, `git apply --3way --check`, apply, then run the new tests and every `tests/test_*{ingest,merge,identity,price}*` suite.
4. Run the full suite on the branch AND on a detached `origin/main` worktree in the same environment, save both logs, and compare failure names.
5. A failure present only on the branch is not attributable until repeated on both trees: run it 5 to 6 times each with a per-run cap (`perl -e 'alarm 120; exec @ARGV' python3 -m unittest <id>`; macOS has no `timeout`) as a background job. The concurrent stress test in `tests/test_request_resilience.py` can hang on main too; hangs on both trees mean flaky, hangs only on the branch need investigation before any PR.
6. Commit without personal names, push, and open a review-only PR whose body has the before/after problem table, the change, a test table (main versus branch counts), pre-existing failures, and a "Not verified" list (other Python versions, no data rebuild so served data is unchanged until a refresh, QA receipts not regenerated, no deploy).
7. If the owner then asks to deploy a data-pipeline fix, check recent refresh history first (owner-only production notes), rehearse with the candidate and the deployed commit, and merge only after the candidate's rehearsal passes the gate with `missing_ids=0`. If the gate already rejects on the deployed commit, trace the ids, bring the owner the handling choice, fix it in the same PR, and rehearse again.
