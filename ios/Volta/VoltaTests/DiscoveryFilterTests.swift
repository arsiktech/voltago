import Foundation
import MapKit
import XCTest
@testable import Volta

@MainActor
final class DiscoveryFilterTests: XCTestCase {
    func testLaunchDefaultsHaveNoActiveFilters() {
        let state = AppState()
        XCTAssertFalse(state.hasActiveFilters)
    }

    func testClearingFreeOnlyRestoresPaidResults() throws {
        try assertFilterCanBeCleared { $0.freeOnly = true }
    }

    func testClearingPriceCapRestoresResultsAboveTheCap() throws {
        try assertFilterCanBeCleared { $0.maxPrice = 0.25 }
    }

    func testClearingConnectorSelectionRestoresOtherConnectors() throws {
        try assertFilterCanBeCleared { $0.connectorTypes = ["CCS", "CHAdeMO"] }
    }

    func testClearingPowerFloorRestoresSlowerChargers() throws {
        try assertFilterCanBeCleared { $0.minimumPowerKW = 50 }
    }

    func testClearingSavedOnlyRestoresUnsavedResults() throws {
        try assertFilterCanBeCleared { $0.showingSavedOnly = true }
    }

    func testClearingLocalFiltersKeepsLiveModeAndCurrentEvidence() throws {
        let station = try TestDecode.station("""
        {"id":"live","name":"Current live row","lat":39.47,"lon":-0.37,
         "lead":false,"available":1,"decision":{"level":"go"},
         "verification":{"level":"live","live_verified":true}}
        """)
        let state = AppState()
        state.mode = .live
        state.liveState = .ok
        state.liveQuality = .fresh
        state.stations = [station]
        state.freeOnly = true
        state.connectorTypes = ["CCS"]
        state.minimumPowerKW = 50
        state.showingSavedOnly = true

        // With the price cap already at its default, local recovery has no reason
        // to switch mode, start another request, or clear current telemetry.
        state.clearFilters()

        assertDefaultFilters(state)
        XCTAssertEqual(state.mode, .live)
        XCTAssertEqual(state.liveState, .ok)
        XCTAssertEqual(state.liveQuality, .fresh)
        XCTAssertEqual(state.stations, [station])
        XCTAssertEqual(state.liveReadyCount, 1)
    }

    func testClearingAllFiltersPreservesMapFavoritesSelectionAndEvidence() throws {
        let suiteName = "VoltaTests.DiscoveryFilters.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        let originalDefaults = FavoritesStore.defaults
        FavoritesStore.defaults = defaults
        defer {
            FavoritesStore.defaults = originalDefaults
            defaults.removePersistentDomain(forName: suiteName)
        }
        let favorites = [FavoriteRecord(id: "cached", name: "Saved charger", savedAt: Date(timeIntervalSince1970: 100))]
        FavoritesStore.save(favorites)

        let response = try TestDecode.chargersResponse("""
        {
          "stations": [
            {"id":"fresh","name":"Fresh","lat":39.47,"lon":-0.37,
             "lead":false,"available":1,"price_min":0.35,
             "decision":{"level":"go"},
             "verification":{"level":"live","live_verified":true}},
            {"id":"cached","name":"Saved charger","lat":39.48,"lon":-0.38,
             "lead":false,"cache_stale":true,"available":2,
             "decision":{"level":"go"},
             "verification":{"level":"live","live_verified":true}}
          ],
          "leads": [
            {"id":"lead","name":"Source evidence","lat":39.49,"lon":-0.39,
             "lead":true,"reliability":"official_directory",
             "verification":{"level":"official","live_verified":false},
             "charge_claim":{"kind":"price","source_label":"Charging source"},
             "parking_claim":{"kind":"unknown","source_label":"Parking source"}}
          ],
          "warning":"Cached rows need a source check.",
          "cache":{"hit":true,"stale":true,"mixed_live":true,"age_seconds":120}
        }
        """)
        let state = AppState()
        // Overview isolates filter recovery from network refresh; live price changes
        // remain covered by AppState's transport tests and normal refetch path.
        let region = MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: 39.47, longitude: -0.37),
            span: MKCoordinateSpan(latitudeDelta: 0.5, longitudeDelta: 0.6)
        )
        state.visibleRegion = region
        state.camera = .region(region)
        state.userCoordinate = CLLocationCoordinate2D(latitude: 39.46, longitude: -0.36)
        state.locationState = .located
        state.stations = response.stations
        state.leads = response.leads
        state.liveCache = response.cache
        state.liveQuality = .mixed
        state.liveState = .ok
        state.warning = response.warning
        state.focusedStationID = "cached"
        state.selected = response.stations.last
        state.searchText = "Valencia"
        state.freeOnly = true
        state.maxPrice = 0.25
        state.connectorTypes = ["CCS"]
        state.minimumPowerKW = 50
        state.showingSavedOnly = true

        XCTAssertTrue(state.hasActiveFilters)
        state.clearFilters()
        state.clearFilters()  // Repeated recovery must remain harmless.

        assertDefaultFilters(state)
        XCTAssertEqual(state.mode, .overview)
        XCTAssertEqual(state.visibleRegion.center.latitude, region.center.latitude)
        XCTAssertEqual(state.visibleRegion.center.longitude, region.center.longitude)
        XCTAssertEqual(state.visibleRegion.span.latitudeDelta, region.span.latitudeDelta)
        XCTAssertEqual(state.visibleRegion.span.longitudeDelta, region.span.longitudeDelta)
        let cameraRegion = try XCTUnwrap(state.camera.region)
        XCTAssertEqual(cameraRegion.center.latitude, region.center.latitude)
        XCTAssertEqual(cameraRegion.center.longitude, region.center.longitude)
        XCTAssertEqual(cameraRegion.span.latitudeDelta, region.span.latitudeDelta)
        XCTAssertEqual(cameraRegion.span.longitudeDelta, region.span.longitudeDelta)
        XCTAssertEqual(state.userCoordinate?.latitude, 39.46)
        XCTAssertEqual(state.userCoordinate?.longitude, -0.36)
        XCTAssertEqual(state.locationState, .located)
        XCTAssertEqual(state.favoriteIDs, ["cached"])
        XCTAssertEqual(FavoritesStore.load(), favorites)
        XCTAssertEqual(state.focusedStationID, "cached")
        XCTAssertEqual(state.selected, response.stations.last)
        XCTAssertEqual(state.searchText, "Valencia")
        XCTAssertEqual(state.stations, response.stations)
        XCTAssertEqual(state.leads, response.leads)
        XCTAssertEqual(state.liveCache, response.cache)
        XCTAssertEqual(state.liveQuality, .mixed)
        XCTAssertEqual(state.liveState, .ok)
        XCTAssertEqual(state.warning, response.warning)
        XCTAssertEqual(state.stations.filter(\.isRouteReady).map(\.id), ["fresh"])
        XCTAssertFalse(try XCTUnwrap(state.stations.last).isRouteReady)
        XCTAssertFalse(try XCTUnwrap(state.leads.first).isRouteReady)
    }

    private func assertFilterCanBeCleared(
        _ configure: @MainActor (AppState) -> Void,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws {
        let station = try TestDecode.station("""
        {"id":"paid","name":"Paid Type 2","lat":39.47,"lon":-0.37,
         "lead":false,"price_min":0.35,"is_free":false,"max_power_kw":11,
         "connector_labels":["Type 2"],"available":1,"decision":{"level":"go"},
         "verification":{"level":"live","live_verified":true}}
        """)
        let state = AppState()
        state.favoriteIDs = []
        state.stations = [station]
        XCTAssertEqual(state.listRows.map(\.id), [station.id], file: file, line: line)

        configure(state)
        XCTAssertTrue(state.hasActiveFilters, file: file, line: line)
        XCTAssertTrue(state.listRows.isEmpty, "The chosen constraint must actually exclude the fixture", file: file, line: line)

        state.clearFilters()

        assertDefaultFilters(state, file: file, line: line)
        XCTAssertEqual(state.listRows.map(\.id), [station.id], file: file, line: line)
        XCTAssertEqual(state.stations, [station], file: file, line: line)
    }

    private func assertDefaultFilters(
        _ state: AppState,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertFalse(state.hasActiveFilters, file: file, line: line)
        XCTAssertFalse(state.freeOnly, file: file, line: line)
        XCTAssertEqual(state.maxPrice, 5.0, file: file, line: line)
        XCTAssertTrue(state.connectorTypes.isEmpty, file: file, line: line)
        XCTAssertEqual(state.minimumPowerKW, 0, file: file, line: line)
        XCTAssertFalse(state.showingSavedOnly, file: file, line: line)
    }
}
