import CoreLocation
import XCTest

/// Real locate-control/Core Location coverage with live responses held back. The test sets
/// the simulated device location itself (Valencia, matching the nearby fixture) so it no
/// longer depends on whatever location the Simulator was left with; no app-only override.
final class NearbyLoadingUITests: XCTestCase {
    private var server: StubServer!

    override func setUpWithError() throws {
        continueAfterFailure = false
        executionTimeAllowance = 120
        server = StubServer()
        try server.start()
        server.configureOverviewResponses([.ok(#"{"stations":[],"leads":[]}"#)])
        let nearby = StubFixtures.overviewChargersJSON
            .replacingOccurrences(of: "39.4620", with: "39.4699")
            .replacingOccurrences(of: "-0.4100", with: "-0.3763")
        server.configureNearbyOverviewResponse(.ok(nearby))
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
        // Reset only after terminating the previous app, so its MapKit location provider
        // cannot raise a permission dialog while the next test is launching.
        app.terminate()
        app.resetAuthorizationStatus(for: .location)
        XCUIDevice.shared.location = XCUILocation(location: CLLocation(latitude: 39.4699, longitude: -0.3763))
        var permissionGranted = false
        let permissionMonitor = addUIInterruptionMonitor(withDescription: "Location permission") { alert in
            let allow = alert.buttons["Allow While Using App"]
            guard allow.exists else { return false }
            allow.tap()
            permissionGranted = true
            return true
        }
        defer { removeUIInterruptionMonitor(permissionMonitor) }
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
        let started = ContinuousClock.now
        locate.tap()

        // A prompt raised after the tap may not trigger an interruption monitor until the
        // next interaction. Read SpringBoard directly rather than the app's own alerts.
        if !permissionGranted {
            let allow = XCUIApplication(bundleIdentifier: "com.apple.springboard")
                .alerts.buttons["Allow While Using App"]
            XCTAssertTrue(allow.waitForExistence(timeout: 5), "The system location permission dialog did not appear")
            allow.tap()
        }
        let appeared = known.waitForExistence(timeout: 10)
        if !appeared {
            let requests = XCTAttachment(string: "NEARBY_REQUESTS\n" + server.recordedRequestPaths().joined(separator: "\n"))
            requests.lifetime = .keepAlways
            add(requests)
            let hierarchy = XCTAttachment(string: app.debugDescription)
            hierarchy.name = "nearby-app-hierarchy"
            hierarchy.lifetime = .keepAlways
            add(hierarchy)
            let failure = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
            failure.name = "nearby-failure-\(appearance.lowercased())"
            failure.lifetime = .keepAlways
            add(failure)
        }
        XCTAssertTrue(appeared, "Known chargers waited on the held live response")
        XCTAssertTrue(known.label.localizedCaseInsensitiveContains("Not live"))
        XCTAssertFalse(known.label.localizedCaseInsensitiveContains("Go now"))
        let timing = XCTAttachment(string: "Locate tap to known nearby row: \(ContinuousClock.now - started)")
        timing.lifetime = .keepAlways
        add(timing)

        let liveRequests = server.recordedRequestPaths().filter(Self.isLive)
        XCTAssertFalse(liveRequests.isEmpty)
        XCTAssertEqual(Set(liveRequests).count, liveRequests.count,
                       "The same pending live query restarted without a change in bounds")
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
