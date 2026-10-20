# Source, attribution, and content-rights inventory

Status: **owner certification required before public distribution or monetization**.
This inventory is not a legal opinion and does not infer permission from technical access.

Collection-policy update 2026-10-20: the owner authorized bounded, anonymous public
GET collection from Repsol, Electromaps and Chargemap for Spain-only additive price
annotations on existing rows. The Electromaps and Chargemap station-evidence exclusions
remain enforced. Only their exact corresponding `price_evidence` kinds are exempt
from their own exclusion's origin check. Other excluded origins inside annotations
still block the row. The owner decision is **not a provider licence or rights clearance**.
Consum remains excluded without an annotation exception. See
[the price-evidence contract](../price-evidence.md) for scope and budgets.

The 2026-07-15 research remains a dated rights finding, not the owner's certification.
The owner must separately review provider reuse/redistribution rights and
[Apple App Store Guideline 5.2.2](https://developer.apple.com/app-store/review/guidelines/#intellectual-property)
before submitting a build that uses third-party source content. Anonymous access and an
owner policy record do not establish the provider permission that Apple may request.

Volta's public/read-only collection boundary protects users and providers from private
API or transaction abuse. It does **not** answer whether a database, page, status field,
tariff, image, or derived record may be cached, combined, republished, or used in a paid
product. Each active source needs a dated rights decision supported by its official
license, terms, written permission, statutory basis, or a link-only design.

## Certification standard

For every row, retain evidence that answers:

1. What exact official URL, API, dataset, page, or export is used?
2. Is automated access allowed, and under what rate/caching limits?
3. May factual fields be copied, transformed, combined, and redistributed?
4. Does commercial use or subscription access change the permission?
5. Are database rights, copyright, attribution, share-alike, or notice obligations triggered?
6. Are photos/logos/marks covered separately from factual station data?
7. Are live status/tariff fields permitted to be cached, and for how long?
8. Is a source link sufficient, or is explicit attribution text/logo placement required?
9. Has the source changed its terms or technical surface since the evidence date?
10. What is the removal path if permission is uncertain or revoked?

Acceptable owner decisions are: `APPROVED WITH CONDITIONS`, `WRITTEN PERMISSION`,
`LINK-ONLY`, `REMOVE BEFORE RELEASE`, or `COUNSEL REVIEW`. “Public website” and
“robots.txt allows it” are not rights decisions.

## Current source inventory

| Source/family | Current technical use | Content used | Rights/terms evidence | Photo/media right | Required attribution/caching terms | Owner decision, initials, date |
| --- | --- | --- | --- | --- | --- | --- |
| MapaREVE | Public OCPI/web API; live bounded-area fetch | Station identity, coordinates, tariff/status/connectors/operator fields | **UNVERIFIED FOR APP REDISTRIBUTION/COMMERCIAL USE** — review [MapaREVE](https://www.mapareve.es/), endpoint terms, OCPI producer rights, and operator-field provenance | N/A unless media appears | `[FILL]` | `[REQUIRED]` |
| Gaia Charge | Public start-session HTML pages; GET-only parsing; no session start or payment | Seeded station, connector status, tariff/session-display facts | **UNVERIFIED** — public availability is not permission; request written API/export permission if terms do not authorize reuse | N/A in current direct parser unless added later | `[FILL]` | `[REQUIRED]` |
| Feníe Energía Recarga | **Permitted (owner-attested) offline snapshot, 2026-10-06.** Anonymous GET of the public map for a session/CSRF value, then one read-only marker-list POST; the owner approved that single POST as a narrow exception enforced by `scripts/source_transport.py` (exact host/path, empty body, no redirects, bounded budget). Only Feníe's own `gratuito=1`, in-service, non-test stations are ingested; rows are cached snapshots, never live | Station identity, coordinates, connectors, free flag, parking count from Feníe's own public map (no status as live) | Owner attested on 2026-07-18 to written redistribution permission retained off-repo (not independently verified in this workspace); the request-method block was lifted by the owner on 2026-10-06. The permission record in `data/source_policy_exclusions.json` `granted_reuse` is the revocation gate. | No Feníe photos/logos are copied | Attribution `Feníe Energía — Red de recarga (public map)` is stamped on each row; weekly snapshot, no live status inferred; withdrawal restores the exclusion record | `OWNER-ATTESTED PERMISSION + READ-ONLY POST EXCEPTION 2026-10-06; RE-CERTIFY BEFORE RELEASE` |
| Repsol published network tariff | **Active additive price-only collection, Spain only.** One anonymous public tariff-page GET each build | Full published normal/fast/ultrafast guide on rows with exact registry operator REPSOL SOLUCIONES ENERGÉTICAS SA; fast is **from** €0.48/kWh and stations may differ; no invented kW bands | **OWNER COLLECTION POLICY, 2026-10-20; NOT A PROVIDER LICENCE.** [Repsol legal notice](https://www.repsol.es/particulares/aviso-legal/) restricts extraction/reuse. No provider permission was established by this activation | No media is copied | Revalidate the complete contextual table and station caveat on every build; withhold only this annotation if the page is unavailable or changed; no station-price verification | `ACTIVE NETWORK GUIDE; PROVIDER RIGHTS + APPLE 5.2.2 REVIEW OPEN` |
| Electromaps | **Active additive price-only collection, bounded Spain pilot.** Anonymous GET list/detail via `source_transport`; historical pin/live/photo adapters stay disabled | Dated community `charge_price` and separate parking metadata on existing rows; no copied usernames, photos or reviews; no primary/free/live authority | **OWNER COLLECTION POLICY, 2026-10-20; NOT A PROVIDER LICENCE.** [Published terms](https://www.electromaps.com/es/terms-and-conditions) reserve sharing/reuse rights. Station evidence remains excluded; only `price_evidence.kind=electromaps_community` has a collection exception. Provider permission remains unverified | No media is copied | Preserve the source update date, fetch date, raw tariff text and uncertainty. Withhold claims without a valid source date. Review caching, transformation, redistribution and commercial-use basis separately | `ACTIVE PRICE-ONLY COLLECTION; PROVIDER RIGHTS + APPLE 5.2.2 REVIEW OPEN` |
| Chargemap | **Active additive price-only collection, bounded Spain pilot.** Anonymous GET map list and pool-price HTML via `source_transport`; historical pin adapter stays disabled | Explicit **Chargemap Pass** payment-route tariff and billing/idle-fee terms on existing rows; no direct-operator price or primary/free/live authority | **OWNER COLLECTION POLICY, 2026-10-20; NOT A PROVIDER LICENCE.** [Current terms](https://community-optins.chargemap.com/conditions/3/document?locale=es_ES) reserve extraction/reuse rights. Station evidence remains excluded; only `price_evidence.kind=chargemap_pass` has a collection exception. Public readability does not grant redistribution permission | No media is copied | Preserve per-started-kWh billing, idle trigger/grace, numeric fees, source/fetch dates and tariff text. List `is_free` cannot create a free claim | `ACTIVE PRICE-ONLY COLLECTION; PROVIDER RIGHTS + APPLE 5.2.2 REVIEW OPEN` |
| OpenChargeMap | Public snapshot/API-derived intelligence | Station/location/cost/status/connectors and possible photos | **UNVERIFIED** — capture the exact current [OpenChargeMap](https://openchargemap.org/site/develop/api) license/attribution/API conditions and upstream contributor terms | Verify each photo license/author/attribution; do not assume the station-data license covers media | `[FILL]` | `[REQUIRED]` |
| OpenStreetMap | Public-tag snapshot/intelligence | Station/location/tags | **UNVERIFIED IN THIS KIT** — capture current [OSM copyright and license](https://www.openstreetmap.org/copyright), database/Produced Work analysis, attribution, and share-alike decision | N/A unless linked media is later used | `[FILL]` | `[REQUIRED]` |
| València OpenData | Public municipal dataset | Station/location/public infrastructure facts | **UNVERIFIED** — record exact dataset page, publisher, version, license, attribution, update date, and commercial-use permission | N/A unless dataset includes media | `[FILL]` | `[REQUIRED]` |
| Lidl official free list/policy | Official public retailer material | Store/charging-policy leads | **UNVERIFIED** — identify the exact list/policy URL, scope, freshness, marks, and reuse permission | Do not use retailer photos/logos without separate permission | `[FILL]` | `[REQUIRED]` |
| Barcelona Endolla open data + tariff/FAQ | Weekly GET of the official location/status JSON plus current tariff and FAQ pages; 43 static Verify-first car-charger rows | Location/address/access/connector/power facts; observed status retained as provenance only; conditional €0.22/kWh B:SM normal/night/subscription claim | Location/status packages state **CC BY 4.0**; record package/resource versions and attribution. Tariff/FAQ wording is exact-checked and fails closed after seven days, but page access is not by itself certification for redistribution/commercial use. | No Endolla photos/logos are copied | Attribute Ajuntament de Barcelona/Endolla and CC BY 4.0; preserve source/update timestamps and every subscription/time/location condition; review tariff/FAQ caching and commercial-use basis | `[REQUIRED: APPROVED WITH CONDITIONS OR COUNSEL REVIEW]` |
| Madrid public charging open data | Monthly GET of the official CSV/catalogue; corroboration-only identity merge | Location/address/operator/equipment/power/update facts; no tariff or live status | Catalogue identifies **CC BY 4.0**; retain exact dataset version, publisher, attribution, and transformation note for ETRS89/UTM30N conversion | No media is used | Attribute Ayuntamiento de Madrid and CC BY 4.0; retain update metadata; do not admit unmatched price-unknown rows | `[REQUIRED: APPROVED WITH CONDITIONS]` |
| Zaragoza municipal charging facts | GET of the official municipal charging page/reuse terms; corroboration-only merge with population-shrink guard | Location/address/operator/connector/power/update facts; no tariff or live status | Municipal reuse conditions permit commercial/non-commercial reuse, extraction, and combination with attribution, update metadata, and no implied endorsement; retain a dated copy/reference | No media is used | Attribute Ayuntamiento de Zaragoza, retain update date, and re-check reuse terms; fail closed on unexplained population shrink | `[REQUIRED: APPROVED WITH CONDITIONS]` |
| Red MOVEM public-price regulation | Dated manual tariff review attached only to independently reusable, exact-network ≤22 kW locations | Official €0.25/kWh public-price claim, regulation URL, current-network confirmation, and 90-day review timestamp; no live status | BOP 129/2024 and current Diputación notices are official public records. The implementation does not bulk-copy the network site; owner must document the lawful reuse/quotation basis and any public-sector attribution obligations. | No media is used | Cite Diputación de Badajoz/BOP; expire the claim after 90 days; remove it above 22 kW or when identity is ambiguous | `[REQUIRED: APPROVED WITH CONDITIONS OR COUNSEL REVIEW]` |
| ACCIONA Moraleja Green pilot | Two current first-party GET pages support exactly one static Verify-first lead | Free/RFID/information-desk charging statement plus venue identity/address/parking context; parking remains a separate claim | First-party pages establish current facts but provide no open dataset licence. The nationwide ACCIONA platform is not ingested; owner must document the narrow factual-use/linking basis or remove the lead. | No ACCIONA/venue media or logos are copied | Link both first-party pages, preserve the oldest source timestamp, keep the row non-live, and re-check both pages before release | `[REQUIRED: APPROVED WITH CONDITIONS, WRITTEN PERMISSION, LINK-ONLY, OR REMOVE]` |
| Consum store locator | **Link-only in this build.** The historical Valencia adapter is disabled; structured source-origin policy excludes direct and nested locator evidence | Consum homepage label/link only; zero store-location/lead/corroboration/media records | **2026-07-15 RESEARCH VERDICT IMPLEMENTED: OWNER-LEGAL-DECISION / written permission.** The locator is technically reachable, but the legal notice does not supply an open reuse grant. | None from Consum; retailer photos/logos remain prohibited without separate permission | Conservative fallback is the Consum home page only; any locator ingestion requires permission covering retrieval, caching, transformation, redistribution, attribution, and link scope | `LINK-ONLY IMPLEMENTED 2026-07-15; WRITTEN PERMISSION + SEPARATE ADAPTER/MEDIA REVIEW REQUIRED TO REINTRODUCE` |
| Other operator/CPO fields included inside an upstream | Indirect via MapaREVE/directory/open data | Operator name, source link, station facts | Upstream access does not automatically settle each operator's underlying rights; document the chain or limit to facts/source link | Separate review | `[FILL]` | `[REQUIRED]` |
| Public charger photos as a class | Photo metadata may remain in the intelligence record, but web and iOS display/automatic loading are fail-closed unless that exact item has `display_rights_approved=true` | Photo URL, thumbnail, caption, source label | **NO CLASS-WIDE APPROVAL** — current canonical items have no display approval and therefore are withheld; each future approval needs license, attribution, hotlink/caching permission, and a takedown route | This row exists because media rights differ from factual data rights | `[FILL]` | `WITHHELD BY DEFAULT IN CODE; PER-ITEM OWNER DECISION REQUIRED` |
| Nominatim / OpenStreetMap geocoding | Server relays place searches and caches results | Search result label, coordinates, bounding box | Review [Nominatim policy](https://operations.osmfoundation.org/policies/nominatim/), attribution, caching, contact User-Agent, public-service capacity, and production provider choice | N/A | `[FILL]` | `[REQUIRED]` |
| Apple MapKit / Apple Maps | Native map display and user-requested route handoff | Apple map/route service output | Covered only under the owner's Apple agreements and current [Apple Maps terms](https://www.apple.com/legal/internet-services/maps/terms-en.html); do not export Apple content into the intelligence DB | Apple-controlled | `[FILL]` | `[REQUIRED]` |

## Release-remediation rules

- Feníe's request-method exclusion was lifted on 2026-10-06 by the owner's narrow read-only
  marker-list POST exception; the owner-attested redistribution grant is the active rights
  basis and the revocation gate (remove it and restore the exclusion to turn Feníe off).
  Public-facing submission text (review notes, release runbook, goal status) still describes
  Feníe as link-only until a Feníe-bearing data promotion is actually made.
- The 2026-10-20 owner policy activates only bounded Spain price annotations for Repsol,
  Electromaps and Chargemap. It does not activate historical directory-pin, live-refresh,
  photo or review adapters. Consum remains link-only and policy-excluded.
- Provider rights and Apple Guideline 5.2.2 review remain open for these annotations.
  Record the actual reviewed source/data revision before public distribution. Do not label
  this owner collection policy as a provider-granted licence or a completed release gate.
- If factual-data reuse is uncertain, prefer a user-verifiable **link-only lead** with no
  copied protected text/media, or remove the source until permission is documented.
- If photo rights are uncertain, omit the photo even if the station facts remain usable.
  Volta enforces this in both clients: missing/false `display_rights_approved` suppresses
  automatic loading; the web may retain a user-initiated link to the source record, never the
  unapproved raw image URL.
- If commercial/subscription use is not permitted, do not place the source-derived content
  behind Volta Plus; obtain permission, redesign the free/paid split, or remove it.
- If attribution/share-alike is required, implement and visually verify it before release;
  a private spreadsheet note is not user-facing compliance.
- Preserve a dated copy or hash/reference to the official license/permission and record the
  affected code/data paths. Re-certify after a material terms, endpoint, or product change.

## Owner certification

I have reviewed every active source and media path in the submitted build and its bundled
data. I have either documented an applicable permission/license and complied with its
conditions, obtained written permission, limited the source to a lawful link-only use, or
removed it. I understand that public accessibility alone is not a reuse license.

- Legal/operator name: `[REQUIRED]`
- Certifier name and role: `[REQUIRED]`
- Submitted source/data revision: `[GIT COMMIT AND DATA GENERATED_AT]`
- Date: `[REQUIRED]`
- Signature/record location: `[REQUIRED — KEEP OUT OF PUBLIC REPO IF IT CONTAINS PRIVATE DATA]`
