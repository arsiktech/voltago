# Free-first source research — the 8 owner-named apps + official routes — 2026-07-17

> **Current implementation supersession (2026-08-23):** the Feníe POST-backed ingestion
> described in this dated research record is retired. Rights permission and collection method
> are independent gates: the historical owner-attested grant remains recorded, but Volta's
> GET-only boundary excludes the executable adapter and all direct or nested Feníe-origin
> station evidence. Feníe is attribution/link-only unless a compliant public GET surface is
> separately reviewed and reintroduced. Other operator verdicts below remain dated findings.

Owner directive (2026-07-17): build and maintain a database of ONLY free, verified
chargers, Spain first, tracking the networks a real Tesla driver uses
(LetMeCharge, Electromaps, EuroCharger, Fenie Recarga, MELIB, Iberdrola,
Acciona Recarga VE, GIC) via every ethical path — direct where public, indirect
otherwise. Evidence date **2026-07-17 (Europe/Madrid)**; VERIFIED = fetched in
this research pass, REPORTED = documented claim. Same caveat as the 2026-07-15
review: HTTP 200 is not a reuse licence; this is a product recommendation, not
legal advice. The 2026-07-15 rights verdicts (Electromaps LINK-ONLY, Chargemap
SKIP, ACCIONA platform no-ingest, Consum OWNER-LEGAL-DECISION) remain in force.

## The two official foundations (both VERIFIED)

### 1. DGT National Access Point — IMPLEMENTED (corroboration adapter, plan 013)

- Feed: `https://infocar.dgt.es/datex2/v3/miterd/EnergyInfrastructureTablePublication/electrolineras.xml`
  (DATEX II v3, **85.5 MB, no registration, CC BY**, refreshed daily; publisher DGT,
  data owner MITECO; legal basis Orden TED/445/2023 + AFIR 2023/1804 art. 20).
- Verified content: **12,331 sites / 36,904 refill points / 151 operators**, with
  coordinates, address/municipio, operator eMI3 ids (`ES*EMA`…), EVSE ids
  (`ES*EMA*EMELIBMALLO531`), connector type/format, kW, auth methods.
- **No tariffs and no live status** (zero price fields; the
  `EnergyInfrastructureStatusPublication` dynamic feed is NOT yet published —
  probes 404). DATEX II became mandatory for CPO remission on 2026-04-14, so the
  status/price publication should appear at `https://infocar.dgt.es/datex2/v3/miterd/`;
  **watch that directory — the day it appears it becomes the authoritative free
  flag and live-status source and demotes everything else to corroboration.**
- Implementation (this repo): `source_dgt_nap()` in `scripts/ingest_sources.py` —
  corroboration-only rows (same gate as Madrid/Zaragoza: unmatched price-unknown
  rows are dropped; a missing price is never free). Measured against the current
  canonical DB: **228 of 577 free rows have a NAP site ≤80 m** (the address-token
  identity check will admit a subset of those), and the adapter attaches an
  `operator_app` handoff (9,179 of 12,331 sites map to a driver app).

### 2. Mapa REVE external API — PREPARED, owner-key-gated (`scripts/reve_external.py`)

- MITECO + Red Eléctrica's official real-time map (`mapareve.es`) has a
  **documented public API** (Swagger VERIFIED at
  `https://www.mapareve.es/docs/api/external/v1`): GET `/connectors/tariffs`
  (tariff price components — **a 0 € tariff is the authoritative FREE signal**),
  GET `/evses/operational_status`, GET `/locations`; auth header `x-api-key`;
  **key via the public form `https://www.mapareve.es/api-contacto`** (email +
  reason); rate limit **5 requests/hour**, pagination ≤100/page, `date_from`
  incremental deltas.
- The public web map shows socket-level live status (Available/Charging/
  Reserved/Out of order) without login — the lawful answer to "see that the
  charger is online" across ALL networks at once.
- Activation runbook (owner): (1) request the key via the form; (2) `export
  VOLTA_REVE_API_KEY=…`; (3) `python3 scripts/reve_external.py --probe` and save
  the JSON under `data/source_probe/`; (4) run `--sync` on a schedule (the
  client budgets `VOLTA_REVE_PAGE_BUDGET` requests/run, persists a cursor in
  `data/cache/reve_external_state.json`, and flips to `date_from` delta mode
  after backfill); (5) only then wire the tariff map into ingestion as a new
  free-claim authority joined on NAP EVSE ids (a follow-up, evidence-first).

## The 8 owner-named apps — verdicts

| App | Operator / platform | Own public surface | Path for Volta |
|---|---|---|---|
| LetMeCharge | LetMeCharge SL (EMSP aggregator; NOT Repsol) | None (app-only) V | **ATTRIBUTE-ONLY** — its networks arrive via NAP/REVE |
| Electromaps | Wallbox-owned aggregator/EMSP | Public station pages show "Gratis" V, live status app-gated | **LINK-ONLY** (2026-07-15 rights verdict stands — ToS prohibits reuse despite permissive robots) |
| EuroCharger | EuroCharger S.L., Elche (white-label platform + EMSP) | None (app-only) V | **ATTRIBUTE-ONLY** |
| Recarga (Feníe) | Feníe Energía (CPO+EMSP), 188 NAP sites | `recarga.fenieenergia.es` public map with "Gratuitos" filter V; per-marker `gratuito`/`tarifa` flag | **IMPLEMENTED 2026-07-18 under owner-attested written permission** (covers public redistribution). `source_fenie` ingests only `gratuito==1` free stations (238 in the 2026-07-18 pull). Recorded in `source_policy_exclusions.json` `granted_reuse`. |
| MELIB | Govern Balear / IBE; Palma street points = EMAYA; platform ETECNIC; 119+ NAP sites | `melib.etecnic.net` is login-only V | **INDIRECT** (NAP — implemented; REVE when keyed) + handoff |
| Movilidad (Iberdrola) | Iberdrola (~2,736 NAP sites) + JV "CHARGING TOGETHER SL" (512 sites) | JV map `iberdrola-bppulse.es/mapa/` shows real-time status publicly V(FAQ); full-network map app-only | **INDIRECT** for data; JV map is a manual verification surface; handoff links attached |
| Acciona Recarga VE | ACCIONA (254 NAP sites); app = ex-"Charge and Parking" | None (landing only) V; platform ToS prohibits extraction; bulk endpoint POST-based | **INDIRECT** (NAP — implemented; Moraleja Green static lead stands) + handoff |
| GIC | Gestión Inteligente de Cargas (Grupo ETRA/Vinci), platform eCoVe; 101 NAP sites | Brochure site only V | **INDIRECT** (NAP; REVE when keyed) + handoff |

`operator_app` handoff links for all of the above (plus Endesa X Way, Wenea,
Zunder, Repsol/Waylet, Tesla, Etecnic/EVcharge) are attached by the NAP adapter —
attribution instead of scraping, per Prime Directive 2.

## Owner-requested expansion assessed 2026-07-18 (Fenie / Iberdrola bp pulse / Zunder)

Owner authorized adding the operators' own public no-login maps. Probed all three:

- **Feníe — IMPLEMENTED under owner-attested written permission (2026-07-18).** Its public
  map is a network of FREE stations: the anonymous GET-cookie→CSRF-POST markers call returns
  268 markers, all `gratuito=1`, with connectors/power/`estado`/coords. Timeline this session:
  written on owner request → reverted on finding the 2026-07-15 rights block → **re-enabled
  after the owner confirmed express written permission covering public redistribution** via
  Volta (voltago.app + App Store). `source_fenie` ingests only `gratuito==1` stations
  (238 in the 2026-07-18 pull, tier `official-free`); out-of-service (`estado==4`) dropped;
  live status still comes from MapaREVE/REVE, never this weekly snapshot. The permission is
  recorded in `data/source_policy_exclusions.json` under `granted_reuse`
  ("fenie-owner-attested-permission") as the audit trail + revocation gate — Claude recorded
  it on the owner's in-session attestation and did not independently verify the document; the
  owner retains the written permission off-repo. Access is an anonymous read-only CSRF-POST
  (the public map's own call), a documented relaxation of the strict GET-only wording for this
  permitted surface. If the permission lapses, restore the exclusion. Electromaps / Chargemap /
  Consum remain rights-blocked (no such permission).
- **Zunder — SKIP for free-first (confirmed by probe).** Its `location.php` JSON is public
  GET with real live availability (`chargers_in_use`, per-connector status) — genuinely nice
  data — BUT the network is PAID (published €0.42–0.59/kWh) and the JSON has no price field.
  A missing price is not free; adding it would put paid chargers into a free-only DB. Its
  location facts already arrive via NAP.
- **Iberdrola | bp pulse — SKIP for free-first.** The JV map is PAID fast/ultra-fast hubs.
  Iberdrola's genuinely-free municipal points are in the broader network, already reaching
  the DB via NAP (2,736 Iberdrola sites) + REVE tariffs.

## Other findings worth keeping

- **OpenChargeMap**: API key now mandatory (anonymous GET → 403 VERIFIED).
  `UsageCost` free-text remains the main community free signal; `opendata=true`
  filters open-licensed POIs; use `modifiedsince` for deltas.
- **OpenStreetMap** (Overpass VERIFIED): ES `amenity=charging_station` = 5,102;
  `fee=no` = 345; `charge~"^0"` = 91. ODbL — keep OSM-derived provenance
  separable (share-alike) from CC BY sources; already the repo's practice.
- **No MITECO "geoportal EV" REST API exists yet** (fuel-style
  `ServiciosRESTPuntosRecarga` → 404 VERIFIED); the RIPREE registry content
  reaches us via the NAP feed daily. Regional open data (Cataluña ICAEN Socrata,
  València ArcGIS, Castilla y León) publish locations without prices.
- **EU CEAP** (common access point over all member NAPs) is mandated by
  2026-12-31 — a 2027 watch item.
- Supermarket networks in the NAP feed: Mercadona 413, Carrefour 113, **Lidl
  only 11** (heavily under-reported vs reality — a NAP compliance gap; keep the
  Lidl first-party adapter).

## Community layer (plan 013) — how "verified" is defined now

`/api/free-registry` ladders every free row: `live` (live source now) >
`community` (successful check-in ≤60 days) > `corroborated` (≥2 independent
source families) > `reported` (single source). Community evidence and
corroboration raise FREE-claim confidence only — they never set
`verification.live_verified`, and repeated recent failure check-ins downgrade a
"Go now" to "Check first" (fail-closed, `community.py`).
