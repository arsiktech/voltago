# Volta Go App Store release runbook

> **TestFlight first:** for the shorter free friends-and-family path, use
> `docs/appstore/testflight-fast-track.md`. It stops at a processed, tester-visible build and
> deliberately does not wait for App Store screenshots, subscriptions, DSA/storefront work,
> TC-051, or TC-056. This full runbook remains the public App Store release contract.

Status: **pre-publication draft; not release-ready and not executed**. Required code
revisions, archive/link evidence, source remediation, owner decisions, public
infrastructure, signing, human tests, and App Store Connect work remain open. This
runbook does not authorize deployment or submission.

Current local identity to recheck at release time:

- Bundle ID: `tech.arsik.volta`
- Marketing version and lower-bound build: use `docs/appstore/testflight-state.json` and verify
  they match the final Xcode build settings
- Next upload build: it must be strictly greater than the current App Store Connect maximum and
  the last known Internal Testing assignment
- Target: iPhone only, portrait only
- Release endpoint and Privacy/Terms/Support links: final public HTTPS values must be
  supplied through strict, fail-closed Release build settings and verified in the archive
- Paywall: default off until the owner completes the explicit subscription gate

Apple references:

- [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [Create an app record](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-a-new-app/)
- [App information fields](https://developer.apple.com/help/app-store-connect/reference/app-information/app-information)
- [Platform-version metadata](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information)
- [App Privacy Details](https://developer.apple.com/app-store/app-privacy-details/)
- [Screenshot specifications](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications)
- [Export compliance](https://developer.apple.com/help/app-store-connect/manage-app-information/overview-of-export-compliance)
- [Age rating](https://developer.apple.com/help/app-store-connect/manage-app-information/set-an-app-age-rating/)
- [DSA trader requirements](https://developer.apple.com/help/app-store-connect/manage-compliance-information/manage-european-union-digital-services-act-trader-requirements/)
- [Auto-renewable subscriptions](https://developer.apple.com/help/app-store-connect/manage-subscriptions/offer-auto-renewable-subscriptions/)
- [Submit for review](https://developer.apple.com/help/app-store-connect/manage-submissions-to-app-review/overview-of-submitting-for-review)

## 1. Documentation drafted locally

- Release endpoint and hosted-link implementation targets, including their fail-closed
  archive checks.
- Release privacy/location wording, including the automatic one-location request at app
  launch after permission is granted and another request on locate tap.
- en/es/ru metadata, conservative App Privacy, Privacy/Support/Terms, App Review,
  source-rights, compliance, subscription, and screenshot templates.
- A final-submission placeholder/instruction gate in `docs/appstore/README.md`.

Drafted documentation is not implementation proof, legal approval, submission readiness,
or permission to perform an external action.

## 2. Owner gates before any release work

Stop until the owner supplies or decides all items that affect the release:

- [ ] Final legal/operator identity, postal address, support contact, privacy contact,
      security contact, and honest support response window.
- [ ] Stable public HTTPS endpoint and hosting/reverse-proxy topology.
- [ ] Stable public HTTPS Privacy, Terms, and Support destinations; final values for
      `VOLTA_PRIVACY_POLICY_URL`, `VOLTA_TERMS_URL`, and `VOLTA_SUPPORT_URL`.
- [ ] Actual production cache/log/backups retention, subprocessors, processing regions,
      international-transfer position, lawful bases, and data-rights procedure.
- [ ] Content/database/photo rights and attribution for every active source.
- [ ] Confirm Feníe collection stays within the owner-approved read-only marker query
      recorded on 2026-10-06 in `data/source_policy_exclusions.json` and enforced by
      `scripts/source_transport.py`. Retain required attribution and directory-only
      evidence semantics; verify the recorded permission still covers the release.
- [ ] Provider privacy due diligence sufficient to truthfully make Apple's Guideline
      5.1.1(i) same-or-equal-protection assurance; otherwise remove that provider/flow.
- [ ] App name, storefronts, category, DSA trader status, and availability.
- [ ] Free-versus-paid behavior, subscription prices, territories, trial, and tax category.
- [ ] Apple agreements, banking/tax, signing team, and App Store Connect access.
- [ ] Hosted Privacy, Support, and Terms pages.
- [ ] Final App Store name aligned/localized with `CFBundleDisplayName` and the installed
      home-screen label.

Credentials, payment details, private signing material, and legal identity do not belong
in this repository or command output.

### Authoritative execution sequence

The detailed sections below are evidence/reference cards. Execute them in this order:

1. Reconcile the existing Apple app/build/product records read-only so the next build
   number is known; do not create anything duplicate (Section 3).
2. Freeze a clean local candidate and run non-Release repository/native verification;
   do not claim a Release/archive gate before final public URLs exist (Section 7).
3. Close operator, privacy/provider, DSA, remaining source/content-rights, name/trademark,
   installed display-name, agreements,
   pricing/free-split, and localization decisions (Sections 2, 4, and 9).
4. Activate and verify the production service, then host and verify final legal/support
   URLs (Sections 5–6).
5. Build Release with the final service plus Privacy/Terms/Support settings; archive,
   inspect, install, tap Settings Privacy/Terms/Support and, if the paywall is enabled,
   paywall Terms/Privacy, and confirm each matching final destination. Then capture
   truthful screenshots (Sections 7–10).
6. Enter ASC name/metadata/App Privacy/trader/content-rights result/export/age rating/
   subscription information (Section 11).
7. Sign, Organizer-validate, and upload a newly incremented build (Section 12).
8. Complete TestFlight, then manual TC-051 and TC-056 (Sections 13–14).
9. Perform the final consistency audit, submit for review, and release only with owner
   approval (Section 15).

If a later step changes code, endpoint, privacy behavior, source set, price/trial, or
metadata-visible behavior, loop back to Step 1 with a higher build. Do not carry forward
evidence from a materially different binary.

## 3. Reconcile the existing Apple records first

This is an owner action in App Store Connect.

1. Sign in to App Store Connect → Apps.
2. Search for the existing Volta Go app and verify the bundle ID `tech.arsik.volta`.
3. Check TestFlight and Distribution for previously uploaded builds, especially build 1.
4. Check Monetization → Subscriptions for group `Volta Plus` and product IDs
   `volta.plus.monthly` / `volta.plus.yearly`.
5. **Do not create another app or duplicate products** if the record already exists.
6. If no app exists, confirm the bundle/App ID and final available name before following
   Apple's “Add a new app” flow. A local project name does not reserve an App Store name.
7. Record the highest uploaded build. The next build must be strictly greater and at
   least 2.

Choose one final name from `docs/appstore/metadata.md` and test it in the existing record:

1. Volta Carga Clara
2. Carga Clara España
3. Ruta de Carga España

If unavailable, move to the next candidate. Recheck localized keywords after selection.
App Store availability does not clear trademark or unfair-competition risk; complete the
owner's appropriate legal/name review as a separate gate.

The selected App Store name must also align with localized `CFBundleDisplayName` values
and the label visible under the installed icon in each supported locale. A metadata-only
rename is incomplete: update the implementation, rebuild, archive, install, and recapture
screenshots before proceeding.

## 4. Close business, legal, privacy, and source gates

Owner actions:

1. Complete any required Apple Developer/App Store agreements.
2. If paid subscriptions will ship, complete the Paid Apps agreement, tax, banking, and
   required business verification before configuring sale.
3. Make and record the DSA trader-status self-assessment. Apple cannot make it for you.
4. Complete `docs/appstore/source-rights-inventory.md`. Obtain permission, comply with
   licenses/attribution, change a source to link-only, or remove it. Treat photos separately.
5. **Feníe technical gate — bounded query approved 2026-10-06.** The anonymous public map's
   marker-list POST is an owner-approved read-only exception. Keep its exact host, path,
   empty body, redirect prohibition and request budget enforced by `scripts/source_transport.py`.
   Check the recorded reuse permission, attribution and revocation conditions in
   `data/source_policy_exclusions.json`; never present stored markers as runtime-live availability.
6. Fill every bracketed field in `privacy.md`, `support.md`, and `terms.md`; obtain
   qualified review where appropriate.
7. Complete documented due diligence for every third party receiving user data—including
   host, reverse proxy/CDN, Nominatim, and applicable image/source providers. Publish the
   Guideline 5.1.1(i) same-or-equal-protection assurance only when it is true and supported;
   otherwise remove the provider/flow.
8. Decide whether Apple's standard EULA or an approved custom EULA will apply; ensure web
   terms do not conflict.
9. Approve Spanish and Russian copy with native speakers.
10. Decide whether the hosted Privacy, Support, and Terms pages require Spanish and/or
   Russian versions for the chosen storefronts and users; the templates are English.
11. Select actual service, support, privacy, terms, and optional marketing URLs.

Before hosting, export every final publication/submission artifact and run the multiline
placeholder/instruction command in `docs/appstore/README.md`. The previous one-line bracket
grep is insufficient. Expected: no output and exit 0, followed by manual review.

## 5. Prepare and verify the production service

This section requires owner authorization because it activates or changes a public
external system.

1. Choose the deploy topology from `DEPLOY.md`. Account for the documented datacenter-IP
   limitation on public charger sources.
2. Configure a stable HTTPS hostname. A random/temporary tunnel is not an App Store
   production endpoint.
3. Configure a real `VOLTA_CONTACT` for Nominatim policy compliance.
4. If behind a reverse proxy you control, configure `VOLTA_TRUST_PROXY=true`; do not enable
   it on an untrusted raw bind.
5. Set rate limits, CORS, CSP, cache budgets, logs, backups, alerts, and retention to the
   values disclosed in the hosted privacy notice.
6. Confirm the service and its providers do not retain full charger/geocode query strings
   outside the application log sanitizer unless that retention is disclosed and justified.
7. Keep the HTTPS endpoint responsive throughout App Review.

Set local variables to the final values, with no trailing `/` on the service origin:

```bash
RELEASE_URL='https://[REAL-PUBLIC-HOST]'
PRIVACY_URL='https://[OWNER-DOMAIN]/privacy'
TERMS_URL='https://[OWNER-DOMAIN]/terms'
SUPPORT_URL='https://[OWNER-DOMAIN]/support'
```

Verify from a network outside the host:

```bash
curl -fsS "$RELEASE_URL/api/health"
curl -fsS "$RELEASE_URL/api/chargers?overview=true&include_leads=true&max_price=0.25"
curl -fsS --get --data-urlencode 'q=València' "$RELEASE_URL/api/geocode"
curl -fsS "$RELEASE_URL/api/source-health"
```

Then perform real iPhone checks for launch, city search, near-me, live refresh, source
handoff, photo loading, Apple Maps route, degraded coverage, stale/failure behavior, and
Open now fail-closed behavior. Do not release if a cloud IP reduces the service to a
misleading product experience.

## 6. Host and verify legal/support pages

Host the completed files at stable HTTPS URLs under the owner-controlled domain:

- `docs/appstore/privacy.md` → `https://[OWNER-DOMAIN]/privacy`
- `docs/appstore/support.md` → `https://[OWNER-DOMAIN]/support`
- `docs/appstore/terms.md` → `https://[OWNER-DOMAIN]/terms`

Verify without authentication in a private browser window and on iPhone:

```bash
for url in "$PRIVACY_URL" "$TERMS_URL" "$SUPPORT_URL"; do
  result="$(curl -fsSL --max-redirs 5 -o /dev/null \
    -w '%{http_code} %{url_effective}' "$url")" || exit 1
  code="${result%% *}"
  effective="${result#* }"
  [ "$code" = 200 ] && [ "$effective" = "$url" ] || {
    printf 'BLOCKED: expected 200 %s; got %s\n' "$url" "$result"; exit 1;
  }
  printf 'PASS: %s\n' "$result"
done
```

Requirements:

- unauthenticated 200 response after a bounded, sensible redirect chain;
- each configured variable is already the final canonical URL, so the effective
  destination equals it exactly and is never a login/interstitial;
- readable on mobile and with large text;
- no placeholders, draft banners, repository-only instructions, or test identities;
- Support includes real contact information;
- Privacy exactly matches the production topology and App Privacy answers; and
- links remain stable for the lifetime of the release.

Run the `docs/appstore/README.md` placeholder/instruction gate over the exact rendered or
exported pages before this link check and again before App Store Connect entry.

## 7. Freeze and verify the release candidate

Start from the exact commit intended for archive. Preserve unrelated owner work.

```bash
git status --short
git rev-parse HEAD
git log -1 --oneline
```

Require a clean release-candidate tree. Do not use `git reset --hard`, discard unrelated
changes, or archive a mixture of uncommitted work.

Run the complete repo gates:

```bash
./scripts/verify.sh --full
```

With a built SPA and a running local server on a fresh port, run the full regression suite
including browser contracts and external canaries:

```bash
python3 scripts/qa_regression_suite.py \
  --base-url http://127.0.0.1:8773 \
  --include-external
```

Expected: all 30 checks run and pass. If browser or external checks are skipped, record
the skip explicitly; do not call that a full green suite.

Run the native tests on iOS 27, serially:

```bash
xcodebuild -project ios/Volta/Volta.xcodeproj -scheme Volta \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=27.0' \
  -parallel-testing-enabled NO test
```

If the simulator name differs, use another available iOS 27 device as documented in
`ios/README.md`; do not weaken StoreKit assertions to accommodate a broken runtime.

Derive the marketing version from Xcode and prompt for the highest build actually visible
in App Store Connect. Keep these variables in the same shell used for build/archive:

```bash
RELEASE_VERSION="$(xcodebuild -project ios/Volta/Volta.xcodeproj -scheme Volta \
  -configuration Release -showBuildSettings |
  awk -F ' = ' '/^[[:space:]]*MARKETING_VERSION = / { print $2; exit }')"
[ -n "$RELEASE_VERSION" ] || { echo 'BLOCKED: no MARKETING_VERSION'; exit 1; }

printf 'Highest uploaded App Store Connect build: '
IFS= read -r HIGHEST_ASC_BUILD
case "$HIGHEST_ASC_BUILD" in
  ''|*[!0-9]*) echo 'BLOCKED: highest build must be a non-negative integer'; exit 1 ;;
esac
RELEASE_BUILD=$((HIGHEST_ASC_BUILD + 1))
[ "$RELEASE_BUILD" -ge 2 ] || RELEASE_BUILD=2
[ "$RELEASE_BUILD" -gt "$HIGHEST_ASC_BUILD" ] || {
  echo 'BLOCKED: candidate build is not greater than uploaded build'; exit 1;
}
printf 'candidate version=%s build=%s\n' "$RELEASE_VERSION" "$RELEASE_BUILD"
```

Do not infer `HIGHEST_ASC_BUILD` from the project file. Every new upload must repeat this
read-only App Store Connect reconciliation.

Run the explicit Release build with all four final HTTPS values injected. The project must
fail closed when any value is missing, malformed, non-HTTPS, private, or a known placeholder;
do not weaken validation to obtain an archive:

```bash
xcodebuild -quiet \
  -project ios/Volta/Volta.xcodeproj \
  -scheme Volta \
  -configuration Release \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/volta-release-build \
  CODE_SIGNING_ALLOWED=NO \
  MARKETING_VERSION="$RELEASE_VERSION" \
  CURRENT_PROJECT_VERSION="$RELEASE_BUILD" \
  VOLTA_RELEASE_BASE_URL="$RELEASE_URL" \
  VOLTA_PRIVACY_POLICY_URL="$PRIVACY_URL" \
  VOLTA_TERMS_URL="$TERMS_URL" \
  VOLTA_SUPPORT_URL="$SUPPORT_URL" \
  build
```

Expected: zero warnings and success. Any test or build failure stops the release.

## 8. Create and inspect an unsigned local archive

Use a fresh archive path; do not overwrite evidence from another candidate:

```bash
ARCHIVE="/tmp/Volta-${RELEASE_VERSION}-build${RELEASE_BUILD}-unsigned.xcarchive"
[ ! -e "$ARCHIVE" ] || {
  echo "BLOCKED: archive path already exists: $ARCHIVE"; exit 1;
}
xcodebuild \
  -project ios/Volta/Volta.xcodeproj \
  -scheme Volta \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "$ARCHIVE" \
  CODE_SIGNING_ALLOWED=NO \
  MARKETING_VERSION="$RELEASE_VERSION" \
  CURRENT_PROJECT_VERSION="$RELEASE_BUILD" \
  VOLTA_RELEASE_BASE_URL="$RELEASE_URL" \
  VOLTA_PRIVACY_POLICY_URL="$PRIVACY_URL" \
  VOLTA_TERMS_URL="$TERMS_URL" \
  VOLTA_SUPPORT_URL="$SUPPORT_URL" \
  archive
APP="$ARCHIVE/Products/Applications/Volta.app"
```

Run the recursive contract against the **actual archived app** and the four exact URLs
approved for this candidate. This is intentionally separate from the ordinary static test
run: omitting `VOLTA_RELEASE_APP_PATH` skips the built-bundle scan, while omitting any
`VOLTA_EXPECTED_*` value below makes the candidate scan fail.

```bash
VOLTA_RELEASE_APP_PATH="$APP" \
VOLTA_EXPECTED_RELEASE_BASE_URL="$RELEASE_URL" \
VOLTA_EXPECTED_PRIVACY_POLICY_URL="$PRIVACY_URL" \
VOLTA_EXPECTED_TERMS_URL="$TERMS_URL" \
VOLTA_EXPECTED_SUPPORT_URL="$SUPPORT_URL" \
PYTHONPYCACHEPREFIX=/tmp/volta-pycache \
python3 tests/test_ios_release_contract.py -v
```

Expected: all release-contract tests pass with **zero skipped tests**. The scan compares
the archived `Info.plist` URLs exactly, recursively rejects debug/private/test residue,
checks the bundled privacy manifest and localizations, and confirms no `.storekit` file is
present.

Inspect the actual archive:

```bash
plutil -p "$APP/Info.plist"
plutil -p "$APP/PrivacyInfo.xcprivacy"
plutil -extract CFBundleIdentifier raw "$APP/Info.plist"
plutil -extract CFBundleShortVersionString raw "$APP/Info.plist"
plutil -extract CFBundleVersion raw "$APP/Info.plist"
plutil -extract VoltaReleaseBaseURL raw "$APP/Info.plist"
plutil -extract VoltaPrivacyPolicyURL raw "$APP/Info.plist"
plutil -extract VoltaTermsURL raw "$APP/Info.plist"
plutil -extract VoltaSupportURL raw "$APP/Info.plist"
plutil -extract CFBundleDisplayName raw "$APP/Info.plist"
plutil -extract UIDeviceFamily json -o - "$APP/Info.plist"
plutil -extract UISupportedInterfaceOrientations json -o - "$APP/Info.plist"
plutil -p "$APP/Info.plist" | rg 'NSAppTransportSecurity|NSAllowsArbitraryLoads|NSLocalNetworkUsageDescription'
find "$APP" -path '*.lproj/InfoPlist.strings' -print -exec plutil -p {} \;
find "$APP" -name '*.storekit' -o -name '*UITest*' -o -name '*TestHarness*'
strings "$APP/Volta" | rg 'VoltaUITest|VoltaPaywallDebugState|127\.0\.0\.1|100\.84\.44\.96|NSAllowsArbitraryLoads'

[ "$(plutil -extract CFBundleShortVersionString raw "$APP/Info.plist")" = "$RELEASE_VERSION" ]
[ "$(plutil -extract CFBundleVersion raw "$APP/Info.plist")" = "$RELEASE_BUILD" ]
[ "$(plutil -extract VoltaReleaseBaseURL raw "$APP/Info.plist")" = "$RELEASE_URL" ]
[ "$(plutil -extract VoltaPrivacyPolicyURL raw "$APP/Info.plist")" = "$PRIVACY_URL" ]
[ "$(plutil -extract VoltaTermsURL raw "$APP/Info.plist")" = "$TERMS_URL" ]
[ "$(plutil -extract VoltaSupportURL raw "$APP/Info.plist")" = "$SUPPORT_URL" ]
```

Expected:

- bundle ID `tech.arsik.volta`, derived version `$RELEASE_VERSION`, and dynamic build
  `$RELEASE_BUILD` strictly greater than the highest uploaded build and at least 2;
- the exact approved public HTTPS origin;
- the exact approved public HTTPS Privacy, Terms, and Support URLs;
- PrivacyInfo with tracking false and the reviewed data/API declarations;
- no Release ATS/local-network exception (the corresponding `rg` command should print
  nothing);
- no `.storekit`, UI-test resource, private IP, localhost endpoint, DEBUG paywall argument,
  or arbitrary-load token in the Release app; and
- iPhone-only, portrait-only declarations and the intended app icon/launch background;
- localized `CFBundleDisplayName` values and installed home-screen labels aligned with the
  final App Store name.

Install the matching Release simulator product, then tap Settings **Privacy, Terms, and
Support**. If the paywall branch is enabled, also tap its **Terms and Privacy** entries
(the paywall has no Support entry):

```bash
SIM_APP=/tmp/volta-release-build/Build/Products/Release-iphonesimulator/Volta.app
xcrun simctl install booted "$SIM_APP"
xcrun simctl launch booted tech.arsik.volta
```

For each tap, record the visible label, source screen, effective destination, HTTP 200,
and confirmation that the page needs no login and contains no placeholder/manual-review
text. Settings must map to `$PRIVACY_URL`, `$TERMS_URL`, and `$SUPPORT_URL`; an enabled
paywall must map Terms to `$TERMS_URL` and Privacy to `$PRIVACY_URL`. Repeat on the signed
TestFlight build; simulator evidence alone is not the final gate. If paywall is disabled,
record it as absent rather than fabricating a tap.

Also inspect archive size/resources and launch on a Release-installed simulator/device.
An unsigned local archive proves structure and compilation; it is **not** Apple validation
or upload readiness.

## 9. Decide and configure monetization

Choose exactly one branch.

### Branch A — free/TestFlight build, paywall off

1. Keep `PaywallConfig.enabled = false`.
2. Do not claim an active subscription or trial in metadata/screenshots/review notes.
3. Paste the complete disabled assembly from `docs/appstore/review-notes.md`.
4. If the App Store release itself is free, record that product decision explicitly.
5. Legal/support Settings links still require the final embedded Privacy, Terms, and
   Support values and installed tap evidence even though the paywall is absent.

### Branch B — Volta Plus enabled

1. Reconcile/create the exact products in `docs/appstore/subscriptions.md`.
2. Complete agreements, tax/banking, territories, prices, tax category, subscription
   levels, and the owner-approved 7-day introductory offer.
3. Add en/es/ru group/product localizations and a real review screenshot.
   Product display names must be 2–30 characters and product descriptions at most 45;
   use the measured copy in `docs/appstore/subscriptions.md` only after native review.
4. Verify content rights permit the paid use; public data is not automatically licensable.
5. Exercise ASC sandbox/TestFlight products end to end.
6. Only then change `PaywallConfig.enabled` to `true` in a new commit/build.
7. Increment the build number above every upload and rerun Sections 7–8.
8. Recapture metadata screenshots from the enabled build and paste the complete enabled
   review-note assembly. There is no remote flag; the uploaded binary is authoritative.
9. Tap paywall Terms and Privacy and prove each opens its matching embedded final public
   page. Separately tap Settings Privacy, Terms, and Support and retain the same evidence.

The owner must approve actual prices and the free/paid split. Never derive production
pricing from the local `.storekit` fixture.

## 10. Produce the screenshot assets

Follow `docs/appstore/screenshots.md`:

1. Capture native accepted 6.9-inch and 6.1-inch portrait dimensions.
2. Capture six truthful states in en/es/ru from the final Release build/service.
3. Include no fabricated Go now, price, source proof, trial eligibility, or savings.
4. Do not show the paywall when Branch A is used.
5. Record exact pixels, build/commit, API evidence/time, rights result, and SHA-256.
6. Inspect every image for stale UI, raw localization keys such as `inflect: true`,
   truncation, test endpoints, private data, and fixed defects.
7. Confirm the installed localized home-screen label matches the selected App Store name.
   Any `CFBundleDisplayName`/name change requires rebuild, archive, reinstall, and a full
   screenshot recapture; do not reuse images from the previous label.

Use `sips` and `shasum` for evidence:

```bash
sips -g pixelWidth -g pixelHeight path/to/screenshot.png
shasum -a 256 path/to/screenshot.png
```

Do not upload planned/placeholder manifest rows.

## 11. Enter App Store Connect information

Owner/App Store Connect actions, after the existing record is reconciled:

1. App Information:
   - final available localized name;
   - matching localized installed display names/home-screen labels verified from the final
     archive and installed build;
   - primary category chosen by owner;
   - Privacy Policy URL;
   - standard EULA or approved custom EULA;
   - DSA trader status and any required permits/markings.
2. Candidate-version (`$RELEASE_VERSION`) localizations:
   - subtitle, promotional text, description, keywords;
   - Support URL and optional Marketing URL;
   - 6.9-inch and 6.1-inch en/es/ru screenshots.
3. App Privacy:
   - start from `docs/appstore/app-privacy.md`;
   - disclose Precise Location, Search History, and conservative Device ID/IP use;
   - App Functionality only, tracking false;
   - reconcile against actual production retention/providers before saving.
4. Age Rating:
   - complete every current question;
   - expected result is 4+ only if the final answers support it;
   - Not Applicable for Kids category.
5. Export Compliance:
   - answer from the final binary and `docs/appstore/compliance.md`;
   - current engineering draft is system TLS/StoreKit only, no non-exempt crypto.
6. Review Information:
   - real reachable contact;
   - no demo account required;
   - paste exactly one complete disabled/enabled assembly from
     `docs/appstore/review-notes.md`; do not combine assemblies;
   - provide backend/support notes and any requested attachment.
7. Build:
   - attach only the signed, processed, validated final build.
8. Subscriptions, if Branch B:
   - attach both IAPs, localizations, availability, offer, and review screenshot.

Preview every locale in App Store Connect. Do not rely on fallback metadata to claim a
localization is complete.

Immediately before entry, run the all-artifact placeholder/instruction gate from
`docs/appstore/README.md` over the final exported metadata, review notes, IAP copy,
compliance answers, legal pages, attribution statements, and screenshot text. No output
and exit 0 are required; then perform manual review.

## 12. Sign, validate, and upload

These actions require the owner's Apple account, signing identity, provisioning, and
explicit approval.

1. In Xcode, select the correct owner team for `tech.arsik.volta` and automatic/manual
   signing according to the owner's policy.
2. Confirm `VOLTA_RELEASE_BASE_URL`, `VOLTA_PRIVACY_POLICY_URL`, `VOLTA_TERMS_URL`, and
   `VOLTA_SUPPORT_URL` are the approved HTTPS values inspected in the unsigned candidate.
3. Select **Any iOS Device (arm64)** and Product → Archive.
4. In Organizer, inspect the dynamic version/build, bundle ID, localized display name,
   entitlements, privacy manifest, all four resolved URLs, and archive contents again.
5. Choose **Validate App**. Resolve every validation warning/error; do not waive privacy,
   signing, icon, or entitlement problems without understanding them.
6. Choose **Distribute App → App Store Connect → Upload**.
7. Do not paste Apple credentials or app-specific passwords into repository scripts/logs.
8. Wait for processing; review email and App Store Connect notices for symbol, privacy,
   export, or asset issues.
9. Confirm the processed build number is strictly higher than every prior upload, at
   least 2, and matches the inspected archive.

Local `xcodebuild archive` success is not a substitute for Organizer validation.

## 13. TestFlight gate

1. Complete any export/compliance prompt for the processed build.
2. Add internal testers first; create an external group only if the owner wants it and
   complete Beta App Review information.
3. Test on a real iPhone in English, Spanish, and Russian; light/dark; large accessibility
   text; reduced motion; fresh/degraded/stale/offline states. For location, verify that
   first grant works, a later launch with permission already granted makes one request to
   load/rank nearby chargers, locate tap requests again, coordinates go only to the
   configured Volta service for that functionality, and denial leaves city search.
4. Validate the public endpoint from cellular and Wi-Fi, including datacenter-source
   degradation and stale/fail-closed behavior.
5. If Branch B, test real sandbox/TestFlight product metadata, eligible/ineligible offer,
   purchase, pending, cancel, restore, expiry, billing recovery, refund/revocation, and
   no mid-route stranding.
6. Do not pay or compensate testers for TestFlight participation.
7. From the signed TestFlight build, tap Settings Privacy, Terms, and Support and, if the
   paywall is enabled, paywall Terms and Privacy. Record exact effective public URLs,
   unauthenticated 200 responses, and no placeholder/manual-review text.

## 14. Manual product gates

Automated green does not close these:

- **TC-051 physical field test:** follow `qa/field-test-checklist.md` and retain real
  station evidence. Verify that a driver can trust route, access, charge price, parking,
  connector, and source presentation onsite.
- **TC-056 native VoiceOver traversal:** follow `qa/voiceover-checklist.md` on real iPhone
  across overview/search, live list/map, station detail, degraded/stale/error, Settings,
  and paywall if enabled.

Record real evidence through the repository's QA process. Do not mark the completion
audit complete without both manual gates.

## 15. Submit and release

1. Run a final consistency check: binary, endpoint, hosted legal pages, App Privacy,
   metadata, screenshots, review notes, source rights, subscription state, version/build,
   and territories all describe one product.
2. Confirm Feníe stays within its recorded reuse permission and bounded read-only query,
   carries attribution, and is not presented as runtime-live authority. Confirm every personal-data provider has evidence supporting the
   same-or-equal-protection statement.
3. Attach the final build and IAPs if enabled.
4. Add for Review and submit only with owner approval.
5. Monitor Resolution Center and respond with factual evidence. Do not invent provider
   permissions, live coverage, privacy behavior, or reviewer credentials.
6. Prefer manual release after approval for the first version so the service, legal pages,
   support contact, and endpoint can be checked immediately before availability.
7. After release, monitor endpoint health, source failures, support, privacy requests,
   subscription behavior, and incorrect charger reports. Prepare a rollback/hotfix rather
   than widening trust claims during an outage.

## 16. Stop conditions

Do not upload or submit when any of these is true:

- existing App Store app/build/product state is unknown or a duplicate may be created;
- Release endpoint is `configuration.invalid`, private, cleartext, temporary, or unreliable;
- legal/support pages contain placeholders or do not match production behavior;
- source/database/photo rights are unresolved;
- Feníe evidence exceeds its recorded reuse permission, its query bypasses the transport
  guard, required attribution is missing, or it is presented as runtime-live authority;
- any recipient of user data lacks the due diligence needed for a truthful Guideline
  5.1.1(i) same-or-equal-protection assurance;
- App Privacy says “not collected” or conflicts with IP logs/caches/location/search flows;
- metadata/screenshots claim live availability, trial, price, coverage, or paid access that
  the build cannot prove;
- Release bundle contains `.storekit`, DEBUG overrides, arbitrary ATS loads, private URLs,
  or test resources;
- any final service/Privacy/Terms/Support build setting is missing/invalid, archive values
  differ from approved URLs, or installed Settings Privacy/Terms/Support or enabled-paywall
  Terms/Privacy fails its matching public 200/no-login/no-placeholder destination check;
- installed/localized `CFBundleDisplayName` or home-screen label does not align with the
  final App Store name, or screenshots predate the final name;
- build number is not greater than every existing upload;
- native/full regression tests are not green or a skipped family is hidden;
- signing/Organizer validation fails;
- DSA, export, age rating, agreements, tax/banking, or owner pricing decisions are incomplete;
- TC-051 or TC-056 is represented as complete without real manual evidence.

## 17. Release evidence record

Retain a non-secret receipt with:

- git commit and clean-tree status;
- version/build, archive path/hash, Xcode/SDK versions;
- exact test commands and results, with skips named;
- final endpoint and health-check time (no credentials/query PII);
- legal/support URL checks;
- all four resolved Info.plist URL values, installed Settings Privacy/Terms/Support tap
  evidence, and enabled-paywall Terms/Privacy tap evidence when applicable;
- localized display-name/home-label evidence;
- metadata-limit report and screenshot manifest/hashes;
- source-rights certification location;
- Feníe permission/attribution and bounded-query evidence plus the owner-held rights-audit reference,
  and provider same-or-equal-protection evidence references;
- App Privacy/export/age/DSA answers;
- ASC app/build/product identifiers that are safe to record;
- Organizer validation/upload result;
- TestFlight devices/locales/states;
- TC-051/TC-056 evidence references; and
- owner approval/date for submission and release.

Do not store credentials, payment details, private certificates, provisioning profiles,
or private legal identity evidence in the public release receipt.
