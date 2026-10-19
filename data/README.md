# Charger data

`spain_boundary.geojson` is the unchanged simplified Spain ADM0 boundary from
[geoBoundaries](https://www.geoboundaries.org/api/current/gbOpen/ESP/ADM0/), originally
provided by Instituto Geográfico Nacional under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Attribution and the pinned revision are retained in the file. It filters only
Spanish adapters whose upstream omits country information; it does not restrict
the worldwide catalogue or establish charger evidence.

`free_intelligence.json` in this repository is a **sample**: 162 records whose sources
publish open reuse terms. It keeps tests, the Docker image and local runs working
offline. It is not the dataset served at [voltago.app](https://voltago.app).

| Source | Records | Terms |
| --- | ---: | --- |
| OpenStreetMap public tags | 94 | ODbL (OpenStreetMap contributors) |
| Madrid public charger open data | 38 | CC BY 4.0 (Ayuntamiento de Madrid) |
| Zaragoza municipal open data | 16 | Zaragoza open-data reuse conditions |
| València OpenData | 12 | Ajuntament de València open data |
| Barcelona Endolla open data | 2 | CC BY 4.0 (Barcelona open data) |

OpenStreetMap-derived rows are © OpenStreetMap contributors, available under the
[Open Database License](https://opendatacommons.org/licenses/odbl/). Each record keeps
its source URL and attribution in `sources`.

## Build the full dataset

```bash
python3 scripts/ingest_sources.py
```

This queries public sources only, applies `source_policy_exclusions.json`, and writes
`data/free_intelligence.json`. Do not commit a full build: several sources have not
granted bulk redistribution. See [DEPLOY.md](../DEPLOY.md) for the gated refresh that
production uses.

## Canarias station pages

The Canarias te recarga adapter reads the public map and each identity-checked
station page. It collects connector types, explicit socket quantities, rated
power, energy/hourly tariffs, minimum charges, conditions and source image links.
Directory facts never establish live occupancy. A missing quantity stays unknown;
parking spaces are separate from sockets. If a connector specification prints
`kWh` where a power rating belongs, the original unit and the interpretation as
`kW` remain in the source evidence and the displayed information note.

Page requests use HTTPS GET on the source origin only, four workers, a 350-page
limit, 12 seconds per request, a 180-second collection budget and a 3 MB body cap.
Cached pages retain their fetch dates; stale fallback is labeled. Mislinked pages
are held when the station name, address or explicit coordinates do not agree.
Image metadata does not grant display rights: clients keep source-page links and
automatically load only individually approved images.

To update only existing Canarias contributions in a served full catalogue:

```bash
python3 scripts/enrich_canarias.py --baseline /path/to/served/free_intelligence.json \
  --out /path/to/candidate/free_intelligence.json --report /path/to/candidate/canarias-report.json
```

This keeps canonical IDs, aliases, coordinates and unrelated records. It does not
promote data. Use the unchanged delta/canary gates and the existing
`runtime_refresh.py --candidate` adoption path from [DEPLOY.md](../DEPLOY.md).
Keep full candidates, page caches and detailed collection reports out of Git.

## Worldwide candidates

`python3 scripts/build_global_intelligence.py --all-countries --sources ocm`
adds eligible records from the current OpenChargeMap export to the existing
baseline, using disk-backed staging and bounded downloads. The default export is
pinned to a reviewed Git revision; pass `--ocm-revision` to advance it explicitly.
It writes `global_intelligence.candidate.json` and a matching manifest, never the
canonical dataset. Large generated files and caches remain local.

Additional adapters support bounded OpenStreetMap queries and official national
datasets. The [source matrix and runbook](../docs/global-coverage.md) distinguishes
populated sources, implemented adapters, credential requirements and exclusions.
Run the delta/canary gates against the exact candidate before local activation or
any separately authorized production promotion. Preserve each original source's
attribution and reuse obligations, including ODbL obligations for OSM-derived data.
