# Source, attribution, and content-rights inventory

Status: **owner certification required before public distribution or monetization**.
This inventory is not a legal opinion and does not infer permission from technical access.

Research update 2026-07-15: `docs/source-research-2026-07-15-national.md` records current
official URLs, access observations, and conservative product verdicts. It is evidence for the
owner's decision, **not** the owner's certification. Plan 011 implemented the conservative
verdicts: Electromaps and Consum are link-only, Chargemap is removed, and the canonical database
contains no direct or nested station/claim/media evidence from those families. Reintroduction
requires written permission plus a new source-policy and adapter review.

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
| Electromaps | **Link-only in this build.** Direct ingestion/live-refresh adapters are disabled; structured source-origin policy excludes direct and nested evidence | Conventional homepage/source attribution only; zero station/location/price/status/connector/media records | **2026-07-15 RESEARCH VERDICT IMPLEMENTED: LINK-ONLY pending express permission.** Current terms reserve exploitation/reuse rights; reachable endpoints/robots rules are not a reuse grant. | None from Electromaps | Preserve only conventional external links; reintroduce data/media only with express written permission covering caching, transformation, and redistribution | `LINK-ONLY IMPLEMENTED 2026-07-15; WRITTEN PERMISSION + SEPARATE ADAPTER/MEDIA REVIEW REQUIRED TO REINTRODUCE` |
| Chargemap | **Removed in this build.** Direct ingestion is disabled and structured source-origin policy excludes direct and nested evidence | No station/location/status/connector/media records; zero retained evidence | **2026-07-15 RESEARCH VERDICT IMPLEMENTED: SKIP without written API/data permission.** Generic robots rules disallow crawling and the current CGU withholds extraction/reuse absent authorization. | None from Chargemap | No extraction or copied content; a future adapter requires written permission and a fresh GET-only/source-policy review | `REMOVED 2026-07-15; WRITTEN PERMISSION REQUIRED TO REINTRODUCE` |
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
- Electromaps and Consum remediation is implemented as link-only, and Chargemap is removed.
  Their adapters, runtime promotion, cached/assembled responses, generated records, nested
  claims/media/connectors, and public source metadata are policy-partitioned. The canonical
  Plan 011 database has zero retained evidence from all three families.
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
