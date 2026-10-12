# National source and reuse review — 2026-07-15

Status: research complete and the safe repository-local verdicts are implemented by Plan 011.
The checkout/count section below preserves the dated pre-implementation baseline explicitly;
the post-implementation result is recorded immediately after it.

Evidence date: **2026-07-15 (Europe/Madrid)** unless a source date is stated explicitly.

This is an evidence-based product recommendation, not a legal opinion. `robots.txt`, public
access, copyright/database rights, contractual terms, and an open-data licence are different
questions. A URL returning HTTP 200 is not by itself permission to store, transform, or
redistribute its contents.

## Bottom line

| Candidate | Verdict | Product action |
|---|---|---|
| Consum national locator | **OWNER-LEGAL-DECISION** | Technically available, but do not ingest nationally without written permission. At most link to the Consum home page under the current terms. |
| Electromaps | **LINK-ONLY** | Do not expand the current Valencia ingestion. Link externally without copying data or media; obtain express permission before any further API ingestion. |
| Chargemap | **SKIP** | Do not expand or refresh through the public map endpoint: generic crawling is disallowed and the current CGU prohibits extraction without written authorization. |
| Wenea | **LINK-ONLY** | MyStation is genuinely €0.25/kWh, but it is a paid, account-bound tariff at one user-selected station. Volta cannot infer that selection from public data. |
| Zunder core network | **SKIP** | Current general Spanish tariffs are above €0.25/kWh; absence of price in station JSON is not free evidence. |
| Zunder-managed Red MOVEM, Badajoz | **IMPLEMENT** | A narrow official exception: attach €0.25/kWh only to matched Red MOVEM connectors at ≤22 kW, with the public-price regulation as the claim source. |
| ACCIONA nationwide platform | **OWNER-LEGAL-DECISION** | General tariffs are above the cap, platform terms prohibit extraction, and the known bulk endpoint is POST-based. Do not ingest it. |
| ACCIONA Moraleja Green pilot | **IMPLEMENT** | One static, non-live, verify-first free lead has a current first-party evidence chain. Keep parking terms separate. |
| Madrid open data | **IMPLEMENT** | CC BY 4.0 official location/operator/power corroboration; no price or live-status claim. |
| Barcelona Endolla open data | **IMPLEMENT** | CC BY 4.0 locations plus current port status, and an official conditional €0.22/kWh tariff for normal BSM charging at night with a subscription. |
| Zaragoza open data | **IMPLEMENT** | Official reusable location/operator/connector/power facts; no price or live-status claim. |

The highest-value source was **Barcelona Endolla**. Plan 011 now ingests its official facts and a
real, explicitly conditional cheap tariff. The weekly artifact persists every Endolla row as
Verify-first with UNKNOWN connector availability; observed port status remains timestamped source
provenance only, never durable live authority. Madrid and Zaragoza improve official
corroboration, but neither supplies a cheap/free claim.

## Pre-implementation checkout baseline

The handoff was compared with the then-current `main` checkout and generated database rather than
accepted as a complete inventory. The figures in this subsection are the 2026-07-15 baseline
before Plan 011 source implementation and rights remediation; they are not current data counts.

- `HEAD` and `origin/main` are `6a6389f`; commit `d6a5b7c` already made OpenStreetMap national
  and free/cheap-only.
- The handoff's count of two national families remains correct, but its family names are stale.
  The current national families are **OpenChargeMap and OpenStreetMap**, not OpenChargeMap and
  Lidl.
- `data/free_intelligence.json` was generated at `2026-07-15T11:12:26.590922Z`, has 909 records,
  and reports no ingestion errors.
- Current primary-source counts are: OpenChargeMap 382, Electromaps 212, OpenStreetMap 206,
  Consum 61, Chargemap 24, València OpenData 16, and Lidl 8.
- `source_consum()` still starts at `/supermercados/valencia/`; `source_electromaps_mapi()` still
  queries `VALENCIA_BBOX` and applies Valencia filters; `source_chargemap_mappy()` still walks a
  Valencia box and applies the same province guard.
- `source_lidl()` is also still Valencia-only: it extracts the page's `Valencia` section and rejects
  any listing without a `46xxx` postcode. All eight current Lidl primary rows are therefore local,
  despite the handoff describing Lidl as national.
- València OpenData is correctly local by definition. The remaining national coverage gap is
  therefore Lidl/Consum/Electromaps/Chargemap plus additional licensed official city sources, not
  OSM.

Two conclusions in the handoff need refinement:

1. Consum is technically nationalizable, but it is **not rights-unblocked**. Its current legal
   notice makes national ingestion an owner/permission decision.
2. The Valencia-era rejection of Wenea/Zunder/ACCIONA was too broad. Their ordinary tariffs still
   fail the cap, but three narrow cases now matter: Wenea MyStation (link-only), Red MOVEM at
   €0.25/kWh (implementable on matched records), and the ACCIONA Moraleja Green free pilot.

Existing commercial-directory rows were not grandfathered by this review. The baseline's 212
primary Electromaps rows, Electromaps photo evidence, 24 primary Chargemap rows, and 61 Consum
rows therefore entered remediation rather than national expansion.

## Post-implementation canonical result

Plan 011 implemented Barcelona Endolla, Madrid, Zaragoza, Red MOVEM, and Moraleja Green under the
boundaries below. It also implemented the conservative commercial-source verdicts in structured
policy: Electromaps and Consum are link-only, Chargemap is removed, and all three have zero direct
or nested station/claim/media evidence in the canonical database.

- Final canonical artifact after the safety review: **650 merged records from 850 raw rows,
  zero errors**, file SHA-256
  `e8958d03315453c0db42900400b1050bca06ada8b0c57d48e21e8d83805ffc3d` and stable recordset
  SHA-256 `bb883b68fb00726258a5a646f47cddb0d58ef35f3d540e99d8b9714b625bd23c`.
- Primary-source counts: OpenChargeMap 374, OpenStreetMap 208, Barcelona Endolla 43,
  València OpenData 16, Lidl 8, Moraleja Green 1, Madrid 0, and Zaragoza 0.
- Four OpenStreetMap rows carry the separately attributed Red MOVEM official tariff claim.
- Madrid/Zaragoza facts are corroboration-only. The adapters parse 123 Madrid and 37 Zaragoza
  official rows, but no row survives without current independent cheap/free evidence. The four
  earlier Madrid matches were removed because their only price evidence was missing or more than
  two years old. A distinct site 30 m away does not merge.
- Relative to the earlier 657-row checkpoint, the final review removes one motorcycle-only
  Endolla location, four ancient-price Madrid matches, and two impossible/quarantined
  OpenChargeMap rows. The final 650-to-650 rebuild has no missing or added IDs, canary `ok=true`,
  and zero blocked evidence or stale `source_count` values.

## Decision rules used in this review

- **IMPLEMENT** means the cited public evidence is suitable for a narrowly modelled Volta source,
  subject to the stated attribution, condition, and fail-closed rules.
- **LINK-ONLY** means Volta may send the user to the official surface but should not bulk copy its
  station dataset or media.
- **OWNER-LEGAL-DECISION** means there is a usable public surface but no adequate reuse grant, or
  the provider's terms create a material ambiguity that requires written permission/owner review.
- **SKIP** means the candidate currently fails Volta's price, GET-only, access, rights, or data-
  honesty constraints.
- A missing price/payment field is **unknown**, never zero or free.
- `payment_required=true` proves payment is required, not the amount.
- A cheap claim requires a named official tariff or an explicit official free statement. Every
  membership, time, power, station-selection, access, and parking condition must survive into the
  normalized claim.

## Commercial directories and store locators

### Consum — OWNER-LEGAL-DECISION

Official surfaces checked:

- [National store locator](https://www.consum.es/supermercados/)
- [Consum help article describing the locator](https://ayuda.consum.es/hc/es/articles/360012516520--C%C3%B3mo-encuentro-un-supermercado-Consum-o-Charter)
- [EV-service-filtered locator](https://www.consum.es/supermercados/?field_servicios_centro_value%5Brecarga_coche%5D=recarga_coche)
- [Same-site filtered JSON response](https://www.consum.es/get-map-list/block_otros_supermercados_consum/?field_servicios_centro_value%5Brecarga_coche%5D=recarga_coche)
- [robots.txt](https://www.consum.es/robots.txt)
- [Legal notice and terms](https://www.consum.es/aviso-legal-y-terminos-y-condiciones/)

The national filtered JSON was public GET, required no login/cookie/token, and returned 159 store
entries tagged with an EV-charging service. The unfiltered response contained 1,033 Consum/Charter
locations. Records expose a store ID, name, coordinates, address, store URL, and rendered service
HTML. It is an undocumented Drupal AJAX route, not a supported public API, and its CORS policy is
limited to the Consum origin.

This evidence proves only that Consum lists a charging amenity. It does **not** publish operator,
public-access conditions, connector, power, live status, tariff, free charging, or parking terms.
Even with permission it could create only official-directory leads, never a live or ≤€0.25 claim.

Consum's robots file does not disallow the locator or JSON path. That is an access observation,
not a reuse licence. The legal notice says the site is for `uso exclusivamente particular`,
requires consent for copying/storage, and limits third-party linking to the home page. No open-data
or station-dataset licence was found.

**Implemented action:** the locator adapter is inactive and the structured policy removes direct
and nested Consum locator evidence. Only the conventional Consum homepage link is permitted.
Request written permission covering periodic GET retrieval, caching, transformation,
redistribution, attribution, and link scope before any reintroduction.

### Electromaps — LINK-ONLY

Official surfaces checked:

- [Current terms and conditions](https://www.electromaps.com/es/terms-and-conditions) — last update shown as 2025-12-02
- [`www.electromaps.com` robots.txt](https://www.electromaps.com/robots.txt)
- [`map.electromaps.com` robots.txt](https://map.electromaps.com/robots.txt)

The current `www` robots file broadly allows crawling and does not disallow `/mapi/v2`, the GET
surface used by the existing adapter. The separate map-host robots file allows only a small set of
routes and does not allow general station-detail crawling. Neither file grants storage or reuse.

The current terms apply to site/app visitors, prohibit sharing information appearing on the
platform, reserve exploitation rights absent express permission, and permit conventional external
links subject to stated conditions. This is incompatible with scaling the current data and photo
extraction merely because the endpoint is reachable.

**Implemented action:** `source_electromaps_mapi()` and direct detail refresh are inactive; the
structured policy removes direct and nested Electromaps station/claim/connector/media evidence.
Only conventional external links remain permissible. Seek express written permission before any
reintroduction.

### Chargemap — SKIP

Official surfaces checked:

- [Chargemap legal page](https://chargemap.com/es-es/about/legal)
- [Current linked General Terms of Use PDF](https://community-optins.chargemap.com/conditions/3/document?locale=en_GB)
- [robots.txt](https://chargemap.com/robots.txt)

The current legal page still links the four-page CGU document; the PDF metadata is dated
2023-09-26. Its content licence permits private viewing but withholds extraction, reproduction, and
reuse of site/editorial content without written authorization.

The current robots file has named exceptions for some search crawlers, but its generic rule is
`Disallow: /`. Volta is not one of the named exceptions. This is both an operational access stop and
a separate rights stop; one does not substitute for the other.

**Implemented action:** `source_chargemap_mappy()` is inactive and structured policy removes direct
and nested Chargemap evidence. Ask Chargemap for written API/data permission plus a new GET-only
adapter review before considering reintroduction.

## Nationwide operators and real ≤€0.25 exceptions

### Wenea — LINK-ONLY

Official evidence:

- [Current ordinary charging tariffs](https://wenea.com/tarifas-de-carga/)
- [Wenea Everyday and MyStation](https://wenea.com/wenea-everyday/)
- [March 2026 compatible-station list](https://wenea.com/wp-content/uploads/2026/03/Descargable-estaciones-compatibles-Wenea-Everyday_Provincias1.pdf)
- [Public locator](https://wenea.com/encuentra-punto-de-carga/)

Ordinary Wenea prices are €0.39/kWh AC, €0.49 DC, and €0.59 HPC. Everyday discounts reduce those
to €0.29, €0.37, and €0.44, still above Volta's cap.

MyStation is a genuine exception: €0.25/kWh at **one assigned Wenea station** for a subscription of
€16.99/month or €169.90/year. It requires a Wenea account/app; the selected station can change once
per month. The public compatible-station PDF lists eligible stations but cannot say which station
this user selected, and bulk reuse permission for the locator/list was not established.

**Action:** link to the official MyStation flow and explain the condition. Do not mark every eligible
station as €0.25. Revisit only if the owner wants subscription-aware user configuration and obtains
permission to reuse the compatible-station list.

The older Wenea case study describing 32 free Diputación de Badajoz stations is no longer current:
the service stopped being free on 2025-01-01 and the active network now uses Zunder's platform.

### Zunder core network — SKIP

Official evidence:

- [Current Spanish user tariffs](https://www.zunder.com/usuario-ve/)
- [Official location map](https://www.zunder.com/mapa-de-ubicaciones/)
- [Example public station JSON](https://www.zunder.com/wp-content/plugins/easycharger/location.php?id=4)

Published Spanish prices are above €0.25: non-subscriber rates include €0.42/kWh at ≤50 kW and
€0.59 above 50 kW; subscriber rates include €0.39 and €0.45. Another tariff block displayed by the
same official page starts at €0.30. The station JSON supplies coordinates, connectors, power, and
availability buckets but no price.

The missing price field is not a zero-price signal. A 2023 official announcement about free Zunder
charging at 27 Gipuzkoa sites specified a two-year period; that period expired in 2025 and no
official extension was found.

**Action:** do not add the general Zunder network as a cheap/free source.

### Zunder-managed Red MOVEM, Badajoz — IMPLEMENT narrowly

This is a public-network exception, not Zunder's general tariff:

- [BOP 129/2024, official Red MOVEM public-price regulation](https://www.dip-badajoz.es/toolsphp/descarga.php?fr=6&id=bop_2024-07-08000000_firmado.pdf)
- [Diputación notice putting the tariff into operation from 2025-01-01](https://sostenibilidad.dip-badajoz.es/entrada/noticias/la-diputacion-de-badajoz-establece-precios-publicos-para-el-uso-de-la-red-de-puntos-de-recarga-para-vehiculos-electricos-a-partir-del-1-de-enero)
- [January 2026 current-network confirmation](https://sostenibilidad.dip-badajoz.es/entrada/noticias/la-diputacion-de-badajoz-inicia-los-trabajos-de-ampliacion-y-repotenciacion-de-la-red-provincial-de-recarga-de-vehiculos-electricos)
- [May 2026 expansion plan](https://sostenibilidad.dip-badajoz.es/entrada/noticias/la-diputacion-de-badajoz-impulsa-el-plan-movem-ii-con-mas-de-83-millones-para-reforzar-la-movilidad-sostenible-en-la-provincia)

The BOP sets €0.30/kWh for connectors up to 50 kW and **€0.25/kWh for connectors at or below
22 kW**, VAT included. It says the agreement remains effective until modified or repealed. The
January 2026 official update confirms a current network of 9 × 50 kW and 23 × 22 kW stations,
available through the Zunder platform. No later tariff modification was found in this review.

**Implementation boundary:** use independently reusable location records (for example, matched
OSM/OpenChargeMap rows) and attach the BOP as the charge-claim source only when the record is
unambiguously part of Red MOVEM and the connector is ≤22 kW. Do not bulk copy the Diputación's
station web pages; their general site notice does not provide an open-data licence.

The 2026 expansion plan includes upgrades from 22 to 25/50 kW and new/relocated stations. A refresh
must therefore remove the cheap claim when a connector exceeds 22 kW, and must not infer the tariff
for planned but not commissioned equipment.

### ACCIONA nationwide platform — OWNER-LEGAL-DECISION; practical SKIP

Official evidence:

- [ACCIONA Recarga](https://recarga.acciona.com/)
- [Platform conditions of use](https://recarga.acciona.com/es/condiciones-generales-de-uso)

Current published prices are €0.35/kWh at ≤22 kW and €0.55 above 22 kW. The platform conditions
prohibit extraction/web scraping and reproduction without prior written authorization. The known
bulk station surface is POST-based, which also violates Volta's public GET-only source rule.

**Action:** do not ingest the nationwide platform. Treat numeric zero or missing tariff values as
unconfigured/unknown unless an independent named official page explicitly states free charging.

### ACCIONA Moraleja Green — IMPLEMENT one static free lead

Official evidence chain:

- [ACCIONA Green Smart Charge instructions](https://www.acciona.com/es/gsc) state that charging is free and uses the RFID attached to the charger.
- [ACCIONA's 2021 launch announcement](https://www.acciona.com/es/actualidad/noticias/acciona-energia-lanza-solucion-inteligente-carga-eficiente-vehiculos-electricos) identifies Moraleja Green in northern Madrid as the shopping-centre pilot.
- [Current Moraleja Green services page](https://moralejagreen.com/servicios/) still lists three ACCIONA standard chargers at Avenida de Europa 13, 28108 Alcobendas, in Parking Norte, level -2.

**Implementation boundary:** one static official lead, never live and never route-ready solely from
this evidence. Use `Verify first`; tell the user to confirm the free RFID process at the information
desk. Keep the parking claim separate: the venue advertises limited free-parking windows, not
unconditional free parking.

## Official city open data

### Madrid — IMPLEMENT official location facts only

Official sources:

- [Dataset catalogue](https://datos.madrid.es/dataset/208979-0-puntos-recarga-historico)
- [CKAN metadata API](https://datos.madrid.es/api/3/action/package_show?id=208979-0-puntos-recarga-historico)
- [Current CSV](https://geoportal.madrid.es/fsdescargas/IDEAM_WBGEOPORTAL/MOVILIDAD/VEHICULOS_ELECTRICOS/CSV/PUNTOS_PUBLICOS_RECARGA_VEHICULOS_ELECTRICOS.csv)
- [Official ArcGIS service](https://sigma.madrid.es/hosted/rest/services/GEOPORTAL/ESTACIONES_PUBLICAS_RECARGA_RAPIDA_VEHICULOS_ELECTRICOS_GP/MapServer)

The catalogue is CC BY 4.0 and monthly. The 2026-07-15 probe observed 123 data rows covering public
rapid stations above 50 kW. Fields include address, district/neighbourhood, operator, equipment
count/details, and ETRS89/UTM zone 30 coordinates. There is no tariff, payment, or availability
field.

**Implementation boundary:** official discovery and corroboration of location/operator/power only.
Do not create free/cheap or live claims. Prefer an official ArcGIS JSON query transformed to WGS84,
or test a stdlib UTM conversion against fixtures before accepting CSV coordinates. Preserve CC BY
attribution and the dataset update date.

### Barcelona Endolla — IMPLEMENT locations, observed-status provenance, and one conditional tariff

Official open-data sources:

- [Location/details package](https://opendata-ajuntament.barcelona.cat/data/dataset/informacio-punts-recarrega-electric)
- [Current location JSON](https://opendata-ajuntament.barcelona.cat/data/dataset/8cdafa08-d378-4bf1-aad4-fafffe815940/resource/9febc26f-d6a7-45f2-8f73-f529ba4da930/download)
- [Live-status package](https://opendata-ajuntament.barcelona.cat/data/dataset/estat-ports-recarrega-electric)
- [Current live-status JSON](https://opendata-ajuntament.barcelona.cat/data/dataset/a2bd4c83-d024-4d78-8436-040ef996cf7f/resource/ada4c823-9566-477d-9362-7b15e7d38189/download)
- [Authentication definitions](https://opendata-ajuntament.barcelona.cat/data/dataset/autenticacions-punts-recarrega-electric)

Both packages are active, immediate-frequency, and CC BY 4.0. The evidence snapshot contained 133
public locations, 860 stations, and 1,346 ports; the latest observed port-status timestamp was
2026-07-15 14:09:46 CEST. The datasets expose coordinates/address/access, connector/power facts,
per-port status, timestamps, whether the location is on-street, and payment-required flags. Every
current port reported `payment_required=true`; that is consistent with paid use but says nothing
about the amount.

Official tariff evidence:

- [Current Endolla payment modes and tariffs](https://endolla.barcelona/ca/modalitats-de-pagament)
- [FAQ defining day/night windows](https://endolla.barcelona/es/faqs)
- [Endolla conditions of use](https://endolla.barcelona/es/condiciones-de-uso)

The real ≤€0.25 case is **€0.22/kWh** for normal charging in a BSM car park during the night window,
00:00–07:59, with an EcoVehicles (or applicable professional) subscription. The ordinary
EcoVehicles fee is €4.20/month; only the subscription fee is temporarily discounted through
2026-09-30. On-street car charging and pay-as-you-go BSM charging exceed the cap.

**Implementation boundary:** join location/station/port IDs and retain all conditions in the charge
claim: `onstreet_location=false`, BSM car park, normal charging, 00:00–07:59, eligible subscription,
and €0.22/kWh. Parking cost is explicitly separate and must stay in `parking_claim`. The source
adapter validates current observed port status and its timestamp, but the weekly generated artifact
persists station status as VERIFY and connector status as UNKNOWN. A future bounded live path would
need a separate freshness-aware runtime contract.

### Zaragoza — IMPLEMENT official location facts only

Official sources:

- [Current municipal charging page](https://www.zaragoza.es/sede/portal/movilidad/puntos-recarga)
- [2025 municipal deployment report](https://www.zaragoza.es/sede/servicio/noticia/342449)
- [Open-data Equipamientos catalogue](https://www.zaragoza.es/sede/portal/datos-abiertos/servicio/catalogo/300)
- [Open-data API documentation](https://www.zaragoza.es/sede/portal/datos-abiertos/api)
- [EV category JSON](https://www.zaragoza.es/sede/servicio/equipamiento/category/2200.json?srsname=wgs84)
- [Example structured record](https://www.zaragoza.es/sede/servicio/equipamiento/29970.json?srsname=wgs84)
- [Reuse conditions](https://www.zaragoza.es/sede/portal/aviso-legal#condiciones)

The current municipal page contains 37 locations (26 Endesa x Way and 11 Zunder), matching the
official total of 149 charging points at 37 locations. It publishes coordinates, address, operator,
connector, and power facts. Endesa rows explicitly describe payment; Zunder rows omit that field,
but Zunder's current general tariff proves that omission is not free evidence.

Zaragoza permits commercial and non-commercial reuse, extraction, and combination with source
attribution, preservation of update metadata, and no suggestion of municipal endorsement. The
category JSON appeared partial in the probe, while individual records and the municipal page held
additional entries.

**Implementation boundary:** static location/operator/connector/power corroboration only. Preserve
attribution and `lastUpdated`; expose no tariff or live claim. The adapter should compare the
catalogue/page/API populations and fail closed on an unexplained shrink rather than silently
publishing a partial city.

## Implemented order and measured result

1. **Barcelona Endolla implemented:** 43 car-charger Verify-first rows, exact
   subscription/time/location tariff condition, separate parking, source timestamp bounds, no
   persisted live availability, and explicit motorcycle-only exclusion.
2. **Madrid/Zaragoza implemented:** official location/hardware attribution is corroboration-only;
   unmatched price-unknown rows are dropped and ancient/missing OpenChargeMap price evidence cannot
   admit a match. The final canonical retains zero Madrid/Zaragoza primary rows.
3. **Red MOVEM implemented:** four exact-network ≤22 kW rows carry the dated BOP claim; higher-power
   and ambiguous rows fail closed, and the review expires after 90 days.
4. **Moraleja Green implemented:** exactly one static Verify-first free lead with two current
   first-party pages and separate parking terms.
5. **Commercial-source remediation implemented:** Consum/Electromaps are link-only and Chargemap is
   removed at adapter, runtime, cache, data, nested provenance, and public-metadata seams.
6. **Wenea remains link-only:** MyStation stays an education/handoff surface unless Volta gains
   explicit subscription-aware configuration and reuse permission.

Each new data behavior should follow the existing QA contract: non-destructive ingestion to `/tmp`,
source-family delta checks, explicit attribution/claim fixtures, a regression test for every
condition above, and no promotion of `data/free_intelligence.json` until the canary and delta are
clean.

## Limitations

- No login, private token, payment/session-start action, app interception, or authenticated surface
  was used.
- Research used official first-party pages, public APIs/files, robots files, and published terms.
- Public terms and tariffs can change. Commercial-source rights decisions and all conditional
  tariff claims need a dated recheck before implementation and during recurring refreshes.
- No nationwide official open-data feed with complete station-level price and status was found in
  this scope. Madrid/Zaragoza are corroboration sources; Barcelona is the only reviewed city source
  that combines licensed public station/status data with a current ≤€0.25 official tariff.
- A source being suitable for a static lead does not make it live, available, route-ready, or free
  to park at.
