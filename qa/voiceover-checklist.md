# Volta Go native VoiceOver checklist

Current runbook: 2026-07-15

Purpose: validate the installed SwiftUI app with real iPhone VoiceOver. Repository source,
XCTest/XCUITest, keyboard, DOM, and Chrome accessibility-tree checks are useful preparation, but
none can prove the spoken order, focus recovery, or clarity of the actual native experience.

## Preconditions

- Use the owner's iPhone with the current Volta Go build and VoiceOver enabled.
- In Settings, confirm the backend is `https://voltago.app` and Test Connection succeeds.
- Start in the national overview. Do not grant location until intentionally testing Locate.
- Have a short screen/audio recording or a note-taking path ready; the transcript is required.

## Pass 1: Map, sheet, search, and filters

1. Start VoiceOver.
2. Navigate from the top of the page.
3. Confirm the Volta brand is announced as one element, then Locate and Settings are reachable.
4. Confirm the sheet title says Spain overview and the status badge says Not live.
5. Confirm Search, Open now, Free, and Leads are reachable and selected state is announced.
6. Confirm a station row is one understandable control rather than a sequence of unlabeled icons.

Expected result: the user understands that the opening list is source-backed and not live before
choosing a station or granting location.

## Pass 2: Status and mode changes

1. Load the app fresh.
2. Confirm the overview's Not live status is discoverable.
3. Search a city or use Locate and wait for the nearby/live result to replace the overview.
4. Confirm fresh, limited-live, stale, or unavailable state is announced or discoverable without
   losing the user's position in the sheet.

Expected result: source-backed overview versus fresh/degraded live mode is unambiguous.

## Pass 3: Charger Card Semantics

1. Navigate to the first station card.
2. Confirm VoiceOver announces the station name.
3. Confirm the announcement includes decision, price, source confidence, freshness, parking/access,
   and whether it is live or a lead.
4. Navigate through the card actions.
5. Open the row and confirm the native detail screen has a clear heading and back path.

Expected result: the user can decide whether the charger is live/route-ready or only a source-backed lead before activating a map link.

## Pass 4: Detail and Settings semantics

1. Traverse Trust & provenance. Charging claim and Parking claim must be separate stops/phrases,
   never merged into one source assertion.
2. Confirm connector rows read as combined facts rather than status-dot/icon fragments.
3. Confirm Open in Maps and Source are reachable, station-specific, and clearly external.
4. Open Settings. Confirm the backend field, presets, selected preset, and Test Connection control
   have useful labels/hints and a predictable return path.

Expected result: evidence and configuration are understandable without relying on glyphs or sight.

## Pass 5: Open Now And Empty Recovery

1. Activate Open now.
2. If route-ready rows appear, confirm each is announced as live/open and exposes Maps plus source/provider actions after opening it.
3. If no route-ready cards appear, confirm the empty state is announced.
4. Confirm the available recovery action is reachable and activatable.
5. Confirm fallback leads remain announced as Not live/source-backed, never route-ready.

Expected result: Open now is fail-closed and recovery actions remain understandable without sight.

## VoiceOver Transcript Requirements

The manual evidence is not complete with yes/no answers alone. Capture a short recording or written transcript for:

- App entry, overview Not live badge, and filter announcements including selected state.
- Overview-to-nearby status change and how the new status is discovered.
- First station-row announcement, including decision, price, parking/access, source, freshness, and live versus lead status.
- Detail heading, separate Charging claim/Parking claim, connector row, Open in Maps, and Source.
- Settings field/preset/selected-state/Test Connection announcements.
- Open now empty or route-ready state plus its recovery control.
- Focus order notes proving there is no focus trap.

## Evidence To Capture

- iPhone model, iOS version, Volta build/commit if known, and backend URL.
- Recording file path or written transcript reference.
- App entry/filter transcript.
- Whether preview/live status updates were announced.
- Status update transcript.
- First card announcement notes and transcript.
- Card action transcript.
- Detail and Settings announcement notes/transcript.
- Open now state and recovery transcript.
- Focus order notes.
- Any missing labels, confusing repeated announcements, focus traps, or controls that cannot be reached.

## Current Blocker

This checklist requires a person to operate real iPhone VoiceOver and judge the spoken experience.
The 2026-07-15 simulator screenshot proves the current overview badge says Not live, and automated
contracts prove labels/traits exist, but TC-056 stays blocked until the transcript/recording and
focus-order evidence above are attached.

## Native iOS (automated-side evidence)

Date added: 2026-07-13 (GOAL_CONTRACT.md P3.4, the award-bar VoiceOver-completion item). This section is the native-app counterpart to the web checklist above -- it documents what is now proven by repository-level automated contracts for the SwiftUI app (`ios/Volta/`), per screen. It does NOT close the human gate: native VoiceOver traversal on a real device or Simulator, observed by a person, remains **TC-056**, and TC-056 stays `blocked` in `qa/test-cases.csv` exactly as it does for the web flow above. Nothing below should be read as "VoiceOver verified" -- only "the labels/hints/traits/targets/motion-path a human VoiceOver pass would exercise are present in the source", pinned by `tests/test_ios_design_contract.py` and, where the assertion can run at the Decodable/computed-property layer, the `VoltaTests` XCTest target.

### What's automated-verifiable today, per screen

**Map (`RootView.swift`)**
- Map pin and cluster annotations are native `Button`s (not gesture-only views), each with `.accessibilityLabel` (station name / "Charger cluster") and `.accessibilityHint` ("Shows charger details" / "Zooms in to inspect this area"); the brand chip is a single combined element (`.accessibilityElement(children: .ignore)` + one label) rather than fragmenting into icon plus two lines of text.
- Locate, Settings, and "Search this area" controls are 44x44pt `Button`s with explicit labels/hints; Locate's label switches between "Find my location" / "Finding my location" with an `.accessibilityValue` for in-progress state.
- Reduce Motion: cluster-zoom camera animation and pill selection both branch on `accessibilityReduceMotion` (contract-pinned since Iteration 165 / TC-107).
- New in P3.4: a `.sensoryFeedback(.selection, ...)` light-impact haptic fires once per pin/cluster tap (see "Haptics" below -- not itself a VoiceOver signal, but the same Section-5 award-bar item, so pinned here for the same reason).

**List (`SheetView.swift`)**
- Search field, filter chips (Open now / Free / Leads), and the retry button all carry explicit labels/hints/values; filter chips additionally expose `.accessibilityValue("On"/"Off")` and `.accessibilityAddTraits(.isSelected)` so VoiceOver announces toggle state, not just a bare button.
- Station rows are a single `Button` wrapping `StationRow` (decision pill + tier + price + availability read as one interactive element per row, not a fragmented card).
- New in P3.4: station-row tap now also fires a `.sensoryFeedback(.selection, ...)` haptic.
- Accessibility Dynamic Type: the header switches to a vertical layout and the sheet uses the `.large` detent only at accessibility sizes (contract-pinned since TC-107).

**Detail (`StationViews.swift`, `StationDetailView`)**
- The "Trust & provenance" rows (verification / corroboration / parking / access / claim-conflict / freshness, via the shared `row(icon:title:detail:)` helper) and the two charge/parking `claimRow` blocks (P3.3, D-159) all carry `.accessibilityElement(children: .combine)`, so each reads as ONE VoiceOver stop (icon + title + detail together) instead of fragmenting into 2-3 stops per row; the decorative SF Symbol icon is never announced on its own (SF Symbols carry no default accessibility label, so `.combine` alone excludes them).
- New in P3.4 (D-162): the Connectors section's per-connector rows (status dot + label + kW + status) also carry `.accessibilityElement(children: .combine)` -- previously up to 4 fragmented stops per connector.
- Public-source photos have an explicit `.accessibilityLabel` (caption or a safe fallback) and a hint warning the image may be stale.

**Settings (`SettingsView.swift`)**
- New in P3.4 (D-161): this screen had ZERO accessibility annotations before this unit. The manual backend-address `TextField` now has a label + hint; each of the 4 preset rows is a single combined element with an explicit label ("{preset name}. {detail}") and a hint naming the address it sets; the Test Connection button has a label that switches with its busy state, plus a hint.
- New in P3.4 (D-161): the currently-selected preset (previously indicated ONLY by a checkmark glyph with no accessible signal) now carries `.accessibilityAddTraits(urlText == preset.url ? .isSelected : [])`, so VoiceOver unambiguously announces which preset is active.

**Haptics** (Section 5: "light impact on selection, success notification on route-ready confirmation -- sparing, never decorative")
- `.sensoryFeedback(.selection, ...)`: map pin tap, map cluster tap (one shared trigger on `RootView.swift`'s `MapLayer`), station row tap (`SheetView.swift`). Plus the one pre-existing `FilterChip` selection haptic. Four `.sensoryFeedback(` call sites total in the whole app -- nothing on locate/settings/search-area/clear-search/dismiss chrome.
- `.sensoryFeedback(.success, ...)`: the route-ready confirmation banner (`VisualMomentViews.swift`'s `RouteReadyMomentView`), firing once per new confirmation.
- Haptic PLAYBACK cannot be observed by any headless XCTest API -- this is proven only as a text contract (the right modifier/trigger/feedback-kind at the right call sites, and nowhere else) plus a green compile+test run. Whether it actually *feels* right (timing, intensity, whether it reads as "sparing" in practice) is a human, on-device judgment that belongs to TC-051 (physical field test) and/or a future manual haptics pass -- not claimed here.

### What stays open

- **TC-056 (native VoiceOver traversal, human-observed)** -- unchanged, still `blocked` in `qa/test-cases.csv`. Everything above proves the SOURCE carries the right labels, hints, traits, grouping, and 44pt targets; it does not prove a real VoiceOver user can traverse the app comfortably end to end, notice the haptics, or find the wording clear when spoken aloud. A human pass (Simulator Accessibility Inspector + VoiceOver, or a real device) covering all four screens above -- Map, List, Detail, Settings -- plus the haptic confirmations, is still required before TC-056 can move to `pass`.
- **TC-051 (physical field test)** -- unchanged, still `blocked`; haptic feel while actually driving/parking is squarely in its scope, not this automated pass's.
