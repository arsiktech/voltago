# App Store Connect App Privacy draft

Status: **pre-publication engineering draft; not ready for App Store Connect**. The owner
must reconcile it against the actual production topology, provider contracts, retention,
legal basis, final binary, and Apple questionnaire immediately before answering. This is
not legal advice.

Apple defines collection in relation to off-device transmission and retention, gives
specific definitions for precise location and search history, and says an IP address
must be classified according to how it is used. Apple also states that developers do not
declare data collected by Apple itself through Apple frameworks unless the developer
collects that data. See [App Privacy Details](https://developer.apple.com/app-store/app-privacy-details/)
and [manage App Privacy](https://developer.apple.com/help/app-store-connect/manage-app-information/manage-app-privacy/).

## Top-level answers

| App Store Connect question | Draft answer | Evidence / caveat |
| --- | --- | --- |
| Does the app collect data? | **Yes** | Coordinates, searches, and client IP/rate-limit identity leave the device and are retained at least in operational caches/logs under the current server design. |
| Is data used to track users? | **No** | No advertising, data broker, cross-app tracking, or third-party analytics SDK is implemented. Owner must confirm production providers do not repurpose data for tracking. |
| Tracking domains | **None** | Matches `NSPrivacyTracking=false` and an empty `NSPrivacyTrackingDomains` array. |
| Is data used for third-party advertising or developer advertising/marketing? | **No** | No ads or marketing profiling is implemented. |

Do not choose “Data Not Collected.” The server currently keeps in-memory live/geocode
caches and logs client IP addresses. Apple explicitly says retained IP addresses should
be declared under the data type that matches their use.

## Collected data types

| Apple data type | Collected | Linked to user | Tracking | Purpose | Why this conservative answer is used |
| --- | --- | --- | --- | --- | --- |
| **Precise Location** | Yes | **Yes** | No | App Functionality | After When In Use permission is granted, the app requests one location at launch to load/rank nearby chargers and requests it again when the locate control is tapped. Latitude/longitude are sent to the configured Volta service. Declining permission leaves city/place search available. Because the request reaches infrastructure that also sees client IP, treat location as linkable unless final topology proves and documents effective separation. |
| **Search History** | Yes | **No** | No | App Functionality | Place-search text is sent to the Volta server and relayed to Nominatim. The server caches normalized search queries/results; request logs strip geocode query strings. There is no Volta account or search profile. Reassess “linked” if the deployed provider correlates searches with durable identifiers. |
| **Device ID** | Yes | **Yes** | No | App Functionality | Conservative classification for client IP used as request/rate-limit identity and written to server logs. Apple says to classify IP according to use; owner must confirm this category with the final infrastructure and counsel. |

The current `PrivacyInfo.xcprivacy` mirrors this draft: all three use App Functionality,
tracking is false, Precise Location and Device ID are linked, and Search History is not.
The privacy manifest does not replace App Store Connect answers.

## Data types not currently declared

These are “No” only if the submitted binary and production service remain as inspected:

- Contact info, health/fitness, financial info, contacts, user content, browsing history,
  advertising data, usage analytics, crash/diagnostic uploads, and sensitive data.
- Purchase History: StoreKit processes transactions and the app checks Apple-provided
  entitlements on device; Volta does not currently send a purchase history to its server.
  Reassess if server-side receipt validation or account linking is later added.
- Photos/Videos: Volta downloads public charger images for display. It does not collect
  user photo-library content. The image host still receives ordinary network request
  metadata, which belongs in the hosted privacy notice and provider review.
- Product Interaction: launch timing uses Apple's signpost API locally; no analytics or
  telemetry upload is implemented.

## Recipient and flow map

| User action | Data leaving device | Immediate recipient | Further recipient / storage |
| --- | --- | --- | --- |
| App launch without location permission | App/overview request and client network metadata; no precise location | Configured Volta server and its hosting/reverse-proxy providers | Volta live/intelligence caches; access logs according to deployed policy |
| App launch after location permission has already been granted | One precise-location request, filters, and client network metadata | Configured Volta server | Nearby results are loaded/ranked; public charger sources receive bounded area requests; Volta caches may retain area keys temporarily |
| Tap locate / search this area | Precise coordinates and/or map bounding box, filters, client network metadata | Configured Volta server | Public charger sources receive bounded area/source requests; Volta caches may retain area keys temporarily |
| Place search | Search text, client network metadata | Configured Volta server | Query is relayed to Nominatim; normalized query/results are cached server-side |
| View map | Map viewport and ordinary service metadata as handled by MapKit | Apple | Governed by Apple's terms/privacy; Volta does not receive Apple's MapKit telemetry |
| Open station detail with a photo | Third-party image URL request and ordinary request metadata | Source image host | Host-specific policy; no Volta upload of user photos |
| Open Source / route | Chosen URL or coordinates and ordinary request metadata | Source website or Apple Maps | Destination's own policy applies |
| Buy/restore Volta Plus, if enabled | Apple ID/storefront transaction context handled through StoreKit | Apple | Volta reads verified entitlement state on device; no card/bank details reach Volta |

## Required owner certifications before saving answers

- [ ] Production Volta server URL, controller/operator, hosting provider, reverse proxy,
      regions, and all subprocessors are recorded.
- [ ] Actual access-log contents and retention are measured, including provider/edge
      logs outside `server.py`.
- [ ] Actual live, stale, geocode, rate-limit, and any backup retention are recorded.
- [ ] Nominatim, Apple, source image hosts, and public charger sources are reviewed for
      the production flow.
- [ ] For every third party that receives user data, the owner has documented terms,
      contracts where applicable, retention, security, and downstream use sufficient to
      make Apple's Guideline 5.1.1(i) same-or-equal-protection assurance truthfully. If
      that assurance cannot be verified, the provider/data flow is removed before release.
- [ ] Lawful bases, user-rights route, and international-transfer mechanism are chosen
      by the owner with qualified advice where needed.
- [ ] “Linked” classifications are rechecked against production infrastructure.
- [ ] No analytics, crash reporting, ads, fingerprinting, server-side receipt validation,
      account system, or new SDK was added after this draft.
- [ ] Hosted `privacy.md` matches these answers word-for-word on material behavior.
- [ ] Hosted Privacy, Terms, and Support destinations match the values embedded in the
      final archive and the links actually opened from Settings/paywall.
