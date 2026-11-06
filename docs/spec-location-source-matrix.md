# Spec: location × source matrix (one page, 2026-10-24)

Status: spec only. No code or new collection until the owner approves it.

## Goal

One view, for drivers and agents alike, showing which public sources list each charging
location, and what each source says about it. Rows are locations, columns are sources.
The same records feed the map. A cell shows **source coverage**. It does not show
availability, price or whether charging is free.

## Data model

| Entity | Key fields |
| --- | --- |
| `Location` | Stable `id` (reuse current station IDs and the retirement list), coordinates, address, city, `merged_ids`. |
| `Source` | `id`, display name, tier (live, official, directory, community), geographic scope, allowed evidence (full row, price annotation only, link only), licence and attribution, expected cadence. |
| `Listing` | `location_id`, `source_id`, source record ID (for example `fenie_code`, EVSE ID), match method and distance, the source's own fields, `source_url`, and three clocks: `fetched_at`, `published_at`, `source_last_updated`. |

The listings already exist as each record's `sources[]` and `merged_source_identifiers`.
The matrix is a view over them, not a second database.

## Cell states

| State | Meaning | Shown when |
| --- | --- | --- |
| Listed (green) | The source lists this location. | A listing matched under the identity rules. |
| Not listed (grey) | The source covers this area, and its latest complete snapshot omits the location. | Scope includes the point, the snapshot is complete and inside its freshness window. |
| Unknown (hatched) | Absence is not proven. | Out of scope, stale, partial or failed snapshot, or the source was refused (MapaREVE today). |
| Uncertain match (amber) | A nearby candidate exists but failed the identity checks. | Within 250 m with no stable code, or several candidates. |
| Restricted (outlined) | Rights allow only a link or a price annotation. | Consum (link only); Electromaps, Chargemap, Repsol (price only). |

Grey must never stand in for "unknown". Stale data must never read as live.

## Achievable freshness

| Source family (records today) | Freshness |
| --- | --- |
| Endolla status, Spirii AFIR status (44 / 26 sites) | About 60 s status cache. Spirii publication must be under 300 s old. |
| MapaREVE live query | 60 s cache when reachable. Refused since 2026-10-21; shown as Unknown until a probe succeeds. |
| DGT NAP registry (11,012) | Daily upstream. No live status published yet (status feed 404). |
| OpenStreetMap (1,892), OpenChargeMap (1,654), Canarias (267), Feníe (228), Madrid, Zaragoza, València, Lidl, Red MOVEM | Snapshot per gated refresh, which runs every 6 h. Upstream cadence varies. |
| Price annotations (Electromaps, Chargemap, Repsol) | Bounded per request; never create a row. |

"Real-time" is achievable only for the live-status feeds. The matrix shows each cell's
clock rather than promising real time overall.

## Matching

Reuse `station_identity.py`. A stable source code wins within 250 m, plus the existing
80 m and 120 m name and operator guards. Today 11,980 of 13,597 locations (88%) have one
source, 1,342 have two, and 275 have three or more. Better matching, not more scraping,
is the main lever for a useful matrix. Report this ratio as the matrix health metric.

## Interface and agent access

1. Search by address, geocode it, then list locations within the viewport.
2. Show the matrix with frozen location and address columns and one column per source.
3. Opening a cell shows only that source's fields, its link, licence and three clocks.
4. Agents read the same view. MCP v1 stays unchanged, per the 2026-10-24 decision. A
   read-only `get_location_sources` tool is a later MCP v2 item.

## Acceptance

- Address search finds the location, and the matrix shows coverage for it.
- Every cell carries one of the five states, and grey appears only when its conditions hold.
- A cell's contents are attributable to that source alone.
- Each source's freshness is measured in production and shown, not assumed.
- Map, matrix and agents read the same records.

## Owner decisions (2026-10-24)

1. Columns: every source family gets its own column (about 20).
2. Price-only sources (Electromaps, Chargemap, Repsol) get columns in the Restricted
   state. Each shows only that the source has a price for the location.
3. The first build is better cross-source matching, measured by the single-source share.
   The matrix interface follows.

## First build (2026-10-24)

- `GET /api/location-sources?bbox=south,west,north,east` (at most about 20 km across)
  returns the columns, the 150 locations nearest the centre, and each cell's state with
  its source entries, reason or nearby candidate. Built by `scripts/source_matrix.py`.
- `/matrix` is the web page: address search, the matrix, and a detail panel per cell.
- A column is marked stale, and cannot show "Not listed", when its adapter failed, its
  latest fetch fell back to an older cached copy, or its listing is outside its freshness
  window (see below).
- Not yet included: the runtime live feeds (MapaREVE live, Endolla and Spirii status,
  Gaia), and the MCP tool.

## Search area and clocks (2026-11-06)

- `/matrix` uses the geocoder's extent for a place, centred on the place and capped to the
  API limit. A result with no extent, or one under about 1.4 km across such as a house,
  stays at ±0.7 km and shows a radius control (0.7, 2, 5 or 10 km).
- Each listing carries `clocks`: `fetched_at`, `published_at` and `source_last_updated`. A
  clock the listing does not retain is `null`, and the page shows "Unknown". The record-level
  `source_fetched_at` is the oldest across merged sources, so a cell never uses it.
- Each column carries `last_fetch` from the build's `fetch_evidence`: the newest and oldest
  file times, the file count, and the files served from a cached copy after a failed fetch.
  It is `null` when the build has no evidence for that source.

Listing clocks retained in the production build of 2026-11-04 (15,403 records):

| Clock | Retained for |
| --- | --- |
| `fetched_at` | All listings: DGT, Madrid, Endolla, Feníe, Red MOVEM, ACCIONA, Moraleja, Repsol, Chargemap, Electromaps. Part: Canarias (255 of 270), OpenStreetMap (118 of 1,983, only cached-copy rows). None: OpenChargeMap, MapaREVE, Catalonia, Navarra, Castilla y León, Etecnic, Zunder, València, Zaragoza, Lidl. |
| `published_at` | No stored listing. Only the runtime live feeds, which the matrix does not include, publish it. |
| `source_last_updated` | All OpenChargeMap listings. Part: Zaragoza (5 of 37). Electromaps (12 of 12) through its price `source_updated_at`. None elsewhere. |

That build holds no fetch evidence for Repsol, Chargemap or Electromaps, so their `last_fetch`
is unknown. Filling the gaps is ingestion work. Nothing here estimates a missing clock.

## Freshness windows (2026-11-06)

"Not listed" requires that the source's listing was fetched inside its freshness window at
the moment of the request. The API compares against the current time, not the build time,
because a snapshot keeps ageing while it is served. Otherwise the cell is Unknown with a
reason, and the column header shows "(stale)".

Window = cache TTL + 6 h refresh + 1 h margin. A run just before the TTL ends still hits the
cache, so a healthy file reaches TTL + 6 h before the next run replaces it. The 2026-11-06
candidate build shows this: its DGT file followed a 30 h cycle. The margin covers the timer's
5-minute jitter and the run's time until it publishes.

| Column | Listing files | Cache TTL | Window |
| --- | --- | --- | --- |
| DGT registry | `dgt_nap_electrolineras.xml` | 24 h | 31 h |
| OpenStreetMap | `osm_es_area_chargers_*` (24 tiles) | 12 h | 19 h |
| OpenChargeMap | `ocm_poi.json.gz` | 7 days | 175 h |
| MapaREVE snapshot | `mapareve_public_tenerife_p*` (12 pages) | 12 h | 19 h |
| Canarias te recarga | `canarias_tecarga_map*` | 24 h | 31 h |

Only these five columns can show grey. Network and price columns are always Unknown or
Restricted when absent, so they have no window.

- **Several files: the oldest decides.** Evidence does not say which file covers which point,
  so one fresh tile cannot vouch for an old tile that may cover the location.
- **Only listing files count.** Canarias detail pages enrich a row but do not decide what the
  source lists, so their age does not affect the window.
- **Missing or unusable evidence is Unknown.** This covers a listing with no evidence, any
  listing file with an unreadable or zoneless time, and a time over 5 minutes in the future.
- **A failed fetch stays the reason shown** when both apply, because it is more specific.
- **Out of scope stays the reason shown** for a place outside the source's area.

Effect on the production build of 2026-11-04 (first 150 locations in each of 11 sampled areas,
1,513 locations), measured against 2026-11-06 14:30 UTC:

| Column | Grey before | Grey after | Why |
| --- | ---: | ---: | --- |
| DGT registry | 444 | 0 | File fetched 2026-11-03 12:39 UTC; window ended 2026-11-04 19:39 UTC. |
| MapaREVE snapshot | 71 | 0 | Oldest page fetched 2026-11-04 00:48 UTC; window ended 2026-11-04 19:48 UTC. |
| OpenChargeMap | 1,314 | 1,314 | Inside its window until 2026-11-11 07:47 UTC. |
| OpenStreetMap, Canarias te recarga | 0 | 0 | Already Unknown: that build used cached copies after failed fetches. |
| **Total** | **1,829** | **1,314** | **515 cells change to Unknown.** |

The same build at publication changes 0 cells. If refreshes stay rejected, all 1,829 become
Unknown after 2026-11-11 07:47 UTC. That is the intended result: the page says it cannot prove
absence, and the cause is the refresh gate, not the matrix.
