import XCTest

/// VG-19/VG-20: the Watch action appears only on fresh, eligible live chargers when the server
/// enables watches, and it always says that an alert does not reserve a connector.
final class StationWatchUITests: XCTestCase {
    private var server: StubServer!
    private let token = String(repeating: "ab", count: 32)

    override func setUpWithError() throws {
        continueAfterFailure = false
        server = StubServer()
        try server.start()
    }

    override func tearDownWithError() throws {
        server?.stop()
        server = nil
    }

    private func watchableLivePayload() throws -> String {
        var payload = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(StubFixtures.liveChargersJSON.utf8)) as? [String: Any])
        var station = try XCTUnwrap((payload["stations"] as? [[String: Any]])?.first)
        station["live_status"] = ["valid": true, "feed": "endolla", "checked_at": "2026-10-24T21:40:00Z"]
        station["connectors"] = [
            ["connector_id": "3748:11240:1", "label": "CCS2", "status": "CHARGING", "power_kw": 50],
            ["connector_id": "3748:11241:1", "label": "CCS2", "status": "AVAILABLE", "power_kw": 50],
        ]
        payload["stations"] = [station]
        payload["leads"] = []
        return try XCTUnwrap(String(data: JSONSerialization.data(withJSONObject: payload), encoding: .utf8))
    }

    private func openLiveDetail(arguments: [String]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-voltaBaseURL", server.baseURLString, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"] + arguments
        app.launch()
        let search = app.textFields["discoverySearchField"]
        XCTAssertTrue(search.waitForExistence(timeout: 15))
        search.tap()
        search.typeText(StubFixtures.geocodeQueryText)
        let place = app.buttons.matching(NSPredicate(format: "label CONTAINS[cd] %@", StubFixtures.geocodeResultName)).firstMatch
        XCTAssertTrue(place.waitForExistence(timeout: 10))
        place.tap()
        let row = app.buttons["stationRow-uitest-live-go"]
        XCTAssertTrue(row.waitForExistence(timeout: 15))
        row.tap()
        XCTAssertTrue(app.navigationBars["Charger details"].waitForExistence(timeout: 10))
        return app
    }

    private func scrollTo(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<12 {
            if element.exists && element.isHittable { return }
            app.swipeUp()
        }
    }

    func testWatchStartsWithTokenAndStopsWithoutClaimingAReservation() throws {
        server.configureLiveResponses([.ok(try watchableLivePayload())])
        server.configureWatchRoutes(
            config: #"{"enabled": true, "push_configured": false, "eligible_feeds": ["endolla", "spirii"], "default_minutes": 120, "max_minutes": 360, "connector_types": ["CCS2"]}"#,
            create: StubServer.ResponseStep(status: "201 Created", body: #"{"ok": true, "watch": {"id": "uitestwatch0000000001", "station_id": "uitest-live-go", "station_name": "UITest", "active": true, "expires_at": "2030-01-01T12:00:00Z", "push_configured": false, "free_now": ["3748:11241:1"]}}"#),
            record: #"{"watch": {"id": "uitestwatch0000000001", "station_id": "uitest-live-go", "active": true}}"#
        )
        let app = openLiveDetail(arguments: ["-VoltaUITestPushToken", token])
        let watch = app.buttons["watchChargerButton"]
        scrollTo(watch, in: app)
        XCTAssertTrue(watch.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["An alert never reserves a connector. It may be taken before you arrive."].exists)
        watch.tap()
        let active = app.staticTexts["watchActiveNote"]
        XCTAssertTrue(active.waitForExistence(timeout: 10))
        XCTAssertTrue(active.label.contains("Watching"))
        XCTAssertTrue(server.recordedRequestPaths().contains("/api/watches"), "\(server.recordedRequestPaths())")
        XCTAssertFalse(server.recordedRequestPaths().contains { $0.contains(token) }, "The device token must never be in a URL")
        let stop = app.buttons["stopWatchButton"]
        scrollTo(stop, in: app)
        stop.tap()
        XCTAssertTrue(app.buttons["watchChargerButton"].waitForExistence(timeout: 10))
        XCTAssertTrue(server.recordedRequestPaths().contains("/api/watches/uitestwatch0000000001"))
    }

    func testNoWatchActionWhenTheServerDoesNotOfferWatches() throws {
        server.configureLiveResponses([.ok(try watchableLivePayload())])
        let app = openLiveDetail(arguments: ["-VoltaUITestPushToken", token])
        for _ in 0..<6 { app.swipeUp() }
        XCTAssertFalse(app.buttons["watchChargerButton"].exists)
    }
}
