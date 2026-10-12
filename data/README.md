# Charger data

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

## Public-source connectors added 2026-10-12

These adapters collect public, anonymous data only (no login, user credential, app-traffic
interception or CAPTCHA bypass). None publishes an official tariff; rows stay price-unknown
unless a source says otherwise. Listing here is provenance, not a reuse licence.

- Place to Plug: anonymous GraphQL, verified 2026-10-12. One exact read-only POST
  (`api.placetoplug.com/v2/graphql`, enforced by `scripts/source_transport.py`); coordinates
  are `[lon, lat]`; status `AVAILABLE`/`UNKNOWN` only, never live availability; no price.
- Reus: municipal public GIS (no licence stated). Only the `carregadors-municipals` layer
  (13 points) is MOT; `carregadors-privats` is excluded. `endolls` is a plug count; power is unknown.
- Tarragona: Ajuntament de Tarragona open data (CKAN). The six CSV rows duplicate Etecnic
  chargers at identical coordinates, so MOT is recorded as a cross-reference on the Etecnic
  row (no second pin, no re-keyed id). `AMPERATGE` is amperes, not kW.
- PlugShare: public web-client API credential from public bundle; community data; free flags are community claims.
  The credential is the site's own public web-client value, configurable via
  `VOLTA_PLUGSHARE_BASIC_AUTH` (default equals the public value); requests are HTTP/1.1 only.
- P-APP (Interparking): one GET of the robots-allowed finder page
  (`interparking.es/es/encuentra-un-aparcamiento/`), embedded Next.js data. Only Spanish
  car parks flagged "Electric charging station" are rows; the page's `price` is a parking
  price, never a charging price. Cross-source proximity outliers against DGT rows are kept
  unmerged beyond the existing rules.

## Geographic boundary

`spain_boundary.geojson` contains the simplified Spain ADM0 boundary from
[geoBoundaries](https://www.geoboundaries.org/api/current/gbOpen/ESP/ADM0/), originally
provided by Instituto Geográfico Nacional. It is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The geometry is unchanged;
source attribution and the pinned revision are included in the file. It filters
coordinates when a source omits its country, rather than guessing land borders.
It is not charger evidence, and its simplified coastline has finite precision.

## Build the full dataset

```bash
python3 scripts/ingest_sources.py
```

This queries public sources only, applies `source_policy_exclusions.json`, and writes
`data/free_intelligence.json`. Do not commit a full build: several sources have not
granted bulk redistribution. See [DEPLOY.md](../DEPLOY.md) for the gated refresh that
production uses.
