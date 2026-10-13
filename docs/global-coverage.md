# Worldwide coverage and source integration

This describes the worldwide implementation prepared on 2026-10-13. It is not a
production-deployment or App Store release receipt. Exact counts belong to each
generated candidate's manifest and `/api/source-health` response.

## Source matrix

| Source | Scope | Integration | Current limitation |
| --- | --- | --- | --- |
| OpenChargeMap current `ocm-export` | All country folders in the selected revision | Streaming two-pass tar reader, reference hydration, provider-specific licence gate, candidate union | Directory snapshot; only providers with compatible explicit reuse terms; no live claim |
| OpenStreetMap / Overpass | ISO countries or bounded geographic tiles | Public GET, cached/resumable country batches, connector tags | Request/response limits; deferred and failed countries stay explicit; ODbL obligations |
| France official IRVE consolidation | France and identified overseas territories | CSV schema normalization, stable charging-point identities | Implemented and fixture-tested; direct full feed not fetched in the current restricted environment |
| Germany Bundesnetzagentur | Germany | Official download-page discovery, CSV preamble/schema handling | Implemented and fixture-tested; direct full feed not fetched in the current restricted environment |
| Hong Kong CLP | CLP's published Hong Kong locations | Public JSON schema adapter | Implemented and fixture-tested; direct feed not fetched in the current restricted environment |
| AFDC / NLR | United States and Canada | Documented official GET API, header-only key, no redirects, USD/CAD and connector normalization | Implemented and fixture-tested; `NLR_API_KEY` or `NREL_API_KEY` required; no credential configured for this check |
| Existing Spain sources | Spain, source-specific regions | Valencia, Lidl, OSM, OCM, Endolla, Madrid, Zaragoza, DGT, Feníe, Moraleja, Canarias, MapaREVE Tenerife | Existing evidence preserved; source-specific prices and freshness still vary |
| MapaREVE public map | Its Spain coverage envelope | Existing bounded read-only map query | Regional live evidence only; snapshot directory records cannot inherit its authority |
| Gaia Charge | Configured Alboraia location and its connectors | Existing public station-page integration | A seed integration, not the whole Gaia network |
| REVE external tariff export | Official connector tariffs in Spain | Existing export hardened with durable request quota, strict tariffs and resumable state | Official API key required; exact EVSE/connector identities are necessary for a safe match |
| Provider links | The linked operator's site/app | Handoff URLs | A link is not an integrated tariff, session or live-status feed |
| EVRoam (New Zealand), NOBIL direct, additional operator OCPI feeds | Provider-specific | Research/connection candidates | Registration, credentials, agreement or a verified current contract still required |
| Switzerland ich-tanke-so | Switzerland | Research candidate | Commercial reuse requires the relevant permission; do not treat a future OCPI feed as current |
| Electromaps, Chargemap, Consum | Provider-specific | Excluded by existing source policy | Technical access does not establish reuse rights |

Some national/operator data also arrives through OpenChargeMap. That is attributed
OCM snapshot coverage, not proof that the direct provider API is connected. There
is no claim that every station or every country is covered.

## Build and serve a candidate

Run from the repository root with the documented Python toolchain:

```bash
python3 scripts/build_global_intelligence.py --all-countries --sources ocm
python3 scripts/qa_ingestion_delta.py \
  --baseline data/free_intelligence.json \
  --rebuilt data/global_intelligence.candidate.json \
  --max-missing-id-ratio 0
python3 scripts/qa_ingestion_canary.py \
  --baseline data/free_intelligence.json \
  --existing-candidate data/global_intelligence.candidate.json
npm --prefix frontend run build
VOLTA_INTELLIGENCE_DB=data/global_intelligence.candidate.json PORT=8773 python3 server.py
```

Use `--baseline` on the builder to preserve a different existing full dataset;
use the same baseline for both gates. The committed sample is deliberately small.
The environment override serves a local candidate without replacing canonical
data. Production data, deployment and native distribution remain distinct actions.

For eligible national sources on a network that permits their official endpoints:

```bash
python3 scripts/build_global_intelligence.py --countries FR,DE,HK \
  --sources france_irve,germany_bnetza,hk_clp \
  --baseline data/global_intelligence.candidate.json \
  --out data/national_intelligence.candidate.json
```

For OSM, keep a bounded batch and inspect deferred scopes in the manifest:

```bash
python3 scripts/build_global_intelligence.py --countries FR,DE,GB,US,CA,JP,AU,NZ \
  --sources osm --osm-max-requests 8 \
  --baseline data/global_intelligence.candidate.json \
  --out data/osm_intelligence.candidate.json
```

`--osm-offset` resumes a larger country list; preserve the previous candidate as
the next baseline, and reuse the cache. Large-country queries can require smaller
`--bbox south,west,north,east` tiles. Split tiles at the antimeridian. An Overpass
partial-response warning is a failed query, not successful coverage. `--offline`
retains cached evidence age and cannot turn an old snapshot into fresh data.

For AFDC, provision the documented key in the environment using the deployment's
secret mechanism, then select `--countries US,CA --sources afdc` with a candidate
output and the intended baseline. Never put the key in a command argument, source
file or URL. Missing credentials produce an explicit `needs_credentials` manifest;
they cannot silently count as a connected source. See [official API documentation](https://developer.nlr.gov/docs/transportation/alt-fuel-stations-v1/all/).

The REVE external exporter reports its configuration without making a request:

```bash
python3 scripts/reve_external.py --status
```

After provisioning the official `VOLTA_REVE_API_KEY`, `--sync` advances the
bounded tariff mirror. `--join-input data/global_intelligence.candidate.json
--join-output data/reve_intelligence.candidate.json` attaches fresh evidence only
to an exact, unambiguous EVSE and connector identity. The inspected existing full
baseline had 22,474 connector objects but no such explicit identity pairs, so its
join correctly matched zero. Synthetic connector IDs and proximity cannot solve
that gap. Joining evidence does not grant live status or unconditional free charging.

The runtime refresher now detects worldwide datasets and uses the global builder.
An explicit Spain-only refresh against a global baseline fails before collection.
It can also gate an already-built candidate with `--candidate`; see
[the deployment workflow](../DEPLOY.md#worldwide-runtime-bundles) for publication.
The global collector currently preserves existing records and adds new evidence
identities. Reusing a pinned OCM revision does not refresh old source observations.

## Indexed serving and coverage page

The full JSON candidate is approximately 1.3 GB. Keep it as the source artifact
for baseline preservation and refresh gates, and serve its derived SQLite index:

```bash
python3 scripts/build_intelligence_index.py \
  --source data/global_intelligence.candidate.json \
  --output data/global-intelligence.sqlite --health-summary
VOLTA_INTELLIGENCE_DB="$PWD/data/global-intelligence.sqlite" python3 server.py
```

The output must be a new path: published indexes are immutable. The index stores
lossless compressed records, coordinate/filter indexes and precomputed evidence
summaries. Policy, seed and classification-code fingerprints must match the server;
a stale index fails closed with a rebuild instruction. The refresh workflow publishes
JSON, SQLite and gate reports together through one atomic bundle switch.

For 268,626 records, the index is about 587 MiB on disk. A local fresh-process
measurement opened the catalogue in 10.62 ms and returned source health in 11.25 ms,
with 22.7 MiB peak RSS. This is metadata/summary startup, not a concurrent query
load test. The JSON fallback remains supported, but its earlier full-catalogue
measurement used 2,344.7 MiB and 224.7 seconds to load; use indexed serving for the
worldwide release and measure actual query load on the destination host.

The website's `/coverage/` page reads `/api/sources` from the same running service.
It shows raw dataset counts, manifest country counts, deduplicated source inventory,
collector states and live-source scope separately. An implemented adapter with no
collection receipt is shown as not run. The page also explains connector support,
vehicle-profile limitations and which operator integrations still need an API.

## What the application supports

| Area | Behavior |
| --- | --- |
| Geography | Worldwide finite coordinates, city search, poles and wrapped antimeridian viewports |
| API size | Bounded map overview and paginated catalogue; truncated results are disclosed |
| Regional live status | MapaREVE/Gaia queried only within their configured geographic coverage |
| Connectors | CCS1 and CCS2 kept distinct; Type 1/J1772, Type 2, NACS/J3400, CHAdeMO, GB/T AC/DC and source labels retained |
| Vehicles | Connector evidence supports discovery; no make/model/year/adapter compatibility certification |
| Prices | Native currency/raw tariff retained; only suitable structured EUR/kWh enters the existing euro comparison |
| Unknown evidence | Unknown price is not free, a snapshot is not live, parking remains a separate claim |
| Clients | Web/PWA and native iOS; Python standard-library backend; no native Android client introduced |
| Operations | Candidate build, source manifests, delta/canary gates, runtime source-health, existing Docker/refresh infrastructure |

The legacy price filter still expresses a euro cap; this implementation does not
invent foreign-exchange conversion, membership prices or unconditional free
charging from ambiguous text. Model-specific vehicle profiles and new live
operator agreements remain separate work.

## Evidence and data handling

The generated manifest records selected scopes, counts, failures, skipped work,
snapshot identity and source evidence. The union preserves baseline records and
uses stable source identity for conservative deduplication; nearby stations from
different sources are not silently merged. Totals are source records, not a
verified count of unique physical stations or individual charging sockets.

OpenChargeMap's own licence flag is insufficient: explicit incompatible or
unspecified provider terms are excluded. Source exclusions remain active. Raw
tariffs and attribution remain attached to records. Do not commit bulk datasets,
private operational state, credentials or generated receipts.

Run `./scripts/verify.sh --full`, the map browser contract and real-candidate API
checks for an integrated change. Native builds require the native toolchain;
Python source-contract checks cannot substitute for an Xcode build or device test.
