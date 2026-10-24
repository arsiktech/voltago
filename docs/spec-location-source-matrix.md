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
