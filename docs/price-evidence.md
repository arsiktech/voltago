# Additive price evidence

The 2026-10-20 owner decision permits three bounded, anonymous public GET sources
for **Spain only**: Repsol's published tariff, Electromaps community prices and
Chargemap Pass prices. The records in `owner_collection_decisions` in
[`source_policy_exclusions.json`](../data/source_policy_exclusions.json) are owner
collection policy, **not provider licences**. They are separate from `granted_reuse`.
The Electromaps and Chargemap exclusions still block station evidence. Each has
status `station-evidence-blocked-price-annotations-permitted` and permits only its
exact `price_evidence` kind: `electromaps_community` or `chargemap_pass`, respectively.
The exception applies only when checking that exclusion's origin in those annotations.
Primary sources, nested sources, charging/parking/location/hardware claims, pricing,
connectors and media remain subject to both exclusions. Every other excluded origin
inside an annotation still blocks the row. Consum has no annotation exception.
Existing request-method restrictions remain unchanged.

Provider reuse, caching, transformation, redistribution and commercial-use rights
remain a separate review. [Apple App Store Guideline 5.2.2](https://developer.apple.com/app-store/review/guidelines/#intellectual-property)
review remains open; the owner policy does not establish provider permission.
Record that review in the [source-rights inventory](appstore/source-rights-inventory.md).

## Evidence and identity contract

The collector adds `price_evidence` only after
`merge_cross_source_duplicates(dedupe(rows))`. It never contributes a directory
row to either operation. It preserves existing IDs, aliases, merge outcomes and
primary source evidence. The historical Electromaps/Chargemap station, live and
photo adapters stay disabled; enabling the new collector does not activate them.

The optional `station.price_evidence` list contains these fields:

| Field | Meaning |
| --- | --- |
| `kind` | `repsol_network_tariff`, `electromaps_community` or `chargemap_pass` |
| `source` | Source name |
| `label` | Exact English label from the table below |
| `text` | Literal tariff, billing and fee terms retained as text |
| `source_url` | Public source page or endpoint |
| `source_updated_at` | Source timestamp as a string, or `null`; separate from retrieval time |
| `fetched_at` | Timestamp of the actual successful retrieval; cache reuse does not reset it |
| `price_per_kwh` | Nullable numeric EUR metadata; never a filter or primary price |
| `currency` | `EUR` |
| `parking_price` | Optional separate parking text from Electromaps, displayed by both clients; never primary parking evidence |
| `tariff_details` | Optional metadata for started units, idle trigger/grace, numeric fees and separate `parking_price` text |

| Kind | Exact label |
| --- | --- |
| `repsol_network_tariff` | `Repsol published tariff · station may differ` |
| `electromaps_community` | `Electromaps community price · YYYY-MM-DD` |
| `chargemap_pass` | `Price with Chargemap Pass` |

Every annotation carries a date. Electromaps requires a valid source `updated_at`
and appends its calendar date to the label. Missing or invalid source dates withhold
the community claim. A current fetch never becomes a community observation date.
For Repsol and Chargemap, `fetched_at` is the checked date when the source supplies
no update date. Old cache fallback retains its actual retrieval date.

The annotations never set or change `price_min`, `price_max`, `charge_claim`,
`is_free`, `is_cheap`, primary parking evidence, verification, live availability or
route readiness. An existing verified official/live price retains its authority.
A numeric annotation does not change cheap/free filters or ranking. Parking price
stays in the annotation's optional top-level `parking_price` field, separate from
charging text and primary parking claims. The same text may also appear in
`tariff_details.parking_price`. Normalization must preserve the additive list.

Source-family canaries still exclude historical Electromaps and Chargemap station
families. Additive annotations have separate counts and cannot supply source-family
coverage. Policy scope fields and station/operator name mentions are data, not
provider licence claims or proof of source origin.

Repsol attaches only to existing Spanish registry rows whose normalized actual
registry operator is `REPSOL SOLUCIONES ENERGÉTICAS SA`. Directory names, related
networks and nearby Repsol-branded premises are insufficient.
Rows with an existing station price receive no Repsol network-guide annotation.

Directory joins require a unique match. Prefer stable provider/EVSE keys where
available, with a 250 m maximum distance guard. Otherwise require corroborated
location/address, operator or network agreement, compatible connectors and a 40 m
maximum distance guard. Missing connectors, wrong operators, nearby mismatches,
ambiguous candidates and same-family distinct identities withhold the annotation.
The collector does not alter the existing dedupe or merge rules to force a join.

## Sources

**Repsol:** GET the [public charging page](https://www.repsol.es/particulares/vehiculos/movilidad-electrica/recarga-fuera-de-casa/)
once on every build. Require the contextual normal €0.36/kWh, fast **from**
€0.48/kWh and ultrafast €0.56/kWh table, plus the caveat that station tariffs can
differ. A failed fetch or changed page withholds this annotation only. It does not
fail the adapter/build or remove any station.

The published labels are qualitative. No numeric kW boundaries were justified in
the reviewed table. Preserve the full network table as text and keep
`price_per_kwh=null`; do not infer a station tariff from power or mark a station
price verified.

**Electromaps:** use only anonymous GETs to
`https://www.electromaps.com/mapi/v2/locations` with ordered bbox parameters
`latNE`, `lngNE`, `latSW`, `lngSW`, and the public client's fixed query:

```text
realtime=false&connectors=&types=ON_STREET,PARKING,AIRPORT,CAMPING,HOTEL,RESTAURANT,SHOP,WORKSHOP,FUEL_STATION,CAR_DEALER,MALL,TAXI&power=3&app=false&rfid=false&favorites=false&skipAuthTokenValidation=true
```

Read details from `/mapi/v2/locations/{id}`. Keep required identity, location,
operator/network, connector, `charge_price`, separate `parking_price` and
`updated_at` fields only. Sanitize before persisting snapshots. Do not retain
usernames, photos, reviews or unrelated personal data. `skipAuthTokenValidation`
is the public client's query value; it does not authorize bypassing a challenge,
login or future access restriction.

**Chargemap:** GET
`https://map.chargemap.com/mappy/charging_pools.json?NW=lat%3Blon&SE=lat%3Blon`
with northwest/southeast corners in the proper order. GET tariffs as HTML from
`https://map.chargemap.com/pool-prices/v3/prices/{id}?locale=es-es&noBootstrap=true&noFooter=true`.
Retain tariff text, per-started-kWh billing, idle trigger, grace duration and
numeric fees. These are Chargemap Pass payment-route prices, not the operator's
direct tariff. Missing fees stay unknown. List `is_free` cannot donate free
evidence. Internal numeric connector IDs are not operator EVSE identifiers;
use supplied stable EVSE keys only when their meaning is compatible.

All requests use [`source_transport.py`](../scripts/source_transport.py).
No additional POST, login, account token, payment/session action or authenticated
scraping is authorized by this collection policy.

## Bounds, cache and coverage

Each refresh has a hard maximum of **81 network requests**: one Repsol page,
16 list requests per directory and 24 detail requests per directory. Cache hits
reduce actual network use. New network requests stop after 180 seconds. Each
request timeout is at most 12 seconds and the remaining request-start budget.
This deadline is not an absolute elapsed-time limit for response reads or cache
processing. Maximum response size is 3 MB; the pause between requests is 0.25 seconds.
Budget exhaustion withholds remaining annotations;
it never removes existing rows or fails the full build.

The fixed pilot list order samples Valencia, Madrid, Barcelona, Seville, Malaga,
Bilbao, Zaragoza, Alicante and Valladolid, then Palma and Ibiza, Santa Cruz de
Tenerife, Las Palmas and Arrecife, Ceuta and Melilla. These compact city tiles avoid
country-scale cluster-only responses. They do not cover every station or town.
Every bbox has south < north and west < east. A separate Spain geography guard
includes all mainland and island regions for registry tariff annotations.
Foreign points are removed before list-cache persistence.
Detail candidates must be within 250 m of an existing row. Candidates near an
unknown primary price come first, followed by provider ID in lexical order.
The same inputs and budgets produce the same priority. Chargemap clusters are
counted as no station evidence; this bounded pass does not recursively expand them.

List caches have a one-day TTL. Detail caches have a seven-day TTL; do not refresh
a detail more often. Cache snapshots contain only the sanitized required fields.
An outage can use stale sanitized directory cache with original timestamps, or
omit an annotation. The Repsol tariff must pass a fresh contextual page check on
every build and has no stale claim fallback. No failed source aborts the build.

Build reports and payload `price_evidence_counts` report actual collected and
attached evidence counts. They do not estimate repaired registry prices or national
coverage. Multiple annotations on one row are distinct from the number of rows.

## Local candidate workflow

The main ingestion build invokes the additive collector after its existing
merge/dedupe pass. To enrich a saved baseline without creating directory pins:

```bash
python3 scripts/enrich_prices.py --baseline /path/to/baseline/free_intelligence.json \
  --out /path/to/candidate/free_intelligence.json \
  --report /path/to/candidate/price-evidence-report.json
```

Keep full candidates, sanitized source caches and detailed reports out of Git.
Check the exported candidate, IDs, counts, fees and source dates. Run the existing
delta/canary checks before any separately authorized promotion. Local collection,
backend tests, production dry run and deployment remain separate claims.
