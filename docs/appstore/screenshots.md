# App Store screenshot plan and manifest

Status: plan only. No App Store screenshot is generated, resized, or claimed by this
document.

Apple currently accepts one to ten screenshots per device slot and localization. For the
iPhone-only release, provide the current 6.9-inch slot and a separately captured useful
6.1-inch set. See [Apple's screenshot specifications](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications).

## Accepted portrait sizes used by this plan

- **6.9-inch slot:** `1260×2736`, `1290×2796`, or `1320×2868` pixels, depending on the
  supported source device listed by Apple.
- **6.1-inch slot:** `1170×2532`, `1125×2436`, or `1080×2340` pixels, depending on the
  supported source device listed by Apple.

Capture on a simulator/device that natively produces an accepted size. Do not stretch a
6.3-inch screenshot, add a fake device bezel, or resample a test artifact and call it a
native capture. Recheck Apple's page on capture day because device slots can change.

## Release constraints

- iPhone only; portrait only. No iPad screenshot set is needed while the binary remains
  `TARGETED_DEVICE_FAMILY=1`.
- Locales: English, Spanish (Spain), Russian.
- App Store screenshots must use the exact Release behavior and a configured public HTTPS
  service, not localhost, Tailscale, a DEBUG menu, or a forced UI-test state.
- Show only actual current source data. A “Go now” card may appear only if the captured
  response is genuinely route-ready at capture time. Never fabricate live availability,
  price, savings, source count, parking, or trial eligibility.
- Remove/crop personal location, home/work labels, notifications, Apple ID, and unrelated
  status-bar information.

## Six-frame product-page story

Capture the same truthful sequence for each locale and both size slots:

| No. | Screen/state | User value to show | Honesty requirement |
| --- | --- | --- | --- |
| 01 | National overview, light | Spain-wide source-backed options at first paint | Leads visibly say Not live; no live implication on overview |
| 02 | Place search | Search for a real Spanish city/place and select a result | Search result and language must be real; no private address |
| 03 | Nearby live/list state | Decision-first map/list after a real live refresh | Show freshness/coverage; use Go now only with current availability |
| 04 | Station detail | Charge, parking, trip, connector/access, instructions | Charging and parking provenance remain separate; source visible |
| 05 | Source confidence | Proof/corroboration and source handoff | Directory/community/stale evidence cannot look live |
| 06 | Dark-mode core state | Same scannable decision experience in dark appearance | Adaptive colors, no clipped copy, same data-honesty labels |

Optional seventh frame: an honest degraded or no-route-ready state that demonstrates
fail-closed behavior. Use it only if the copy is clear and the state is reproducible in the
submitted binary; never stage an outage as ordinary availability.

## Subscription screenshot rule

- If the paywall remains disabled, **do not** include it in product-page screenshots.
- If enabled, one product-page frame may show the production paywall only after ASC prices
  and trial configuration are real. The subscription review screenshot is a separate
  App Store Connect asset; follow `subscriptions.md`.
- Never show local StoreKit fixture prices or a DEBUG-forced paywall as production proof.

## File naming

Use lowercase ASCII names:

`NN-locale-slot-state-appearance-vVERSION-bBUILD.png`

After deriving the final runbook variables, create one dynamic tag and use it consistently:

```bash
BUILD_TAG="v${RELEASE_VERSION//./p}-b${RELEASE_BUILD}"
```

Examples are `01-en-6p9-overview-light-${BUILD_TAG}.png`,
`04-es-6p1-detail-light-${BUILD_TAG}.png`, and
`06-ru-6p9-live-dark-${BUILD_TAG}.png`.

Use `6p9` and `6p1` as slot labels; record exact pixels separately. If the build changes,
recapture or explicitly verify every image against the new binary before renaming.

## Capture checklist per image

- [ ] Exact submitted Release commit/build and public HTTPS service recorded.
- [ ] Correct app locale; no mixed English fallback unless that is the shipped behavior.
- [ ] Installed localized home-screen label aligns with the final App Store name. Any name
      or `CFBundleDisplayName` change invalidates the capture and requires a rebuild/
      reinstall/recapture.
- [ ] Native accepted pixel dimensions verified with `sips -g pixelWidth -g pixelHeight`.
- [ ] Light/dark state recorded; normal Dynamic Type unless the screenshot intentionally
      demonstrates accessibility.
- [ ] No `inflect: true`, raw localization key, placeholder, clipped/truncated title,
      `configuration.invalid`, private endpoint, test banner, or DEBUG identifier visible.
- [ ] No stale screenshot from a fixed bug or earlier shell/design.
- [ ] Source, price, freshness, parking, connector, and availability claims checked against
      the captured API response and timestamp.
- [ ] External trademark/logo/photo use covered by the rights inventory.
- [ ] No user/private data.
- [ ] PNG hash recorded after final visual review; file not edited afterward.

## Evidence manifest

Fill one row per asset. `PLANNED` is not evidence.

| File | Locale | Slot | Exact pixels | State | Appearance | Build/commit | API evidence/time | Rights check | SHA-256 | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `[FINAL 01 FILE]` | en | 6.9 | `[FILL]` | overview | light | `[FILL]` | `[FILL]` | `[FILL]` | `[FILL]` | PLANNED |
| `[FINAL 02 FILE]` | en | 6.9 | `[FILL]` | search | light | `[FILL]` | `[FILL]` | `[FILL]` | `[FILL]` | PLANNED |
| `[FINAL 03 FILE]` | en | 6.9 | `[FILL]` | live/list | light | `[FILL]` | `[FILL]` | `[FILL]` | `[FILL]` | PLANNED |
| `[FINAL 04 FILE]` | en | 6.9 | `[FILL]` | detail | light | `[FILL]` | `[FILL]` | `[FILL]` | `[FILL]` | PLANNED |
| `[FINAL 05 FILE]` | en | 6.9 | `[FILL]` | provenance | light | `[FILL]` | `[FILL]` | `[FILL]` | `[FILL]` | PLANNED |
| `[FINAL 06 FILE]` | en | 6.9 | `[FILL]` | live/list | dark | `[FILL]` | `[FILL]` | `[FILL]` | `[FILL]` | PLANNED |

Duplicate the six rows for `es` and `ru`, then for `6p1`, using separately captured files.
Do not mark a localization complete merely because App Store Connect can fall back to or
scale another set.
