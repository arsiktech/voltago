# Volta Visual System Pass

Current rules live in [the design system](../design-system.md); this is a dated record.

Date: 2026-07-06

## Source Images

Generated contact sheets are saved under `docs/visual-design/generated/`:

- `volta-visual-sheet-01-system.png` - map/pin/source-proof system language.
- `volta-visual-sheet-02-ui-moments.png` - practical mobile toast and empty-state shapes.
- `volta-visual-sheet-03-micro-assets.png` - small status object vocabulary.
- `volta-visual-sheet-04-celebration.png` - restrained route-ready particle ideas.

## External Map References

- [SuC Tracker map reference](suc-tracker-map-reference-2026-08-20.md) records
  the observed three-tier semantic zoom, time-of-use price controls, responsive
  findings, and the data-honesty boundaries for any Volta adaptation.

## Direction

Use the generated work as reference only. The runtime UI should stay code-native:
CSS/SVG/canvas in the web app and SwiftUI-native drawing in iOS. Avoid shipping
large decorative raster images inside the driving surface.

Native iOS retains a quiet route-ready confirmation:

- Trigger only when a real live `go` row appears after live/search/locate data.
- Use a short glass toast plus a small local particle ring.
- Respect reduced motion and keep the effect non-blocking.
- Never celebrate directory, stale, manual, or lead-only rows as route-ready.

## Do Not Use

- Full-screen celebration.
- Rainbow confetti or fireworks.
- Decorative background blobs/orbs.
- Fake app screenshots or unreadable generated UI text.
- Art that suggests live availability when the data is only a lead.

## Platform Note

Public Apple design material currently centers the Liquid Glass system introduced
for iOS 26. Treat the requested "iOS 27" feel as an iOS-forward Liquid Glass
direction, not as permission to rely on unavailable future APIs.

## Implemented

- Web (2026-09-05): the map uses only the visible area above/beside an opaque results
  panel. A compact trust badge and current driver guidance carry readiness; duplicate
  counters, timed toast/confetti, map pulses, and decorative result sweeps were removed.
  `VisualMoment.tsx` retains only the empty-state illustration.
- Web: OpenFreeMap Positron/Dark replaces CARTO, whose keyless raster responses showed
  an API-key watermark during this review. Provider attribution remains visible.
- Web: `isRouteReady()` still governs card decisions and the closest-route action.
  Coverage warnings remain visible, with bounded copy and no transport diagnostics.
- Native iOS: `ios/Volta/Volta/VisualMomentViews.swift` mirrors the same moment with
  SwiftUI-native marks, a compact particle burst, and Liquid Glass where available
  with material fallbacks.
- Native iOS: `ios/Volta/Volta/AppState.swift` and `Models.swift` apply the same
  live, non-lead route-ready boundary. Its native presentation is unchanged.

## Current web review

See [first-principles review](../../qa/first-principles-review-2026-09-05.md) for current
screenshots, checks, deletions, and explicit native/public-readiness limits.

## Historical verification (original visual-moment implementation)

- `npm --prefix frontend run test` - 46/46 passing.
- `npm --prefix frontend run build` - TypeScript and production Vite build passing.
- `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -m py_compile server.py scripts/*.py` - passing.
- XcodeBuildMCP `build_sim` for Volta on iPhone 17 / iOS 27 simulator - passing.
- Browser smoke with mocked `/api/chargers` on desktop 1440x1000 and mobile 390x844:
  route-ready status visible, `overflowX=0`, particle canvas present, and area pill
  hidden while the callout is active.

Smoke screenshots:

- `/private/tmp/volta-visual-desktop.png`
- `/private/tmp/volta-visual-mobile.png`

## Liquid Glass control adaptation (2026-09-05)

See [reference adaptation and evidence](liquid-glass-adaptation-2026-09-05.md). Web floating
controls use layered glass with opaque accessibility fallbacks; filters keep stationary
44px targets. Native map action buttons now use system Liquid Glass with solid accessibility
fallbacks. The native change is source-inspected only pending an available Xcode/iOS 27 setup.
