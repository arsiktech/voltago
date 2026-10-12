# Volta — public source research (2026-06-23, Valencia-era)

> **Scope note (added 2026-07-02):** this research predates the 2026-06-25 national pivot and
> was scoped to **Valencia province**. The endpoint verifications, the Gaia enumeration and the
> historical observations remain research provenance, but the whitespace **SKIP verdicts (Powerdot, Wenea,
> Zunder, Acciona, PlugShare, …) were decided on Valencia-province economics and have NOT been
> re-run at national scale** — a national re-probe may change some of them (e.g. municipal/FEDER
> free programs in other regions).
>
> **Feníe retirement note (2026-07-13):** the method documented below is historical and must
> not be treated as current architecture. Direct runtime/ingestion adapters and Feníe-origin
> station evidence were removed. The provider is homepage attribution/link-only because the
> request method fell outside Volta's GET-only public-page rule and its [public conditions](https://recarga.fenieenergia.es/pagina/condiciones-de-uso-y-politica-de-privacidad)
> do not grant reuse through access alone. Reintroduction requires express written permission
> plus a separate permitted GET-only adapter review.
>
> **National/reuse supersession (2026-07-15):** use
> `docs/source-research-2026-07-15-national.md` for current expansion and rights decisions.
> In particular, the Electromaps detail/realtime TODO below is cancelled: current terms make
> further extraction **LINK-ONLY pending express permission**. Chargemap expansion/refresh is
> also stopped pending written API/data permission. Plan 011 implemented that remediation:
> Electromaps and Consum are link-only, Chargemap is removed, their adapters are inactive, and
> the canonical database retains zero direct or nested evidence from those families. Remaining
> active sources still require owner certification; reintroduction remains permission-gated.

Result of a 37-agent research + adversarial-verification workflow over every named + candidate
public EV-charging data source for **Valencia province** (free / ≤ €0.25, realtime). All
endpoints below were **verified live** (HTTP 200, no login / token / paid key) unless noted.
Hard rule unchanged: **public / user-verifiable only** — no auth bypass, private tokens, paid keys.

## Verified public endpoints

| Source | Method (public) | Gives | Realtime | Status in Volta |
|---|---|---|---|---|
| **MapaREVE** (REE) | `POST https://www.mapareve.es/api/public/v1/locations` (body = bbox `{latitude_ne,longitude_ne,latitude_sw,longitude_sw,zoom}` + filters `available`,`energy_price_max`,`connector_types[]`); `/markers` for clusters; `/stats` | locations, price (`tariffs[].human` + structured `price_components` ENERGY), kW (watts/1000), sockets, Tesla connectors | ✅ per-EVSE `status` + `status_updated_at` (minute-fresh) | **PRIMARY live** (already used). Behind Imperva — send browser UA + Referer, throttle, cache, server-side (not browser-direct). |
| **Feníe Energía Recarga** | **Historical only:** GET home/CSRF cookie then POST public listing | Historical probe exposed free-station fields | Not used | **RETIRED 2026-07-13:** direct adapter and redistributed station evidence removed; homepage attribution/link-only. |
| **GIC / ECOVE** | `POST https://clientes.recargagic.com/DataServices.asmx/obtenerEstaciones` body `{"location":null}` → `json.loads(resp["d"])` | ~7134 nat rows; locations, `ESTADO_DESC`, `TIPO_ENCHUFE`, `POTENCIA_ENCHUFE` (watts), Type2/CCS/CHAdeMO. **NO price.** | ✅ `ESTADO_DESC` | DEFER — no price + heavy MapaREVE overlap; use as live-path corroboration later (dedupe on coords). |
| **Electromaps** | Historical Valencia adapter, now inactive | No retained station/location/price/status/connector/media evidence | Not authoritative in Volta | **LINK-ONLY IMPLEMENTED 2026-07-15. Express written permission plus a new GET-only adapter/media review is required to reintroduce data.** |
| **Chargemap** | Historical Valencia adapter, now inactive | No retained directory evidence | ❌ | **REMOVED 2026-07-15. Written API/data permission plus a new GET-only review is required to reintroduce it.** |
| **OpenChargeMap** | current snapshot `ocm-data/master/poi.json.gz` (alive, 30MB) **or** per-POI `ocm-export/data/ES/OCM-*.json` + `referencedata.json` | free/cheap price-text, kW, sockets | ❌ | Integrated via gz snapshot. Per-POI "fix" = thousands of requests → not worth it; gz is fine. |
| **OSM Overpass / Lidl / Consum / municipal (GVA ArcGIS)** | public | static free/cheap leads, tags | ❌ | Integrated as static/discovery layers. |

## Attribute-only / skip (no usable public data)
- **Google Maps** — Places needs paid key + no price field → navigation deep-link only.
- **LetMeCharge** (partner login), **EuroCharger** (Wix + app-only), **Miio** (Cloudflare Access token) → mention only.
- **Iberdrola / Acciona** — premium €0.35+ (never ≤€0.25); arrive via MapaREVE OCPI; skip direct for the cheap goal. (Acciona's keyless `POST back.clientesrecarga-acciona.com/api/v3/ubicaciones/list` works and carries IKEA rows — optional future free cross-ref.)
- **Tesla** — SC in Valencia €0.51–0.56 (fails ≤€0.25); no public realtime (owner-OAuth only). Do **not** build a Tesla scraper; surface only as it roams via MapaREVE, badge `network=Tesla` with honesty note.

## Gaia enumeration (the user's free-charger site)
- The `partner-next.gaiacharge.com/cpo/jump/<slug>` page is **Alpine.js `x-data`** (JS object, not JSON): `html.unescape` → `ast.literal_eval`; realtime via public SSE `GET /events/pdn_connector:<CHARGER>:<n>/` or 30–60s page re-GET.
- **No top-down crawl** (no sitemap/listing; `/cpo` 404). Enumerate **seed-then-expand**: find Gaia stations in MapaREVE/Electromaps/OCM (operator/title contains "Gaia", Valencia bbox) → GET each station page → parse sibling connectors from the page (never guess `-2/-3`) → dedupe vs MapaREVE on `ES*VAL*` EVSE id. Keep `smoke_gaia_parser.py` as the breakage canary.

## Realtime authority order (per station, highest first)
1. MapaREVE OCPI (status + status_updated_at) — primary.
2. GIC `ESTADO_DESC` — corroboration when independently available through a permitted source.
3. Gaia public page/SSE evidence for known slugs.
Always: dedupe on EVSE id / coords+connector before merging; convert all power to kW; label realtime source + timestamp; never fake realtime from OSM/OCM/Chargemap-free.

## Whitespace to explore next (completeness critic)
Not yet checked, likely NEW free/cheap or realtime coverage:
- **Powerdot** (`app.powerdot.es` / `charge.powerdot.com` bbox XHR) — major free/cheap retail CPO.
- **Wenea** (free municipal contracts), **Zunder**, **EnergyGo** own maps.
- **Plugshare** (`api.plugshare.com/v3/locations/region`) — largest community realtime+free layer.
- **DGT NAP / DATEX II** (`nap.dgt.es`, `infocar.dgt.es/datex2/`) — official OCPI realtime, lower ToS risk than scraping mapareve.
- **Valencia OpenDataSoft** (`https://valencia.opendatasoft.com/api/explore/v2.1/catalog/datasets?q=recarga`), VLCi smart-city.
- **goingelectric.de** (free-key API) / **chargefinder.com** XHR — EU cross-check.
- Parking-operator chargers (Saba, Empark, EYSA Valencia).

> Historical structural note: free stations rarely expose realtime in aggregators. Any
> operator-direct feed must still pass current method, permission, and redistribution review.

---

## Whitespace probe round 2 (2026-06-23, 24 agents) — outcome: mostly SKIP

Probed Powerdot, Wenea, Zunder, PlugShare, DGT NAP, València OpenDataSoft/VLCi, Acciona-IKEA,
GoingElectric, Chargefinder, Endesa X, parking operators. **Decision: none integrated** — none
advance the free / ≤€0.25 goal, and adding them would bloat a free-first app with premium or
duplicate data. Recorded so we don't re-probe:

| Source | Public? | Verdict | Why not integrated |
|---|---|---|---|
| Wenea | yes (`wenea.com/localizadorv2/localizaciones.php` + detail) | SKIP for cheap | Realtime sockets, but Valencia €0.39 AC / €0.49 DC — above cap; ~11 stations, high MapaREVE overlap. |
| Zunder | yes (`/wp-content/plugins/easycharger/location.php?id=`) | SKIP for cheap | Clean realtime JSON, but no price field; retail €0.42–0.59; municipal/FEDER points (Torrent ER-xx) better priced via MapaREVE. |
| DGT NAP / DATEX II | yes (`infocar.dgt.es/datex2/...electrolineras.xml`, 85MB) | SKIP | Same MITERD source as MapaREVE → duplicate; no price, no realtime. |
| Acciona | yes (`back.clientesrecarga-acciona.com/api/v3/ubicaciones/list`) | SKIP for cheap | €0.35–0.55, zero <€0.25; "0 EUR" rows are unconfigured not free; **no IKEA free rows** (earlier hunch refuted). |
| Powerdot | yes (`powerdot.eu/wp-json/wpgmza/v1/markers`, 8.5MB global) | SKIP | Only Live/Under-Installation flags — NO kW/connector/sockets/price/realtime; tariffs ~€0.30–0.49. |
| PlugShare | no | SKIP | Cloudflare 403; v3 needs private Cognito JWT (auth bypass). |
| Endesa X / Enel X | no | SKIP | OAuth-only; already arrives via MapaREVE; €0.35+. |
| GoingElectric.de | no (free key behind forum login + app-only ToS) | SKIP | Fails public/keyless rule. |
| Chargefinder | yes-ish (hardcoded AES key in JS) | SKIP | Fragile/non-contractual; 0 free Valencia. |
| València VLCi / geoportal | yes (CKAN+ArcGIS) | SKIP | 14 city points, construction status only; covered richer by MapaREVE/OCM. |
| Parking ops (Saba/Empark/EYSA) | partial | SKIP | App-only; no keyless connector/price/realtime. |

**Current bottom line (superseded 2026-07-15):** use the national review for current source truth.
MapaREVE/Gaia remain live paths; the intelligence database uses permitted open/official families
including OSM, OCM, Endolla, Madrid, Zaragoza, Red MOVEM, and Moraleja. Feníe, Electromaps, and
Consum are link-only; Chargemap is removed.

---

## Gaia expansion outcome (2026-06-23)

Gaia publishes **no station/connector list** (no sitemap; `/cpo` 404; station pages expose no
connector ids or live x-data) — confirmed by inspection. So live per-connector parsing only works
for stations whose **connector id** is known, and `VOLTA_GAIA_JUMP_URLS` lets you add more.
All **3 known Valencia Gaia stations are covered**:
- **Alboraia · Carrer dels Artesans** (`fr-grn-ees-val-alb-artesans-1` / `CIRCUTOR-0786-*`) — **LIVE** via jump-page parser.
- **Alboraya · Carrer dels Fusters / Via del Ferrocarril** (`fr-grn-ees-val-alb-fusters-1`) — free **lead** (Chargemap); connector id not published → not live-parsable.
- **Playa de la Patacona, Alboraia** (`fr-grn-ees-val-patacona-1`) — free **lead** added to `data/seed_leads.json` from the public Gaia station page (Free, Type 2, 22 kW).

To promote a Gaia station to live later: obtain its connector id (e.g. from the on-charger QR / a
working jump URL `.../cpo/jump/<slug>/<CONNECTOR-ID>`) and add it via `VOLTA_GAIA_JUMP_URLS` or a
`GAIA_STATION_SEEDS` entry.

## Deployment

Repo is deploy-ready: `Dockerfile` (multi-stage SPA build + Python-stdlib runtime), `.dockerignore`,
`fly.toml`, and `DEPLOY.md` (Cloudflare Tunnel from a home machine = most reliable live data;
Fly.io / any container host = easy public URL with the datacenter-IP scraping caveat; or local).
