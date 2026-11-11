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

    /// Freshness honesty: a response without any saved charger must not make old saved rows
    /// look newly saved.
    func testSavedDateStaysWhenAResponseRefreshesNoSavedCharger() throws {
        let first = Date(timeIntervalSince1970: 1_792_874_000)
        OfflineStationCache.storeSaved(from: response, favoriteIDs: ["lead-1"], at: first)
        let unrelated = Data(#"{"stations": [], "leads": [{"id": "x", "name": "X", "lat": 40.0, "lon": -3.0, "lead": true}]}"#.utf8)
        OfflineStationCache.storeSaved(from: unrelated, favoriteIDs: ["lead-1"], at: first.addingTimeInterval(86_400 * 14))
        let snapshot = try XCTUnwrap(OfflineStationCache.loadSaved(decoder: VoltaAPI.decoder))
        XCTAssertEqual(snapshot.stations.map(\.id), ["lead-1"])
        XCTAssertEqual(snapshot.savedAt, first)
    }

    /// With some saved rows refreshed and others not, the date is that of the oldest kept row.
    func testSavedDateIsTheOldestKeptRowAfterAPartialRefresh() throws {
        let first = Date(timeIntervalSince1970: 1_792_874_000)
        let second = first.addingTimeInterval(86_400 * 7)
        let third = first.addingTimeInterval(86_400 * 14)
        OfflineStationCache.storeSaved(from: response, favoriteIDs: ["lead-1", "far-1"], at: first)
        let far = Data(#"{"stations": [], "leads": [{"id": "far-1", "name": "Far", "lat": 43.0, "lon": -8.0, "lead": true}]}"#.utf8)
        OfflineStationCache.storeSaved(from: far, favoriteIDs: ["lead-1", "far-1"], at: second)
        XCTAssertEqual(try XCTUnwrap(OfflineStationCache.loadSaved(decoder: VoltaAPI.decoder)).savedAt, first)
        // Refreshing lead-1 leaves far-1 (saved at `second`) as the oldest row.
        OfflineStationCache.storeSaved(from: response, favoriteIDs: ["lead-1", "far-1"], at: third)
        XCTAssertEqual(try XCTUnwrap(OfflineStationCache.loadSaved(decoder: VoltaAPI.decoder)).savedAt, second)
        // Unsaving far-1 drops it, so only lead-1's refresh time remains.
        OfflineStationCache.storeSaved(from: Data("{}".utf8), favoriteIDs: ["lead-1"], at: third.addingTimeInterval(60))
        XCTAssertEqual(try XCTUnwrap(OfflineStationCache.loadSaved(decoder: VoltaAPI.decoder)).savedAt, third)
    }

    /// A saved.json written before per-row times existed still loads, and its rows keep the
    /// file's original date until a response refreshes them.
    func testSavedFileWithoutPerRowTimesStillLoadsAndKeepsItsDate() throws {
        struct LegacyEnvelope: Codable { var schema: Int; var savedAt: Date; var payload: Data }
        let old = Date(timeIntervalSince1970: 1_792_874_000)
        let rows = Data(#"[{"id": "lead-1", "name": "Lead One", "lat": 41.38, "lon": 2.16, "lead": true}]"#.utf8)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try PropertyListEncoder().encode(LegacyEnvelope(schema: OfflineStationCache.schemaVersion, savedAt: old, payload: rows))
            .write(to: folder.appendingPathComponent("saved.json"))
        let loaded = try XCTUnwrap(OfflineStationCache.loadSaved(decoder: VoltaAPI.decoder))
        XCTAssertEqual(loaded.savedAt, old)
        XCTAssertEqual(loaded.stations.map(\.id), ["lead-1"])

        OfflineStationCache.storeSaved(from: Data("{}".utf8), favoriteIDs: ["lead-1"], at: old.addingTimeInterval(86_400))
        let kept = try XCTUnwrap(OfflineStationCache.loadSaved(decoder: VoltaAPI.decoder))
        XCTAssertEqual(kept.stations.map(\.id), ["lead-1"])
        XCTAssertEqual(kept.savedAt, old)
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
