# Module: Live Source Adapters

Live adapters in [server.py](../../../server.py) fetch only public/user-verifiable
surfaces. They contribute live stations or promote fresh source-backed leads to
live status under strict gates.

## Responsibilities

- Fetch MapaREVE live OCPI locations within the requested viewport.
- Parse seeded Gaia public start-session pages without starting a session.
- Enforce structured source-policy exclusions before any station or lead reaches a response.
- Merge duplicate physical stations by URL, source family, and proximity while
  preserving source rows.

## Key Files

- [server.py](../../../server.py) - live adapter implementations and merge logic.
- [scripts/smoke_gaia_parser.py](../../../scripts/smoke_gaia_parser.py) - Gaia
  parser smoke.
- [tests/test_source_policy_exclusions.py](../../../tests/test_source_policy_exclusions.py) -
  retired-source runtime/cache exclusion behavior.
- [docs/source-research-2026-07-15-national.md](../../source-research-2026-07-15-national.md) -
  current source research, rights decisions, and reintroduction gates.

## Public API

- `fetch_mapareve(max_price, available_only, fast_only, bbox) -> dict`
- `fetch_gaia_live(force=False) -> dict`
- `merge_gaia_stations(stations, gaia_stations) -> list`
- `merge_nearby_source_stations(stations, incoming) -> list`
- `remove_duplicate_leads(leads, stations) -> list`
- `likely_same_station(a, b) -> bool`

## Internal Structure

MapaREVE is primary and bounded by timeout, fetch budget, max pages, price
headroom, connector type, and bbox. Gaia keeps a short cache and serves stale only
with warnings. The source tree retains historical Electromaps parsing functions and
tests for defensive compatibility, but `ELECTROMAPS_DIRECT_ENABLED` is false and the
rights policy prevents those rows from entering fresh, stale, or assembled responses.
Reactivation requires written reuse permission plus a separate adapter review.

### Request-method policy

`scripts/source_transport.py` is the single transport gate used by `server.py`
(`public_get_urlopen`) and `scripts/ingest_sources.py` (`fetch`). GET passes. Exactly two
POSTs are allowed: the MapaREVE `locations` bbox query (exact HTTPS host/path, bounded
`page`/`per_page`, read-only JSON body keys) and the Feníe marker list (exact path, empty
body). They carry no credentials, never follow redirects, and share a rolling per-process
budget. Everything else raises `SourcePolicyBlocked` before a socket opens. Feníe is an
offline ingestion source (`source_fenie`) with no runtime fetch; its rows are non-live
snapshots, and the `granted_reuse` record in `data/source_policy_exclusions.json` is the
revocation gate. Overpass uses GET.

## Dependencies

- **Uses:** `urllib`, public web/API endpoints, in-memory
  caches.
- **Used by:** `live_response()`, source canaries, smoke tests.

## Notable Patterns / Gotchas

- Gaia policy text explicitly forbids login, payment setup, or session-start
  requests.
- Feníe is homepage attribution/link-only. Its direct adapter is retired and
  structured-origin station evidence is excluded; independently sourced operator
  mentions keep their actual source provenance.
- Electromaps, Chargemap, and Consum station evidence is permission-gated/retired;
  function presence is not evidence that an adapter is active.
- Cloud/datacenter hosts can receive poorer upstream behavior than a local
  residential IP.
