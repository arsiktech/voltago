import XCTest
@testable import Volta

/// VG-19/VG-20 client rules: a watch is offered only on fresh, eligible live rows, the token
/// travels only in the POST body, and a declined permission leaves a clear explanation.
@MainActor
final class StationWatchTests: XCTestCase {
    private let feeds = ["endolla", "spirii"]

    private func station(_ extra: String = "", feed: String = "endolla", valid: Bool = true,
                         connectorID: String? = "3748:11240:1") throws -> Station {
        let connector = connectorID.map { #"{"connector_id": "\#($0)", "label": "CCS2", "status": "CHARGING"}"# } ?? #"{"label": "CCS2"}"#
        let json = """
        {"id": "endolla-1", "name": "AP BSM La Boqueria", "lat": 41.38, "lon": 2.17,
         "live_status": {"valid": \(valid), "feed": "\(feed)"},
         "connectors": [\(connector)]\(extra)}
        """
        return try VoltaAPI.decoder.decode(Station.self, from: Data(json.utf8))
    }

    func testOnlyFreshEligibleLiveRowsAreWatchable() throws {
        XCTAssertTrue(try station().isWatchable(feeds: feeds))
        XCTAssertFalse(try station(", \"cache_stale\": true").isWatchable(feeds: feeds))
        XCTAssertFalse(try station(", \"lead\": true").isWatchable(feeds: feeds))
        XCTAssertFalse(try station(feed: "etecnic").isWatchable(feeds: feeds))
        XCTAssertFalse(try station(valid: false).isWatchable(feeds: feeds))
        XCTAssertFalse(try station(connectorID: nil).isWatchable(feeds: feeds))
        var unavailable = try station()
        unavailable.responseTrust = .unavailable
        XCTAssertFalse(unavailable.isWatchable(feeds: feeds))
        XCTAssertFalse(try station().isWatchable(feeds: []))
    }

    func testCreateRequestBodyOmitsEmptyTypes() {
        let request = WatchCreateRequest(stationID: "s", lat: 1, lon: 2, deviceToken: "ab", connectorTypes: [], durationMinutes: 120)
        XCTAssertNil(request.jsonObject["connector_types"])
        XCTAssertEqual(request.jsonObject["device_token"] as? String, "ab")
        let typed = WatchCreateRequest(stationID: "s", lat: 1, lon: 2, deviceToken: "ab", connectorTypes: ["CCS2"], durationMinutes: 120)
        XCTAssertEqual(typed.jsonObject["connector_types"] as? [String], ["CCS2"])
    }

    func testServiceOffersNothingUntilTheServerEnablesWatches() throws {
        let service = WatchService(defaults: try XCTUnwrap(UserDefaults(suiteName: "watch-\(UUID())")))
        XCTAssertFalse(service.canOffer(try station()))
    }

    func testDeclinedPermissionExplainsAndSendsNothing() async throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "watch-\(UUID())"))
        let service = WatchService(defaults: defaults)
        service.setConfigForTesting(WatchConfig(enabled: true, pushConfigured: false, eligibleFeeds: feeds,
                                                defaultMinutes: 120, maxMinutes: 360))
        let st = try station()
        // An unreachable API would fail loudly if the service tried to call it.
        let api = VoltaAPI(baseURL: URL(string: "http://127.0.0.1:9")!)
        await service.start(st, connectorTypes: [], api: api, authorize: { false })
        guard case .message(let text) = service.phase(for: st.id) else { return XCTFail("expected a message") }
        XCTAssertTrue(text.contains("Notifications are off"))
    }

    func testStoredWatchesSurviveRelaunch() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "watch-\(UUID())"))
        let local = LocalWatch(id: "w1", stationID: "endolla-1", name: "La Boqueria", lat: 41.38, lon: 2.17, expiresAt: nil)
        defaults.set(try JSONEncoder().encode(["endolla-1": local]), forKey: "voltaActiveWatches")
        let service = WatchService(defaults: defaults)
        XCTAssertEqual(service.phase(for: "endolla-1"), .watching(local))
        XCTAssertEqual(service.watch(id: "w1"), local)
    }

    func testEndedWatchTextNeverClaimsTheConnectorIsStillFree() {
        let alerted = WatchRecord(id: "w", stationId: "s", active: false, endReason: "alerted")
        XCTAssertTrue(WatchService.endText(alerted).contains("Check the current status"))
        let outage = WatchRecord(id: "w", stationId: "s", active: false, endReason: "outage")
        XCTAssertTrue(WatchService.endText(outage).contains("live status unavailable"))
    }
}
