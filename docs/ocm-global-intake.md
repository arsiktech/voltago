# Worldwide OpenChargeMap archive admission

The worldwide builder uses the commit-pinned `openchargemap/ocm-export` archive.
It keeps the worldwide SQLite staging store and append-only station identities;
it does not adopt the separate Spain reference-migration pipeline from PR #6.

## Validation and bounds

The reader validates the complete archive before yielding selected-country POIs.
It then hydrates each selected POI from the archive's reference tables in a second
streaming pass. Reference tables and one POI are held in memory; a temporary SQLite
index detects repeated POI IDs, including duplicates across country folders.
No archive member is extracted to the filesystem.

The archive must have a single `ocm-export-<40-hex-commit>` root matching the selected
revision. Absolute paths, traversal, backslashes, links, devices, sparse members,
unexpected files under `data/`, duplicate reference files, duplicate station IDs,
filename/body ID mismatches and malformed JSON reject the source. JSON duplicate
fields and non-finite constants also reject it. Reference IDs, types, boolean flags,
licence shapes and country codes are checked; required reference tables must exist.

The limits are 160 MiB compressed, 2 GiB decompressed per pass, 600,000 members,
4 MiB per POI, 16 MiB for reference data and 600 seconds across both passes. Extended
tar headers are checked before their payload is allocated and are limited to
64 KiB. The decompressed budget includes headers, member padding, tar end markers
and concatenated gzip output. Complete tar end markers, a valid gzip trailer and
zero-only trailing tar padding are required. A country filter does not bypass
archive validation for other countries.

These bounds detect corrupt or structurally incomplete archives. They do not prove
that an upstream export contains every real station or that a provider's coverage
has not shrunk. The existing candidate delta/canary gates remain necessary.

## Reference and licence evidence

The archive's reference tables are authoritative for provider licence, country,
connector, operator, usage and status metadata. An embedded permissive licence
cannot override a restrictive provider reference. Contradictory country or ID
claims, unresolved explicit references and explicitly unpublished submissions are
quarantined and counted in the source manifest. Snapshot operational status remains
directory evidence and never becomes live availability.

The existing compatible-licence classifier is retained: explicit supported open
licences and grants still require `IsOpenDataLicensed` to be the boolean `true`.
Unknown, noncommercial and no-derivatives grants remain excluded. PR #6's narrower
CC BY 4.0/CC0-only policy is not substituted for the existing worldwide policy;
NREL's explicit grant, Open Government Licence and the other previously supported
licences continue to be evaluated by that policy. Provider attribution and licence
text remain on each accepted record, and the manifest binds the import to the
selected commit and the archive's SHA-256 digest.

## Complete-source admission

`CandidateStore.source_transaction()` protects each source with a SQLite savepoint
and a copy of its counters. The OCM importer releases the savepoint only after the
entire iterator succeeds and a second SHA-256 check confirms that the source file
has not changed during validation. A late parse, I/O, store, digest or unexpected normalization
failure rolls back the source's rows, identity index and counters. It leaves the
baseline and earlier successful sources intact.

A failed manifest reports `admission: rolled_back`, zero accepted/duplicate rows,
`rolled_back_records` and the error. `seen` and rejection reasons describe the
attempt, not published coverage. Successful admission is marked `complete`;
cache age can still make its status `stale`. Neither state promotes a candidate.
