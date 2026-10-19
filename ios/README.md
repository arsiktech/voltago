# Volta Go iOS — native SwiftUI client

A native iPhone client for the Volta Go backend, laid out like Apple Maps: one full-screen
MapKit map and one persistent sheet. The sheet pins search, Settings and filter chips at the
top; below them one scroller holds the results heading, status notes and decision-forward
station cards. Tapping a pin or a card opens the charger's details inside the same sheet
(medium height from the map, so the pin stays visible), with Open in Maps or the public source
as the next tap. Nearby chargers merge into counted clusters in screen space instead of being
dropped. Liquid Glass is used only for the floating map controls on iOS 26 and later, with an
opaque fallback for earlier iOS, Reduce Transparency and Increase Contrast. The surface shows verified live stations AND the source-backed free-charger database
(Lidl, OSM, Barcelona Endolla, municipal open data, …) — the latter clearly marked **"Not live"** (never
Go-now, and only map-pinned when their location is verified). This is the app's core inventory;
hiding leads previously left the national overview empty (fixed 2026-07-18, WS-1). Detailed
nonfatal source warnings live in Settings rather than competing with the route decision.

Place search and map navigation work worldwide, with Spain as the initial region.
Overview requests include the visible bounds instead of downloading a global catalog.
Crossing the date line and reaching the poles preserve valid API coordinates; views wider
than the backend's default live budget (1.2° latitude, longitude adjusted for latitude)
load stored evidence. Geographic support does not imply live coverage or a verified tariff.

## Requirements
- iOS 18.0 or later (iPhone XS/XR and newer), so drivers on older iPhones are covered. The
  only iOS 26+ APIs (Liquid Glass, and StoreKit's refund-type revocation detail on 26.4+)
  sit behind `#available` checks with standard fallbacks.
- Check layout changes on an iPhone SE (3rd generation) with the iOS 18.6 runtime, an iPhone 17
  Pro and a Pro Max on iOS 27, in light and dark, at default and accessibility text sizes.
  `VoltaUITests/ScreenshotTourUITests` walks overview → search → city → detail → price filter
  and attaches a screenshot at each step; set `TEST_RUNNER_TOUR_BASE_URL` to use a running
  backend and `TEST_RUNNER_TOUR_OUT` to also write the PNGs to a folder.
- Xcode 27 with the iOS 27 SDK and Swift 6.4 compiler. The project's Swift 5 language
  compatibility mode is separate from the compiler version; changing that mode
  requires a concurrency migration and native verification. Record the exact
  Simulator runtime build used for evidence rather than assuming it matches the
  newest device OS patch release.
- The Volta Go backend running (`PORT=8773 python3 server.py` from the repo root).

## Run in the Simulator
```bash
cd ios/Volta
xcodebuild -project Volta.xcodeproj -scheme Volta \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build
# or just open Volta.xcodeproj in Xcode and press Run
```
Debug builds share the Mac's loopback, so their default base URL
`http://127.0.0.1:8773` works as-is. Grant location (or
`xcrun simctl privacy <sim> grant location tech.arsik.volta`) and set a simulated
location (`xcrun simctl location <sim> set 39.4699,-0.3763`) to see the live near-me mode.

The editable localhost/LAN/Tailscale presets are development tools compiled only into
**Debug**. `Info-Debug.plist` permits local networking without a broad arbitrary-loads
exception. Release has no local-network purpose string or ATS exception and ignores the
persisted `voltaBaseURL` preference entirely.

## StoreKit subscription tests

The supported target is an iOS 27 Simulator. Run subscription tests serially because
`SKTestSession` mutates process-wide StoreKit state:

```bash
cd ios/Volta
xcodebuild -project Volta.xcodeproj -scheme Volta \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=27.0' \
  -parallel-testing-enabled NO \
  -only-testing:VoltaTests/SubscriptionTests test
```

Do not weaken, mock, skip, or expected-fail purchase and entitlement assertions for runtime
convenience. If StoreKitTest is broken in the installed iOS 27 runtime, record that as a toolchain
blocker and move to a newer Xcode/iOS 27 runtime pair.

The local `Volta.storekit` file lives under `VoltaTests/` only. The shared scheme points its
Run and Test actions to that test fixture, while the production app target and Release archive
contain no StoreKit configuration file.

## Paywall implementation (default off)

`PaywallConfig.enabled` remains a literal `false`. In that state the app takes the original
`RootView` path directly, starts `AppState` immediately, performs no StoreKit admission work,
and the complete app remains free. Do not enable the flag until the P5 owner gates for App
Store Connect products, agreements, pricing, legal hosting, and release review are complete.

When the owner later enables it, StoreKit entitlement resolves before charger/location work.
Access is a one-way latch for that process: a verified subscriber enters without a paywall
flash, and an expiry, refund, revocation, or billing retry received mid-route does not replace
the active map. The next cold launch enforces the updated entitlement. Prices, currencies,
periods, trial eligibility/duration, and yearly savings all come from StoreKit `Product`
metadata; catalog failure shows Retry + Restore without inventing an offer.

DEBUG paywall flows keep native, offline-safe Terms and Privacy screens for hermetic UI tests.
Release uses only the separately configured hosted Terms and Privacy URLs; unsafe or missing
URLs stay visibly pending instead of falling back to the embedded pre-release drafts. Public
hosting and owner review remain P5 owner gates.

Hermetic DEBUG UI coverage uses `-VoltaUITestPaywall` and optional
`-VoltaPaywallDebugState <state>`. Both argument names and all override logic are compiled only
under `#if DEBUG`; Release evaluates only the literal feature flag.

## Debug on a real iPhone
1. Open the project in Xcode, set your personal team under Signing & Capabilities, Run.
2. Start the backend on the Mac **bound to all interfaces** (loopback-only is unreachable
   from the phone):
   ```bash
   HOST=0.0.0.0 PORT=8773 python3 server.py
   ```
3. Debug networking (no cloud needed): the device build defaults to
   `http://your-mac.local:8773`. Replace `your-mac` with your Mac's Bonjour name, or enter a
   LAN or VPN address (for example a Tailscale IP) in the in-app **Settings (gear)** sheet.
   It has presets plus a Test-connection button, so no recompiling to
   switch. This plain-HTTP/local-network surface is compiled into Debug only.

   ⚠️ Simulator quirk: the iOS Simulator cannot route into the host's Tailscale (utun)
   interface, so tailnet URLs fail **in the Simulator only** — use `127.0.0.1` there
   (the default) or the LAN IP. Real devices with the Tailscale app work fine.

## Configure a Release build

Release accepts exactly one bundled public HTTPS endpoint. The checked-in default is the
reserved fail-closed URL `https://configuration.invalid`; it deliberately loads no charger
data. Supply a real stable service at build time only after the owner has activated and
verified that endpoint:

```bash
cd ios/Volta
xcodebuild -project Volta.xcodeproj -scheme Volta -configuration Release \
  -destination 'generic/platform=iOS' \
  VOLTA_RELEASE_BASE_URL=https://your-stable-volta-service.example \
  VOLTA_PRIVACY_POLICY_URL=https://your-public-site.example/privacy \
  VOLTA_TERMS_URL=https://your-public-site.example/terms \
  VOLTA_SUPPORT_URL=https://your-public-site.example/support \
  archive
```

The service runtime accepts only the root of a public ASCII HTTPS hostname (an optional valid
port is allowed). Missing, malformed, HTTP, loopback/local/reserved, Unicode or punycode,
percent-encoded-host, IP-literal or legacy numeric-IP, credential-bearing, query-bearing,
fragment-bearing, and non-root values fail closed to `configuration.invalid`; there is no
fallback to Debug preferences or a private address. Hosted legal/support URLs use the same
transport and hostname boundary but may include document paths; missing or unsafe values are
not links. Release Settings is read-only and shows service status, hosted legal/support links
or an explicit pending state, and version/build. Real service/legal URLs and signing remain
owner gates before distribution; this repository does not invent or activate any of them.
The checked-in TestFlight candidate and last known Internal Testing assignment live in the
canonical `docs/appstore/testflight-state.json`. The timestamp build scheme prevents the prior
failure where a low build number uploaded but did not replace the version visible in TestFlight.
The assignment record does not claim device-install proof. Immediately before archiving, read the
current highest build in App Store Connect and run `qa_testflight_candidate.py` with both
`--highest-asc-build` and a fresh `--asc-checked-at`; then rerun with `--archive` against the actual
signed Organizer archive. An unsigned generic-device Release build is compile/structure evidence,
not `ARCHIVE_READY`, upload, or TestFlight readiness proof.

After location permission has been granted, the near-me-first flow uses location when the app
opens and when the user explicitly asks to find or rank nearby chargers. Coordinates go only
to the configured Volta service; the localized system-purpose string and in-app privacy summary
state that behavior directly.

## Architecture
```
Volta/            synchronized folder — new .swift files are picked up automatically
  VoltaApp.swift    @main entry
  AppState.swift    orchestration: overview/live modes, filters, fail-closed rules,
                    60s live auto-refresh, geocode search, CoreLocation
  VoltaAPI.swift    async client for /api/chargers (+overview/bbox) and /api/geocode
  Models.swift      Codable rows mirroring server.py enriched stations (+presentation
                    helpers ported from frontend/src/lib/format.ts, incl. the D-084
                    forward-compatible decision fallback)
  RootView.swift    map/sheet navigation: starts at the medium detent; accessibility text
                    sizes use the large sheet. Map prices require explicit verification;
                    unverified exact locations remain dots, with regional clustering.
  DiscoveryControls.swift  stable locate, search-this-area and settings actions;
                           accessible material fallback for map controls
  DiscoveryFilters.swift   price/power sliders and connector multiselect; filter state
                           lives in AppState, including clearFilters recovery
  SheetView.swift   persistent city-search field, visible discovery filters, recovery/status
                    panels and the full vertical list of ranked in-view stations
  StationViews.swift  row card, availability breakdown (N free · M in use · K out),
                      decision pill / tier / corroboration badges, detail screen with
                      full provenance (claims, connectors, public sources, license,
                      attribution, transformation and source/retrieval dates). Compact
                      cards put availability and route facts on separate rows so neither
                      is squeezed into unreadable vertical text.
  StationActions.swift       shared map/detail action order: source check first unless
                             explicit route readiness makes Maps the primary action
  StationPresentation.swift  compact parking/access facts, separate from charging price;
                             full claim attribution remains in StationViews
```

`AppState.clearFilters()` restores the launch defaults for free-only, maximum
price, connector types, minimum power and saved-only filtering. It preserves the
map area, saved charger records and source evidence; changing the live price cap
still uses the normal refresh path. `DiscoveryFilterTests` covers each filter and
preservation of discovery context. Run this class with the relevant native
decision/presentation tests when changing discovery controls. The focused source
contract is `python3 -m unittest discover -s tests -p 'test_ios_design_contract.py'`;
it checks UI wiring, touch targets, motion, accessibility and localization but
does not replace Simulator interaction or authentic VoiceOver evidence.

`CoreFlowsUITests` covers persistent discovery, filter recovery, station details and
light/dark rendering at normal and accessibility text sizes. `AdverseStatesUITests`
covers failed requests, denied location and cached/stale evidence. Run these classes
serially on the same compatible Simulator; the loopback stub supplies their data.
Discovery screenshots use the DEBUG-only `-VoltaUITestDiscoveryAppearance light|dark`
argument and assert the effective SwiftUI appearance before capture. Each test also
sets its text-size baseline explicitly. Release contains no appearance override.

## Your EV and connected vehicle readings

The discovery sheet opens **Your EV**, a native garage with a car selector, battery level,
remaining range, usable pack capacity, estimated available kWh, charging state, and a
50–100% planning target. Owner-selected photos use the system Photos picker, are downsampled
and re-encoded without EXIF metadata, protected on disk, and excluded from iCloud backup.
The default car image is labeled an illustration. Manual entries and missing charging
specifications can be entered without a provider connection; they stay local and are always
labeled **not live**. Provider readings cannot be edited.

Real connections require the same configured Smartcar service as the web app. The phone
opens an ephemeral `ASWebAuthenticationSession` at `/api/vehicles/native/start`, carrying a
SHA-256 challenge for a random in-memory verifier. After provider approval, the service
returns a 60-second, single-use ticket to the fixed `voltagovehicle://connection/callback`.
Only the matching verifier can redeem it at `/api/vehicles/native/exchange`. No OEM access
token, refresh token or Volta session cookie appears in the callback URL. The resulting
Volta cookie is kept in Keychain, scoped to the exact service origin, and sent through an
ephemeral URLSession that blocks redirects and bypasses caches. It is cleared on disconnect,
an expired/disconnected session, or an unauthorized response. Safari cookies are never
assumed to be URLSession cookies. Simulation data is explicitly labeled simulation.

Connected readings refresh no more than once per minute while the app is active. Freshness
comes from the vehicle's per-field observation deadlines, including on a stationary screen;
a new charging-state reading cannot revive an old battery reading. A missing or stale
usable capacity cannot be combined with a newer battery percentage. Current telemetry is
not persisted to the phone. The service endpoint and manufacturer coverage determine whether
real connection is available; there are no bundled provider credentials.

Station details add a planning estimate from the selected car and target. Minimum time uses
the actual matching connector's power capped by the vehicle's AC/DC limit; NACS needs an
explicit AC/DC port classification. Charging cost uses a verified current EUR/kWh tariff,
assumes 10% charging loss, and excludes parking, time and session fees. Missing or stale
inputs remain unknown. Vehicle context never changes station route-readiness or source
warnings. `VoltaTests/VehiclePlanningTests` covers freshness, units, incompatible/unknown
plugs, charging limits, targets and the native callback boundary.

The implementation was checked with the Python iOS design/release source contracts in the
development environment. That environment has no Xcode, Swift compiler or Simulator, so
native compilation, device rendering, Photos interaction, Keychain interaction and a real
provider approval still require validation on macOS with the configured service.

## Localization

`Volta/Localizable.xcstrings` is a String Catalog covering en (source) / es / ru, using
Xcode's extract-from-source model: every Swift string literal (`Text("...")`,
`.accessibilityLabel("...")`, `String(localized: "...")`, etc.) stays byte-identical in the
source — it doubles as the catalog key — so `tests/test_ios_design_contract.py`'s literal
source-string assertions keep pinning real UI copy. No call site was refactored to a symbolic
key. A handful of strings that were architecturally unreachable by the extractor (trapped
inside `String`-typed helper parameters whose signature is pinned by
`test_ios_design_contract.py`, e.g. `StationDetailView`'s `row(icon:title:detail:)`/
`claimRow(icon:label:claim:)`) were made extractable by wrapping the literal argument in
`String(localized:)` at the call site instead — same effect as wrapping in `Text()`, without
touching the pinned function signatures.

**es / ru = DRAFT translations, not native-speaker-verified.** They were authored by the
coding agent with attention to correct EV-charging domain terms (cargador/зарядная станция,
gratis/бесплатно, disponible/доступно, "Ir ahora"/"Comprobar primero"/"Verificar primero" ↔
"Ехать сейчас"/"Сначала уточнить"/"Сначала проверить" matching the Go now/Check first/Verify
first honesty tiers, "No en directo"/"Не в реальном времени" for "Not live", "Datos de …" vs
"Actualizado …" ↔ "По состоянию на …" vs "Обновлено …" preserving the evidence-age-on-a-lead
vs freshness-on-a-live-row distinction) and correct plural handling (Spanish one/other;
Russian one/few/many/other, verified via the compiled `.stringsdict` output for both
`inflect: true` strings — station/port counts). Final sign-off is an owner/native-speaker
review step, deferred to Phase P5 (GOAL_CONTRACT.md §5/§7) before shipping to the App Store.
Until then, treat es/ru copy as functionally correct but not idiomatically final.

The catalog also pins the mixed live/cached trust banner, refresh action, and provenance
labels in all three languages. `tests/test_ios_design_contract.py` rejects missing static
SwiftUI literals and verifies that formatted-value placeholders survive translation.

**Honesty-logic note:** `Station.tierLabel` (Live/Official/Directory/Community) and
`Station.priceText`'s `"Free"` value used to double as both display text AND an internal
comparison token (`tierLabel == "Official"`, `priceText == "Free"`) inside
map-pin-verification logic (`tierIsStrong`, `hasExactDirectoryLocation`,
`isMapLocationVerified`) and a stale-warning-clear check in `AppState.loadOverview()`.
Localizing the display value without fixing this would have silently broken that logic under
a non-English device language (comparing a Spanish/Russian string against a hardcoded English
literal). Fixed via a new locale-invariant `Station.tierKind` used internally instead of
`tierLabel`, direct `isFree`/`priceMin` checks instead of `priceText == "Free"`, and a
`warningIsServerUnreachable` flag instead of `warning?.hasPrefix(...)` — all provably
behavior-preserving (same 56/56 `VoltaTests`/`VoltaUITests`, same `TierLabelTests`/
`PriceTextTests` assertions, unchanged).
