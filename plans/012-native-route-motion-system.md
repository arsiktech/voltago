# 012 — Make native route interactions immediate and spatially coherent

> **Historical completed plan.** The implementation notes below describe the July
> work. Use [current contracts](../docs/backend-engine.md) and current code for subsequent changes; the
> original file scopes and STOP rules are retired. See [the index](README.md).

- **Status**: DONE
- **Commit**: working tree (2026-07-17)
- **Severity**: HIGH
- **Category**: Motion quality, hierarchy, reduced motion
- **Estimated scope**: 4–6 Swift files, about 180 lines changed

## Problem

The native map currently mixes unrelated motion treatments and material styles. A selected station only scales its price pill, the carousel can jump independently, the route-ready moment uses a 1.15-second particle expansion, and important controls rely on interactive glass. The result feels like a SwiftUI prototype instead of one spatial system: a map object, its card, and the route action do not appear to be the same object.

```swift
// ios/Volta/Volta/Theme.swift:43 — current
.scaleEffect(configuration.isPressed && !reduceMotion ? pressedScale : 1)
.opacity(configuration.isPressed ? 0.78 : 1)
.animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: configuration.isPressed)
```

```swift
// ios/Volta/Volta/VisualMomentViews.swift:205 — current
.onAppear {
    expanded = false
    withAnimation(.easeOut(duration: 1.15)) { expanded = true }
}
```

```swift
// ios/Volta/Volta/RootView.swift:66 — current
.buttonStyle(.plain)
.glassEffect(.regular.interactive(), in: .capsule)
```

## Target

Create one native motion vocabulary in `Theme.swift` and apply it to map focus, filter value changes, station-card selection, and the route-ready confirmation:

```swift
enum VoltaMotion {
    static let press = Animation.easeOut(duration: 0.12)
    static let state = Animation.easeInOut(duration: 0.20)
    static let spatial = Animation.spring(duration: 0.28, bounce: 0)
    static let delight = Animation.easeOut(duration: 0.28)
}
```

- Press feedback completes in 120ms and changes opacity even with Reduce Motion.
- State crossfades complete in 200ms.
- Pin/card spatial selection uses a non-bouncy 280ms spring.
- The one-shot route-ready flourish completes in 280ms, never loops, and is absent with Reduce Motion.
- Reduced Motion retains opacity and color feedback while removing scale, offset, and particle travel.
- All animated properties are opacity, scale, offset, or color; no layout dimension is animated.
- Solid, high-contrast product surfaces replace interactive glass so motion communicates hierarchy instead of material shimmer.

## Repo conventions to follow

- Motion and color tokens live in `ios/Volta/Volta/Theme.swift`.
- `VoltaPressButtonStyle` already reads `accessibilityReduceMotion` and must remain the single press-feedback implementation.
- Haptics stay sparse: selection only for a pin/card/filter choice, success only for a genuinely route-ready result.
- `AppState.focus(_:)` remains the single source of truth for map-to-carousel selection.
- Do not weaken the fail-closed route-ready calculation or hide stale/degraded labels.

## Steps

1. Add `VoltaMotion` and solid surface/border tokens in `Theme.swift`; keep the existing 120ms press duration and preserve opacity feedback under Reduce Motion.
2. Replace the brand, map buttons, and three-filter strip glass effects in `RootView.swift` with a solid, domain-specific instrument surface. Animate only changing filter values, not the menu layout.
3. Give selected map markers a 280ms non-bouncy focus transition and a static selected halo under Reduce Motion. Use the existing `focusedStationID` to keep pins and the horizontal station carousel synchronized.
4. Replace the generic carousel container in `SheetView.swift` with a route-ticket presentation. Animate card selection with the same spatial token and preserve VoiceOver reading order.
5. Reduce `RouteReadyParticles` from 1.15 seconds to 280ms; replace its glass container with the same solid route surface; keep success haptic exactly once.
6. Remove user-visible placeholder fallbacks: omit unavailable remote-photo chrome instead of showing “Photo unavailable”, and replace the release-placeholder privacy paragraph with current operational disclosure and configured hosted links.

## Boundaries

- Do NOT change backend source selection, pricing, trust, or route-readiness logic.
- Do NOT add packages, raster stock imagery, continuous animation, or glass effects.
- Do NOT animate sheet height, map camera on polling refresh, or list layout dimensions.
- Do NOT touch `data/free_intelligence.json` or unrelated worktree changes.
- If `focusedStationID`, route-ready truth, or legal URL configuration differs from the inspected code, STOP and reconcile instead of inventing alternate state.

## Verification

- **Mechanical**:
  - `xcodebuild test -project ios/Volta/Volta.xcodeproj -scheme Volta -destination 'platform=iOS Simulator,id=BEAAC37A-0DAD-4DEF-B8C6-D356975A549B' -derivedDataPath /private/tmp/volta-ios27-xcodebuildmcp`
  - Run the app through XcodeBuildMCP `build_run_sim`, then inspect `snapshot_ui` and `screenshot`.
  - Run the focused native source/contract tests and update the QA iteration artifacts; preserve known external/manual blockers.
- **Feel check**: on the iPhone 17 iOS 27 simulator:
  - Tap each filter repeatedly: the value updates immediately without the whole strip resizing or flashing.
  - Tap a visible charger pin: one light haptic fires, the pin focus and corresponding carousel ticket settle together in under 300ms, and polling does not replay the transition.
  - Swipe the carousel: the corresponding map selection updates without a camera yank.
  - Trigger a route-ready result: the confirmation appears once, its flourish is over in 280ms, and dismiss is immediate.
  - Enable Reduce Motion: scale/translation/particles disappear, while selection color and opacity remain clear.
- **Done when**: the main map-to-route journey uses one solid visual hierarchy, every core control is functional, no placeholder release copy is visible, motion remains under 300ms, and simulator evidence confirms the iOS 27 layout at compact and accessibility text sizes.
