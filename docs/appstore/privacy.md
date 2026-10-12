# Volta Privacy Notice

> **Pre-publication template. Do not publish or submit while any bracketed field remains.**
> Owner review and, where appropriate, qualified legal review are required. This draft
> describes the repository state on 2026-07-13; the deployed service must be checked again.

**Effective date:** `[EFFECTIVE DATE]`<br>
**Data controller / operator:** `[FULL LEGAL NAME]`<br>
**Address:** `[LEGAL OR BUSINESS ADDRESS]`<br>
**Privacy contact:** `[PRIVACY EMAIL OR CONTACT FORM URL]`<br>
**Support:** `https://[OWNER-DOMAIN]/support`

## 1. Scope

This notice explains how the Volta iPhone app and the Volta charger-data service
(together, “Volta”) handle information. Volta helps drivers find source-backed free and
low-cost charging in Spain. Volta does not require a Volta account.

## 2. Information handled

### Location and map area

After you grant When In Use location permission, Volta requests one location when the app
launches to load and rank nearby chargers. It requests location again when you tap the
locate control. The app sends precise latitude and longitude, or the visible map area, to
the configured Volta service. The service uses that information to return, rank, and
calculate distance to nearby charging options. If you decline permission, city/place
search remains available. Location is not requested for advertising.

### Place searches

When you search for a city or place, the app sends the search text to the Volta server.
The server relays it to the public Nominatim geocoding service and caches the normalized
query and results to answer searches efficiently. Search terms are removed from Volta's
application request logs, but infrastructure providers may have separate logs that the
operator must describe below.

### Network and operational data

The Volta server receives ordinary network data, including client IP address, request
time, and requested path. The application server strips query strings from charger and
geocode request logs, so exact coordinates, bounding boxes, and place-search text do not
appear in those application log lines. Client IP remains in the log and is also used to
apply abuse-prevention rate limits.

### App preferences

The app stores normal preferences on the device. Development builds can store a selected
test server address. Release builds use the server configured into that build. Removing
the app removes its local preferences, subject to normal device or iCloud backup behavior
controlled by Apple and the user.

### Purchases

If Volta Plus is enabled, Apple processes subscription purchases through StoreKit. Volta
checks Apple-provided verified entitlement status on the device. Volta does not receive
payment-card or bank details and does not currently send purchase history to the Volta
server. Apple's handling is governed by Apple's privacy terms.

## 3. How information is used

Volta uses the information above only to:

- return and rank nearby charger results at app launch when location permission was already
  granted, and after explicit locate or place-search actions;
- geocode a requested city or place;
- calculate distance and support routing decisions;
- operate, secure, rate-limit, debug, and maintain the service;
- provide and restore a verified subscription if Volta Plus is enabled; and
- comply with applicable legal obligations.

**Lawful basis:** `[OWNER: INSERT THE APPLICABLE BASIS FOR EACH PURPOSE AND REGION — FOR
EXAMPLE, PERFORMANCE OF A REQUESTED SERVICE, LEGITIMATE INTERESTS WITH BALANCING TEST,
CONSENT WHERE REQUIRED, OR LEGAL OBLIGATION. DO NOT PUBLISH THIS INSTRUCTION.]`

Volta does not include an advertising SDK or third-party analytics SDK. The inspected
implementation does not sell personal data or use it for cross-app advertising tracking.
The owner must confirm that the production hosting and source providers do not change
that statement.

## 4. Services and recipients

Information may be processed by:

- **Volta hosting and delivery providers:** `[HOST, REVERSE PROXY/CDN, REGIONS, LINKS TO
  PRIVACY TERMS]`;
- **Nominatim / OpenStreetMap geocoding:** place-search text is relayed by the Volta
  server; Nominatim normally sees the Volta server's outbound request, not a direct app
  request. [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/);
- **OpenFreeMap (web only):** the browser requests map styles, fonts and tiles directly from
  `tiles.openfreemap.org`, exposing its IP address and viewed map area. Native iOS continues
  to use MapKit. No account or API key is used for the web basemap.
- **Apple:** MapKit and Apple Maps provide maps/routing, and StoreKit provides purchases
  when enabled. Volta does not control data Apple independently collects for those
  services. [Apple Privacy](https://www.apple.com/legal/privacy/);
- **public charger-data sources and operators:** Volta requests charging evidence for a
  bounded area and provides links so users can verify it;
- **public image hosts:** a station detail may automatically request a charger photo
  directly from the source host, which receives ordinary network request metadata; and
- **source websites opened by the user:** tapping Source or another external handoff
  opens the destination under its own privacy terms.

Current source families are listed in `source-rights-inventory.md`. Before publication,
replace that repository reference with a public source-and-attribution page or a stable
link to one.

Apple's App Review Guideline 5.1.1(i) requires the app's privacy policy to confirm that
third parties receiving user data provide the same or equal protection described here
and required by Apple's guidelines. **Do not publish an unverified assurance.** Complete
provider due diligence for the host, reverse proxy/CDN, Nominatim, image hosts, and every
other recipient, then replace the instruction below with a truthful statement. If a
provider cannot support that statement, remove that provider or data flow before release.

`[OWNER: AFTER DOCUMENTED PROVIDER DUE DILIGENCE, INSERT THE TRUTHFUL SAME-OR-EQUAL-
PROTECTION CONFIRMATION REQUIRED BY APP REVIEW GUIDELINE 5.1.1(i). DO NOT PUBLISH THIS
INSTRUCTION.]`

## 5. Retention

The source code uses in-memory caches, but deployed infrastructure and logs may survive
for different periods. The operator must measure and publish the real production values:

| Information | Current code behavior | Production retention to publish |
| --- | --- | --- |
| Application access logs with client IP and path | Written to server stdout; charger/geocode query strings stripped | `[LOG RETENTION AND DELETION PROCESS]` |
| Geocode query/results cache | In-memory; default TTL 24 hours, configurable | `[ACTUAL TTL; BACKUP/RESTART BEHAVIOR]` |
| Live charger / map-area cache | In-memory; commonly 60 seconds, configurable | `[ACTUAL FRESH AND STALE TTL VALUES]` |
| Rate-limit identity state | In-memory operational state keyed by client identity | `[ACTUAL EXPIRY / PROCESS-LIFETIME RULE]` |
| On-device preferences | Until changed, app removed, or restored from user-controlled backup | As controlled by the user/Apple |
| StoreKit purchase records | Controlled by Apple; Volta reads current entitlement on device | See Apple's policy |

Do not replace this section with “we retain nothing.” Volta's current server design has
caches and IP-bearing logs, and the production host may add edge or platform logs.

## 6. International processing

`[OWNER: IDENTIFY THE COUNTRIES/REGIONS WHERE THE CONTROLLER, HOST, REVERSE PROXY,
NOMINATIM, AND OTHER PROCESSORS OPERATE; STATE THE APPLICABLE TRANSFER MECHANISM. DO NOT
PUBLISH THIS INSTRUCTION.]`

## 7. Your choices and rights

- You can decline location permission and search for a city or place instead.
- You can change location permission in iOS Settings.
- You can avoid loading a public photo by not opening a station detail that contains it,
  and avoid external source/route handoffs by not tapping them.
- You can manage or cancel an Apple subscription in App Store subscription settings.
- Depending on applicable law, you may request access, correction, deletion,
  restriction, portability, or objection, and may complain to a supervisory authority.

Send privacy requests to `[PRIVACY EMAIL OR CONTACT FORM URL]`. **There is no Volta
account to delete; privacy/data-rights requests remain available.** A request may require
enough information to locate relevant logs, and some data may not be identifiable or
recoverable from volatile caches.

## 8. Security

Release builds are designed to use a configured public HTTPS endpoint. Volta applies
bounded requests, rate limiting, reduced request logging, and fail-closed presentation
when source data is uncertain. No method of transmission or storage is completely secure.
Report security issues privately to `[SECURITY CONTACT]`.

## 9. Children

Volta is a driving utility and is not directed to children or listed in the App Store
Kids category. Volta does not knowingly ask children for personal information. Contact
`[PRIVACY CONTACT]` if you believe information was handled improperly.

## 10. Changes

Material changes will be posted at this URL with a new effective date. If applicable
law requires another form of notice or consent, the operator will provide it.

## 11. Contact

`[FULL LEGAL NAME]`<br>
`[POSTAL ADDRESS]`<br>
`[PRIVACY EMAIL]`<br>
`[SUPPORT URL]`
