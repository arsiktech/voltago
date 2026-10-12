# App Review notes draft

Status: **pre-publication template; not paste-ready or release-ready**. Resolve every
placeholder, source-method blocker, legal-link implementation/evidence gate, and
monetization assembly first. Keep the final notes aligned with the exact uploaded build.
Apple requires accurate metadata and clear access to reviewable features; see [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
and [submitting for review](https://developer.apple.com/help/app-store-connect/manage-submissions-to-app-review/overview-of-submitting-for-review).

## App Review contact

- Contact name: `[REVIEW CONTACT]`
- Email: `[REVIEW CONTACT EMAIL]`
- Telephone: `[REVIEW CONTACT TELEPHONE]`
- Demo account: **Not applicable — Volta has no account or login.**
- Backend status page/contact: `[PUBLIC STATUS OR SUPPORT URL]`
- Submitted version/build: `[FINAL MARKETING VERSION] ([FINAL BUILD > HIGHEST UPLOAD; >= 2])`
- Release service URL: `[STABLE PUBLIC HTTPS VOLTA ENDPOINT]`
- Privacy URL / `VoltaPrivacyPolicyURL`: `[FINAL PUBLIC PRIVACY URL]`
- Terms URL / `VoltaTermsURL`: `[FINAL PUBLIC TERMS URL]`
- Support URL / `VoltaSupportURL`: `[FINAL PUBLIC SUPPORT URL]`

## Copy/paste App Review assemblies

Choose **exactly one** assembly after inspecting the uploaded binary. Replace its four URL
placeholders, paste the entire selected block into App Review Notes, and omit the other
assembly. Do not concatenate a common template with a monetization snippet.

### Assembly A — submitted build with paywall disabled

Use only when `PaywallConfig.enabled` is `false` in the uploaded Release binary:

```text
Volta is an iPhone, portrait-only EV-charging decision tool for Spain. It separates current live evidence from official, directory, community, and stale evidence. A station is route-ready only when current public evidence supports it. Otherwise Volta says Check first, Verify first, Not live, or stale; Open now can intentionally return no results.

Service: [STABLE PUBLIC HTTPS VOLTA ENDPOINT]
Privacy: [FINAL PUBLIC PRIVACY URL]
Terms: [FINAL PUBLIC TERMS URL]
Support: [FINAL PUBLIC SUPPORT URL]

No login or demo account is required. Volta creates no user account or account record, so there is no account-deletion flow in the app. The Privacy and Support pages explain how to request deletion of eligible retained service data.

Suggested review path:
1. Launch. Without location permission, the national overview shows source-backed leads marked Not live. If When In Use permission was already granted, launch requests location once and loads/ranks nearby chargers.
2. Search for “València” and select a result to request live data from the Service URL above.
3. Open a station card to inspect charging and parking evidence, connector/access facts, freshness, and available public-source links.
4. Select Open now. If current route-ready evidence is absent, the empty result is deliberate; stale or directory evidence is not promoted to available.
5. Open Settings and test Privacy, Terms, and Support. Each opens its matching public HTTPS page above without login.

Location is optional. With When In Use permission already granted, Volta requests one location at launch; it requests again when Locate is tapped. Coordinates go to the configured Volta Service for nearby results and ranking. City search remains available after denial.

Volta never starts a charging session, reserves a bay, makes a provider payment, bypasses login, uses private provider tokens, or calls private mobile APIs. Permitted public HTML pages are retrieved only with GET; Gaia connector/status pages are an example. Official/open read APIs use their documented read requests: MapaREVE's public locations query is a read-only POST, never a session or other write action. Provider apps/pages, posted signs, and onsite rules remain authoritative.

Volta uses MapKit and hands routes to Apple Maps. Station detail may load a charger photo from its attributed public source host. Place searches go to the Volta Service and are relayed to Nominatim. The Privacy page explains these flows, ordinary server IP logging, and server caches. The app contains no advertising or third-party analytics SDK.

Feníe snapshots use one narrowly approved read-only marker-list POST and recorded owner-attested written redistribution permission, not independently verified here. Electromaps and Chargemap anonymous public-map snapshots are directory leads enabled by owner decision, not a third-party reuse licence or proof of Apple Guideline 5.2.2 permission. Consum locator evidence remains excluded. Source-rights certification is required before submission.

Volta Plus is disabled in this build. All charger functionality is available without purchase. StoreKit subscriptions are not an active purchase path, and the product-page metadata/screenshots make no subscription or trial claim.
```

### Assembly B — submitted build with paywall enabled

Use only after both App Store products, prices, territories, agreements, offer,
localizations, hosted legal URLs, and the uploaded Release binary have been verified:

```text
Volta is a portrait-only iPhone EV-charging decision tool for Spain. It distinguishes live, official, directory, community, and stale evidence. Only current public evidence makes a station route-ready; otherwise it says Check first, Verify first, Not live, or stale, and Open now may be empty.

Service: [STABLE PUBLIC HTTPS VOLTA ENDPOINT]
Privacy: [FINAL PUBLIC PRIVACY URL]
Terms: [FINAL PUBLIC TERMS URL]
Support: [FINAL PUBLIC SUPPORT URL]

No login or demo account is required. Volta creates no account record, so it has no in-app account-deletion flow. Privacy and Support explain deletion requests for eligible retained service data.

Suggested review path:
1. Launch without a verified entitlement to see Volta Plus. Use the App Store sandbox; no Volta credentials are needed. Test Restore Purchases, Terms, and Privacy. Terms and Privacy open their matching public HTTPS pages above without login.
2. After entitlement, the charger experience opens. Without location permission, the national overview shows source-backed Not live leads. If When In Use permission was already granted, launch requests location once and loads/ranks nearby chargers.
3. Search “València” to request live data from the Service URL, then open a card to inspect charging/parking evidence, connector/access facts, freshness, and public-source links.
4. Select Open now. With no current route-ready evidence, the empty result is deliberate; stale/directory data is not promoted to available.
5. Open Settings and test Privacy, Terms, and Support. Each opens its matching public HTTPS page above without login.

Location is optional. When permission is granted, Locate requests it again when tapped. Coordinates go to the configured Service for nearby results/ranking; city search remains after denial.

Volta never starts charging, reserves a bay, pays a provider, bypasses login, uses private tokens, or calls private mobile APIs. Permitted public HTML pages use GET only, including Gaia status pages. Official/open read APIs use documented read requests: MapaREVE's public locations query is a read-only POST, never a session/write action. Provider apps/pages, signs, and onsite rules remain authoritative.

Volta uses MapKit and Apple Maps route handoff. Detail may load a photo from its attributed public host. Place searches go through the Service to Nominatim. Privacy explains these flows and server IP logs/caches. There are no advertising or third-party analytics SDKs.

Feníe snapshots use one narrowly approved read-only marker-list POST and recorded owner-attested written redistribution permission, not independently verified here. Electromaps and Chargemap anonymous public-map snapshots are directory leads enabled by owner decision, not a third-party reuse licence or proof of Apple Guideline 5.2.2 permission. Consum locator evidence remains excluded. Source-rights certification is required before submission.

Volta Plus has two auto-renewable products in one group: volta.plus.monthly (one month) and volta.plus.yearly (one year). StoreKit supplies localized price, period, offer eligibility, and duration; production prices are not hardcoded. The paywall includes Restore Purchases, Terms, Privacy, and renewal/cancellation disclosure. Entitlement follows the purchasing Apple ID. If it changes during a route, that route remains usable until the next cold launch to avoid stranding the driver.
```

### Assembly-size verification

The final App Review Notes field is limited to **4,000 UTF-8 bytes**, not 4,000 Unicode
characters. Recompute the selected block after inserting the real URLs. For repeatable
preflight measurement only,
this document uses these realistic-length dummy substitutions (they are **not approved
release destinations**):

- service: `https://api.volta-carga-clara.example`
- privacy: `https://www.volta-carga-clara.example/legal/privacy`
- terms: `https://www.volta-carga-clara.example/legal/terms`
- support: `https://www.volta-carga-clara.example/help/support`

The measured results below include all text inside each fence, its internal newlines, and
one terminal newline to model a normal paste; the Markdown fence markers are not pasted or
counted. Each assembly retains at least 500 UTF-8 bytes of headroom for final URL-length
differences or reviewer-specific additions:

| Exact assembly after substitutions | UTF-8 bytes | Byte headroom below 4,000 |
|---|---:|---:|
| A — paywall disabled | 3,340 | 660 |
| B — paywall enabled | 3,482 | 518 |

### Feníe snapshot — current implementation state

Feníe's station listing uses an anonymous public-map session and the narrow read-only
marker-list POST exception approved on 2026-10-06. Its adapter is active. The recorded
owner-attested written redistribution permission of 2026-07-18 is retained off-repo and
not independently verified here; it remains the source-policy revocation gate. Records
are cached snapshots, never live availability. No Feníe media is copied.

## Public-source provenance summary

The engineering boundary and the rights boundary are separate:

- **Technical/ethical boundary:** public/open, user-verifiable, read-only station evidence;
  no login bypass, private token extraction, MITM, provider payment, charging-session start,
  or app-private scraping.
- **Presentation boundary:** live, official, directory, community, and stale confidence
  remain distinct; charging-price and parking provenance remain separate.
- **Rights boundary:** public accessibility does not itself grant database, commercial,
  caching, photo, or redistribution rights. The owner must certify every active source in
  `source-rights-inventory.md`, preserve required attribution, obtain permission where
  needed, or remove/link-only the affected content before submission.

Active source families include MapaREVE public OCPI, Gaia public pages, DGT NAP corroboration,
OpenChargeMap, OpenStreetMap, València and other municipal open data, and narrow official
retailer/policy material. Feníe snapshots are enabled under the recorded owner-attested
permission and narrow POST exception. Electromaps and Chargemap anonymous public-map
snapshots are enabled by owner decision dated 2026-10-12 as non-live directory leads.
Consum locator evidence remains excluded. The owner decision does not establish third-party
reuse rights or Apple Guideline 5.2.2 permission. The app provides source links and keeps
provider/onsite information authoritative.

## Review attachments checklist

- [ ] App Review contact is reachable during review.
- [ ] Stable HTTPS endpoint passes `/api/health`, overview, València search, and live fetch.
- [ ] Legal URLs are public and contain no placeholders.
- [ ] `VOLTA_PRIVACY_POLICY_URL`, `VOLTA_TERMS_URL`, and `VOLTA_SUPPORT_URL` resolve into
      `VoltaPrivacyPolicyURL`, `VoltaTermsURL`, and `VoltaSupportURL` in the final archive;
      installed Settings Privacy/Terms/Support and, if enabled, paywall Terms/Privacy
      were all tapped and opened their matching public 200/no-login/no-placeholder
      destinations.
- [ ] Feníe's exact bundled/served snapshot matches the recorded permission and narrow POST
      exception; it is not presented as live and contains no copied Feníe media.
- [ ] Electromaps/Chargemap source-rights gaps, including Apple 5.2.2 permission, are resolved
      for the exact submitted revision; owner collection policy is not third-party permission.
- [ ] Source-rights owner certification is complete and retained internally.
- [ ] Correct complete assembly pasted; the other assembly omitted.
- [ ] If enabled, both IAPs are attached to the submission with localizations and a real
      review screenshot from the submitted build.
- [ ] No private host, Tailscale address, localhost URL, secret, or test credential appears.
- [ ] Any unusual fail-closed behavior likely to look like a bug is explained above.
