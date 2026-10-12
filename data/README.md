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

## Build the full dataset

```bash
python3 scripts/ingest_sources.py
```

This queries public sources only, applies `source_policy_exclusions.json`, and writes
`data/free_intelligence.json`. Do not commit a full build: several sources have not
granted bulk redistribution. See [DEPLOY.md](../DEPLOY.md) for the gated refresh that
production uses.
