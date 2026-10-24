import XCTest
@testable import Volta

/// VG-17: the last area and saved chargers stay readable offline, always marked stale,
/// in finite storage that survives corruption and schema changes safely.
final class OfflineStationCacheTests: XCTestCase {
    private var folder: URL!

    private let response = Data("""
    {
      "stations": [{"id": "live-1", "name": "Live One", "lat": 39.47, "lon": -0.38, "available": 2}],
      "leads": [
        {"id": "lead-1", "name": "Lead One", "lat": 41.38, "lon": 2.16, "lead": true, "price_min": 0.31},
        {"id": "lead-2", "name": "Lead Two", "lat": 37.38, "lon": -5.98, "lead": true}
      ]
    }
    """.utf8)

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("offline-\(UUID().uuidString)")
        OfflineStationCache.directoryOverride = folder
    }

    override func tearDownWithError() throws {
        OfflineStationCache.clear()
        OfflineStationCache.directoryOverride = nil
    }

    func testAreaRoundTripMarksEveryRowStale() throws {
        let savedAt = Date(timeIntervalSince1970: 1_792_874_000)
        OfflineStationCache.storeArea(response, at: savedAt)
        let snapshot = try XCTUnwrap(OfflineStationCache.loadArea(decoder: VoltaAPI.decoder))
        XCTAssertEqual(snapshot.savedAt, savedAt)
        XCTAssertEqual(Set(snapshot.stations.map(\.id)), ["live-1", "lead-1", "lead-2"])
        XCTAssertTrue(snapshot.stations.allSatisfy { $0.cacheStale == true })
        // Leads stay "Not live"; a cached live row becomes stale. Neither shows availability.
        XCTAssertEqual(snapshot.stations.first { $0.id == "live-1" }?.trustKind, .stale)
        XCTAssertFalse(snapshot.stations.contains { $0.canShowLiveAvailability })
        XCTAssertFalse(snapshot.stations.contains { $0.isRouteReady })
    }

    func testSavedChargersAccumulateAndDropWhenUnsaved() throws {
        OfflineStationCache.storeSaved(from: response, favoriteIDs: ["lead-1", "live-1"])
        let other = Data(#"{"stations": [], "leads": [{"id": "far-1", "name": "Far", "lat": 43.0, "lon": -8.0, "lead": true}]}"#.utf8)
        OfflineStationCache.storeSaved(from: other, favoriteIDs: ["lead-1", "far-1"])
        let snapshot = try XCTUnwrap(OfflineStationCache.loadSaved(decoder: VoltaAPI.decoder))
        XCTAssertEqual(Set(snapshot.stations.map(\.id)), ["lead-1", "far-1"])
        XCTAssertTrue(snapshot.stations.allSatisfy { $0.cacheStale == true })
    }

    func testOversizedAreaIsNotStored() {
        let big = Data(repeating: 0x20, count: OfflineStationCache.maxAreaBytes + 1)
        OfflineStationCache.storeArea(big)
        XCTAssertNil(OfflineStationCache.loadArea(decoder: VoltaAPI.decoder))
    }

    func testCorruptCacheIsDiscardedNotTrusted() throws {
        OfflineStationCache.storeArea(response)
        let url = folder.appendingPathComponent("area.json")
        try Data("not a plist".utf8).write(to: url)
        XCTAssertNil(OfflineStationCache.loadArea(decoder: VoltaAPI.decoder))
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    func testUndecodablePayloadIsDiscarded() throws {
        OfflineStationCache.storeArea(Data("[1, 2, 3]".utf8))
        XCTAssertNil(OfflineStationCache.loadArea(decoder: VoltaAPI.decoder))
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.appendingPathComponent("area.json").path))
    }

    func testEmptyCacheReturnsNothing() {
        XCTAssertNil(OfflineStationCache.loadArea(decoder: VoltaAPI.decoder))
        XCTAssertNil(OfflineStationCache.loadSaved(decoder: VoltaAPI.decoder))
    }

    func testSavedChargersAreCapped() throws {
        let rows = (0 ..< (OfflineStationCache.maxSavedStations + 20)).map {
            #"{"id": "s\#($0)", "name": "S", "lat": 40.0, "lon": -3.0, "lead": true}"#
        }
        let data = Data(#"{"stations": [], "leads": [\#(rows.joined(separator: ","))]}"#.utf8)
        OfflineStationCache.storeSaved(from: data, favoriteIDs: Set((0 ..< 300).map { "s\($0)" }))
        let snapshot = try XCTUnwrap(OfflineStationCache.loadSaved(decoder: VoltaAPI.decoder))
        XCTAssertEqual(snapshot.stations.count, OfflineStationCache.maxSavedStations)
    }
}
