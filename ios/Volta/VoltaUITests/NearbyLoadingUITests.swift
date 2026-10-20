import XCTest

/// Real locate-control/Core Location coverage with live responses held back. Configure the
/// selected Simulator's location before running serially; no app-only coordinate override.
final class NearbyLoadingUITests: XCTestCase {
    private var server: StubServer!

    override func setUpWithError() throws {
        continueAfterFailure = false
        executionTimeAllowance = 120
        server = StubServer()
        try server.start()
        server.configureOverviewResponses([.ok(#"{"stations":[],"leads":[]}"#)])
        server.configureNearbyOverviewResponse(.ok(StubFixtures.overviewChargersJSON))
        server.configureLiveResponses([
            .init(status: "200 OK", body: StubFixtures.liveChargersJSON, delay: 30),
        ])
    }

    override func tearDownWithError() throws {
        server?.stop()
        server = nil
    }

    func testLocateShowsKnownChargersBeforeLiveProvidersInLightAppearance() {
        locateFlow(appearance: "Light")
    }

    func testLocateShowsKnownChargersBeforeLiveProvidersInDarkAppearance() {
        locateFlow(appearance: "Dark")
    }

    private func locateFlow(appearance: String) {
        let app = XCUIApplication()
        app.resetAuthorizationStatus(for: .location)
        app.launchArguments = [
            "-voltaBaseURL", server.baseURLString,
            "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
            "-VoltaUITestDiscoveryAppearance", appearance,
            "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryL",
        ]
        app.launch()
        let locate = app.buttons["locateButton"]
        XCTAssertTrue(locate.waitForExistence(timeout: 15))
        let known = app.buttons["stationRow-uitest-lead-01"]
        XCTAssertFalse(known.exists, "Launch data must not preload the nearby fixture")
        locate.tap()

        let allow = app.alerts.buttons["Allow While Using App"]
        if allow.waitForExistence(timeout: 5) { allow.tap() }
        let started = ContinuousClock.now
        XCTAssertTrue(known.waitForExistence(timeout: 10), "Known chargers waited on the held live response")
        XCTAssertTrue(known.label.localizedCaseInsensitiveContains("Not live"))
        XCTAssertFalse(known.label.localizedCaseInsensitiveContains("Go now"))
        let timing = XCTAttachment(string: "Permission resolved to known nearby row: \(ContinuousClock.now - started)")
        timing.lifetime = .keepAlways
        add(timing)

        let liveRequests = server.recordedRequestPaths().filter(Self.isLive)
        XCTAssertEqual(liveRequests.count, 1, "The camera animation restarted the same live lookup")
        XCTAssertTrue(server.deliveredResponsePaths().filter(Self.isLive).isEmpty,
                      "Known chargers must appear before live providers return")
        XCTAssertFalse(app.buttons["stationRow-uitest-live-go"].exists)
        let screenshot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screenshot.name = "nearby-before-live-\(appearance.lowercased())"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    private static func isLive(_ path: String) -> Bool {
        path.hasPrefix("/api/chargers?") && !path.contains("overview=true")
    }
}
