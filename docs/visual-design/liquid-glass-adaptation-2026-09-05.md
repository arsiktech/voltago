# Liquid Glass reference adaptation — 2026-09-05

Reference: [Appllama/liquid-glass-screens](https://github.com/Appllama/liquid-glass-screens),
inspected commit `07cb20db94a26790f38166a916bb668e796ad335`.
Volta base: `8f01eb0` plus the owner's existing uncommitted September 5 review.

## What carries over

The reference separates the scene, lens/body, rim light and semantic controls. Its motion
follows interaction, remains interruptible, and provides a Reduce Motion path. Its
[technical specification](https://github.com/Appllama/liquid-glass-screens/blob/07cb20db94a26790f38166a916bb668e796ad335/docs/MOTION_SPEC.md)
is useful as a study, not as a set of timings to paste into a driver's frequent actions.

Volta implements these ideas independently with its existing CSS and SwiftUI stack.
No upstream code, shaders, artwork, copy, dependencies, or exact motion constants were copied.
The reference is GPL-3.0 and has a separate NOTICE; its welcome-screen identity stays in
that reference. There is no new welcome gate, particle field, video, or background sphere.

| Surface | Adaptation |
| --- | --- |
| Web floating brand and area-search control | Adaptive layered fill, restrained rim light, inset edge shading, static backdrop blur; opaque fallback without backdrop-filter support. |
| Web filters | Reserved checkmark slot, stable geometry, minimum 44px height, static selection semantics, pointer press feedback and immediate keyboard response. Removes competing layout/spring/keyframe animations. |
| Web area-search action | Existing settling spring and reduced-motion fade; 44px minimum target; no compounded CSS/Framer press scale. |
| Native map search/location/area buttons | System regular interactive Liquid Glass grouped in GlassEffectContainer; opacity/material remains system rendered. |
| Native accessibility | Solid Theme.surface when Reduce Transparency or increased contrast is enabled; glass interaction removed under Reduce Motion; availability fallback retained. |

Native API source: Apple's [glassEffect](https://developer.apple.com/documentation/swiftui/view/glasseffect(_:in:))
and [GlassEffectContainer](https://developer.apple.com/documentation/swiftui/glasseffectcontainer).
The native content sheet, station cards, price labels and evidence badges remain opaque.
Web reduced-transparency, increased-contrast and forced-colors modes disable the backdrop
filter. Selected filters retain both a checkmark and aria-pressed; color is not their sole cue.

## Verification

- Frontend tests: 139/139; lint and production build pass. Existing MapLibre bundle-size
  advisory remains (no new package).
- `scripts/qa_liquid_glass_ui.js`: four theme/viewport combinations (390 and 1440px),
  rapid toggles, stationary control geometry, Space/Enter, retained focus, 44px targets,
  reduced motion/transparency, increased contrast, forced colors, stale/no-go state and
  no page errors. Uses an explicit stale empty API fixture and deterministic empty basemap;
  these screenshots verify controls, not real map tiles or live source availability.
- Existing `qa_mobile_ui.js`, `qa_ax_tree_contract.js`, and `qa_cdn_blocked_ui.js`: pass.
  The latter preserves list usability when map tiles/chunks fail and tests cached offline
  data with zero live/go-now claims.
- Native source contracts: 22/23 pass; existing empty-key localization failure persists:
  `AssertionError: 'en' not found in {} : '' has no en localization`.
- Xcode/Simulator compilation and runtime inspection are unavailable here. `xcodebuild
  -version` reports that the selected directory is CommandLineTools; no Xcode app is
  installed under /Applications. Native changes need a build and rendered accessibility
  check on the supported Xcode/iOS 27 setup before distribution.
- No full regression suite, external source canaries, native VoiceOver/field proof,
  deployment, TestFlight or public-release verification is claimed.

A browser check initially caught CSS optimization retaining only the prefixed backdrop
property, leaving computed backdrop-filter at none in Chrome. Placing the unprefixed
property last fixes the built output; the new rendered assertion protects that seam.

## Evidence

[Machine receipt](../../qa/browser-evidence/liquid-glass-2026-09-05/material-contract.json).

Current local overview, using the local API (not live telemetry):

![Light overview](../../qa/browser-evidence/liquid-glass-2026-09-05/overview-light.png)
![Dark overview](../../qa/browser-evidence/liquid-glass-2026-09-05/overview-dark.png)

Deterministic material states:
[light mobile](../../qa/browser-evidence/liquid-glass-2026-09-05/light-390.png),
[dark mobile](../../qa/browser-evidence/liquid-glass-2026-09-05/dark-390.png),
[light desktop](../../qa/browser-evidence/liquid-glass-2026-09-05/light-1440.png),
[dark desktop](../../qa/browser-evidence/liquid-glass-2026-09-05/dark-1440.png).
