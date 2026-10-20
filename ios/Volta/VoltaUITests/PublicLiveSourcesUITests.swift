import XCTest

/// Synthetic transport fixtures exercise the shared source disclosure, not real-site availability.
final class PublicLiveSourcesUITests: XCTestCase {
    private var server: StubServer!

    override func setUpWithError() throws {
        continueAfterFailure = false
        server = StubServer()
        try server.start()
    }

    override func tearDownWithError() throws {
        server?.stop()
        server = nil
    }

    func testEndollaLiveStatusAndThreeClocks() throws {
        try inspectSource(name: "Barcelona Endolla live", attribution: "Ajuntament de Barcelona", licensed: true)
    }

    func testSpiriiStatusCreditsOperatorWithoutTariff() throws {
        try inspectSource(name: "Spirii AFIR", attribution: "Spirii; PowerGo", licensed: false)
    }

    private func inspectSource(name: String, attribution: String, licensed: Bool) throws {
        var payload = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(StubFixtures.liveChargersJSON.utf8)) as? [String: Any])
        var station = try XCTUnwrap((payload["stations"] as? [[String: Any]])?.first)
        station["name"] = "UITest public status disclosure"
        station["price_min"] = NSNull()
        station["is_free"] = false
        station["charge_claim"] = NSNull()
        station["parking_claim"] = NSNull()
        station["parking"] = ["kind": "unknown", "label": "Parking unknown"]
        station["verification"] = ["live_verified": true, "location_verified": true, "price_verified": false,
                                   "level": "live", "label": "Public live status",
                                   "note": "Status feed only. Charging and parking prices remain unverified."]
        station["decision"] = ["level": "lead", "label": "Price lead", "reason": "No tariff from this status feed."]
        var source: [String: Any] = [
            "name": name, "url": "https://example.com/public-status-fixture",
            "attribution": attribution,
            "source_fetched_at": "2026-10-20T16:30:00Z",
            "source_published_at": "2026-10-20T16:29:00Z",
            "source_last_updated": "2024-01-01T00:00:00Z",
        ]
        if licensed {
            source["license"] = "CC BY 4.0"
            source["license_url"] = "https://creativecommons.org/licenses/by/4.0/"
        }
        station["sources"] = [source]
        station["primary_source"] = source
        station["source"] = name
        station["source_url"] = source["url"]
        payload["stations"] = [station]
        payload["leads"] = []
        let body = try XCTUnwrap(String(data: JSONSerialization.data(withJSONObject: payload), encoding: .utf8))
        server.configureLiveResponses([.ok(body)])
        let app = XCUIApplication()
        app.launchArguments = [
            "-voltaBaseURL", server.baseURLString,
            "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
            "-VoltaUITestDiscoveryAppearance", licensed ? "Light" : "Dark",
        ]
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
        XCTAssertTrue(row.label.contains("2 free"), "Fresh status must use the ordinary live availability surface")
        XCTAssertFalse(row.label.contains("Go now"), "An unknown price must not turn this fixture route-ready")
        row.tap()
        XCTAssertTrue(app.navigationBars["Charger details"].waitForExistence(timeout: 10))
        let credit = app.staticTexts["Attribution: \(attribution)"]
        scrollTo(credit, in: app)
        XCTAssertTrue(credit.isHittable)
        if licensed { XCTAssertTrue(app.links["License: CC BY 4.0"].exists) }
        for label in [
            "Source updated: 2024-01-01T00:00:00Z",
            "Feed published: 2026-10-20T16:29:00Z",
            "Retrieved: 2026-10-20T16:30:00Z",
        ] {
            let clock = app.staticTexts[label]
            scrollTo(clock, in: app)
            XCTAssertTrue(clock.isHittable, "Missing separate source clock: \(label)")
        }
        let screenshot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screenshot.name = licensed ? "endolla-source-clocks-light" : "spirii-source-clocks-dark"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    private func scrollTo(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<15 {
            if element.exists && element.isHittable { return }
            app.swipeUp()
        }
    }
}
