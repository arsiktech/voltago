# Module: iOS Client

The native iOS client in [ios/Volta](../../../ios/Volta) is a SwiftUI/MapKit
consumer of the same Volta backend API. It mirrors the web app's data-honesty
rules for overview, live fetch, fail-closed behavior, and source tiers.

## Responsibilities

- Show national leads-only overview by default.
- Fetch live data by viewport, user location, or geocoded place.
- Keep source-backed leads visible while live fetch runs.
- Fail closed by dropping stale live rows after live fetch failure.
- Render MapKit price pills/clusters and a persistent station sheet.
- Let the user configure the backend base URL for simulator, LAN, or Tailscale.

## Key Files

- [ios/README.md](../../../ios/README.md) - iOS setup/run guide.
- [VoltaApp.swift](../../../ios/Volta/Volta/VoltaApp.swift) - app entry.
- [AppState.swift](../../../ios/Volta/Volta/AppState.swift) - orchestration,
  filters, search, location, auto-refresh, fail-closed rules.
- [VoltaAPI.swift](../../../ios/Volta/Volta/VoltaAPI.swift) - async API client
  for `/api/chargers` and `/api/geocode`.
- [Models.swift](../../../ios/Volta/Volta/Models.swift) - Codable API models and
  presentation helpers ported from frontend logic.
- [RootView.swift](../../../ios/Volta/Volta/RootView.swift) - map layer.
- [SheetView.swift](../../../ios/Volta/Volta/SheetView.swift) - persistent sheet.
- [StationViews.swift](../../../ios/Volta/Volta/StationViews.swift) - station rows
  and details.

## Public API

- `VoltaAPI.overview(maxPrice:)`
- `VoltaAPI.live(bbox:coords:maxPrice:availableOnly:)`
- `VoltaAPI.geocode(_:)`
- `AppState.start()`
- `AppState.fetchLive(reason:)`
- `AppState.loadOverview()`

## Internal Structure

`AppState` owns mode, live state, stations, leads, filters, visible map region,
location, and search. The simulator default base URL is `http://127.0.0.1:8773`;
device builds default to `http://your-mac.local:8773` and can be changed in Settings.

## Dependencies

- **Uses:** SwiftUI, MapKit, CoreLocation, URLSession, Volta backend.
- **Used by:** native iPhone workflow and manual VoiceOver/field-testing gates.

## Notable Patterns / Gotchas

- Simulator loopback and real-device networking differ; the iOS README calls out
  Tailscale/LAN paths.
- iOS VoiceOver manual evidence remains a completion gate.
- The current working tree has unrelated iOS asset/project changes; this wiki
  did not modify them.
