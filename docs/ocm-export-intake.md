# OpenChargeMap export intake

`source_openchargemap` reads the commit-pinned [`ocm-export`](https://github.com/openchargemap/ocm-export)
bulk snapshot instead of the deprecated `ocm-data` feed (last updated 2021-08-13).

## Transport (GET only, bounded)

1. `GET api.github.com/repos/openchargemap/ocm-export/commits/main` → commit SHA + commit date (cached 24 h).
2. `GET codeload.github.com/openchargemap/ocm-export/tar.gz/<sha>` (≈45 MB; hard cap 160 MB, 600 s).
   Streamed through bounded gzip reads into `tarfile` (`r|`), never extracted.
   The decompressed byte budget includes extended headers, padding and trailing gzip data. Only `data/referencedata.json` and
   `data/ES/OCM-<id>.json` are read. Symlinks, hardlinks, devices, `..`/absolute paths, a wrong
   top-level directory, duplicate or mismatched ids, a missing reference file, a truncated
   archive, fewer than 10 000 Spain files or more than 0.5 % malformed files reject the whole
   snapshot (the adapter raises; `build_payload` files it under `errors`).
   Duplicate or malformed reference entries also reject the snapshot. Nested POI shapes are
   validated before use, and the adapter commits its rows only after the whole source succeeds.
   Unresolved publication/status/access references are quarantined; unresolved licensing is excluded.
3. `GET raw.githubusercontent.com/openchargemap/ocm-data/<pinned sha>/poi.json.gz` (≈30 MB, immutable, cached
   indefinitely) – history only. Bounded line reads and a deadline limit decompression.
   If history is unreadable or unreachable, the current export alone is used and the error is recorded.

Manifest, archive and history downloads are validated before long-lived caching.
No git protocol, no POST, no per-file requests. All requests go through `policy_urlopen`.

## Licensing

Reuse requires the row's DataProvider (from `referencedata.json`) to be open-licensed with an
explicit CC BY 4.0 or CC0 grant. Missing/null licences, "General public data", ShareAlike and
NonCommercial grants and unknown providers are excluded (fail closed). Only facts are reused:
contributor photos and comments are dropped. Each source entry carries `license`, `license_text`,
`license_url`, `license_basis`, `data_provider` and provider attribution.
Historical third-party rows require the grant embedded in that historical row; current reference
data cannot replace it. Only DataProvider 1 uses the explicit
[2022-04-01 contributor license substitution](https://community.openchargemap.org/t/announcing-our-new-simpler-data-license-for-ocm-data-cc-by-4-0-international/565),
corroborated by [OCM terms](https://openchargemap.org/about/terms). The original grant, substitution
basis and canonical license link remain in the source metadata. Operator names (including
Consum) are not a source exclusion: `source_policy.py` keys on source origin, not station brand.

## Dates

- `source_fetched_at` / `source_snapshot_at`: the snapshot's own date (export commit date, or
  2021-08-13 for retained history). This is a bulk-export date, not an observation.
- `last_seen` / `source_last_updated`: OCM's contributor `DateLastVerified` / status timestamp.
- `source_lookup_at`: the actual lookup time of the row's archive or historical artifact.
- Manifest, archive and historical artifact timestamps and cache/fallback state are recorded separately.
- Nothing here is live (`live_status_integrated` stays false).

## Canonical identity and continuity

- `runtime_refresh.py` passes its actual canonical baseline through `ingest_sources.py --prior`.
  Direct CLI builds default to the current local dataset for this context.
- The adapter generates distinct IDs using OCM POI IDs and current coordinates. After deduplication,
  merging and source policy checks, `station_identity.preserve_record_ids` alone carries canonical
  prior IDs forward. Matching requires unique stable source evidence and a distance at most 250 m.
  Ambiguous ownership and larger moves remain unresolved. Reusing an existing application ID
  also requires the distance check; a larger same-ID move rejects reconciliation. No historical per-POI IDs are reconstructed.
- If a missing ID has one uniquely identified candidate within 250 m, and that candidate already
  retains a different-source canonical owner whose identity is independently proved, reconciliation
  can retain the missing ID in `merged_ids`. The surviving ID and current facts do not change.
  Duplicate keys, competing alias claims, same-family collapses and multiple proposals for one
  owner remain blocked. Alias-only changes are written before delta/canary validation.
  The alias keeps its source-ID proof and original coordinate anchor in `identity_aliases`.
  Later refreshes revalidate that proof without copying old charger facts; unresolved aliases
  block runtime promotion rather than silently dropping a saved reference.
- A current row supersedes the same POI's historical row even when current hardware fails a route
  evidence gate. Such a row remains a reported gate loss; old prices, access or status never return.
  A quarantined malformed current file also suppresses historical fallback for its POI ID.
- Legacy-only rows remain explicitly historical, dated 2021, subject to their own licensing grant
  and the unchanged hardware and coordinate-cluster gates. Absence alone does not prove closure.
- New Not Operational / Planned / Removed or unpublished rows are omitted. A station in the actual
  prior dataset, including merged source identities and post-2021 stations, remains listed with
  current evidence and `source_reported_unavailable` when its current row passes the same gates.
- Per-run counts and artifact provenance are in `source_snapshots.openchargemap`.
- The runtime gate still requires `missing_ids == 0`. A failed real-data gate prevents promotion;
  licensing exclusions, ambiguous identity and coordinate changes do not authorize a waiver.
