import XCTest

/// Hermetic UI coverage for P5.2's trust UX. Every adverse state is produced by the loopback
/// stub, never by an external service, so these tests prove both the copy and the recovery
/// controls while preserving Volta's fail-closed contract.
final class AdverseStatesUITests: XCTestCase {
    private var stubServer: StubServer!

    override func setUpWithError() throws {
        continueAfterFailure = false
        let server = StubServer()
        try server.start()
        stubServer = server
    }

    override func tearDownWithError() throws {
        stubServer?.stop()
        stubServer = nil
    }

    func testOverviewFailureOffersRetryAndSettingsAtDefaultMediumDetent() throws {
        // App startup and argument-domain backend reconciliation can each request the
        // overview. Keep the failure deterministic across both calls instead of letting the
        // stub's default empty 200 response immediately replace the adverse state.
        stubServer.configureOverviewResponses([.serviceUnavailable, .serviceUnavailable, .serviceUnavailable])
        let app = launchedApp(
            style: "Light",
            extraArguments: ["-VoltaUITestLocationState", "denied"]
        )

        let retry = app.buttons["overviewEmptyRetry"]
        XCTAssertTrue(
            retry.waitForExistence(timeout: 10),
            "Overview failure did not expose Retry; requests: \(stubServer.recordedRequestPaths())"
        )
        XCTAssertTrue(retry.isHittable, "Overview Retry is not reachable at the default medium detent")

        let settings = app.buttons["overviewEmptySettings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 3), "Overview failure did not expose Settings")
        XCTAssertTrue(settings.isHittable, "Overview Settings is not reachable at the default medium detent")
        attachScreenshot(named: "adverse-overview-503-light-medium")
    }

    func testOfflineRelaunchShowsSavedAreaMarkedOfflineWithRetry() throws {
        // First launch online: the area overview is cached on this iPhone.
        let overview = StubFixtures.overviewChargersJSON
        stubServer.configureOverviewResponses([.ok(overview), .ok(overview), .ok(overview)])
        var app = launchedApp(style: "Light", extraArguments: ["-VoltaUITestLocationState", "denied"])
        let row = app.buttons["stationRow-uitest-lead-01"]
        XCTAssertTrue(row.waitForExistence(timeout: 15), "Online overview never showed the fixture row")
        // The cache is written in the background after the response is shown.
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 3))
        sleep(2)
        app.terminate()

        // Second launch offline: the service fails, the saved area appears, clearly offline.
        stubServer.configureOverviewResponses([.serviceUnavailable, .serviceUnavailable, .serviceUnavailable])
        app = launchedApp(style: "Light", extraArguments: ["-VoltaUITestLocationState", "denied", "-VoltaUITestKeepOfflineCache"])
        let note = app.staticTexts["offlineSnapshotNote"]
        XCTAssertTrue(note.waitForExistence(timeout: 15), "Offline note missing; requests: \(stubServer.recordedRequestPaths())")
        XCTAssertTrue(note.label.contains("Offline. Showing chargers saved on this iPhone"))
        XCTAssertTrue(app.buttons["offlineSnapshotRetry"].exists)
        // The cached charger is back on the map, marked not live (the list row sits below
        // the half-height sheet, so the map pin and the count are the visible evidence).
        let pin = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "UITest Lead")).firstMatch
        XCTAssertTrue(pin.waitForExistence(timeout: 5), "Cached charger missing offline")
        let pinValue = String(describing: pin.value ?? "")
        XCTAssertTrue(pinValue.contains("Not"), "Cached charger should read as not live: \(pinValue)")
        XCTAssertFalse(pinValue.localizedCaseInsensitiveContains("Go now"))
        XCTAssertEqual(app.staticTexts["Chargers in this view"].value as? String, "1")
        XCTAssertFalse(app.buttons["overviewEmptyRetry"].exists, "The empty failure state should give way to the saved area")
        attachScreenshot(named: "offline-saved-area-light")
    }

    func testDeniedLocationOffersSearchAndSettingsWithoutMovingTheSheet() throws {
        let app = launchedApp(
            style: "Dark",
            extraArguments: ["-VoltaUITestLocationState", "denied"]
        )

        let search = app.buttons["locationRecoverySearch"]
        XCTAssertTrue(search.waitForExistence(timeout: 10), "Denied-location recovery did not expose city search")
        XCTAssertTrue(search.isHittable, "City-search recovery is not reachable at the default medium detent")

        let settings = app.buttons["locationRecoverySettings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 3), "Denied-location recovery did not expose Settings")
        XCTAssertTrue(settings.isHittable, "Location Settings is not reachable at the default medium detent")
        attachScreenshot(named: "adverse-location-denied-dark-medium")

        search.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 3), "Search city did not focus the search field")
    }

    func testFailedSecondSearchClearsPriorResultAndKeepsRetryVisible() throws {
        stubServer.configureGeocodeResponses([
            .ok(StubFixtures.geocodeJSON),
            .serviceUnavailable,
        ])
        let app = launchedApp(style: "Light")
        openSearch(in: app)
        let searchField = app.textFields["Search a city…"]
        XCTAssertTrue(searchField.waitForExistence(timeout: 10))
        searchField.tap()
        searchField.typeText(StubFixtures.geocodeQueryText)

        let priorResult = placeResult(in: app)
        XCTAssertTrue(priorResult.waitForExistence(timeout: 10), "First place result never appeared")
        XCTAssertTrue(priorResult.isHittable, "First place result is below the fold at the default medium detent")

        app.buttons["Close search"].tap()
        openSearch(in: app)
        searchField.tap()
        searchField.typeText("Barcelona")
        XCTAssertTrue(priorResult.waitForNonExistence(timeout: 2), "A result from the first query survived the second query")

        let failure = anyElement(in: app, labelContains: "Place search is unavailable")
        XCTAssertTrue(failure.waitForExistence(timeout: 10), "Second-query failure state never appeared")
        let retry = app.buttons["Retry search"]
        XCTAssertTrue(retry.waitForExistence(timeout: 3))
        XCTAssertTrue(retry.isHittable, "Search retry is not reachable at the default medium detent")
        XCTAssertFalse(priorResult.exists, "Failed second query restored a stale first-query result")
        attachScreenshot(named: "adverse-search-second-query-503-light-medium")
    }

    func testLiveFailureDropsLiveRowsButKeepsFreeLeadFallbackAndRetry() throws {
        stubServer.configureLiveResponses([.serviceUnavailable])
        let app = launchedApp(style: "Dark")
        selectPlace(in: app)

        // Live rows drop on a failed request (fail-closed).
        XCTAssertFalse(app.buttons["stationRow-uitest-live-go"].exists, "Failed live request retained a live station")

        // WS-1 (2026-07-18): the source-backed FREE lead remains as the honest "Not live"
        // fallback — a free-charger app must still show its database when live is down. It is
        // never route-ready and is clearly marked Not live.
        let lead = app.buttons["stationRow-uitest-lead-01"]
        scrollUntilHittable(lead, in: app)
        XCTAssertTrue(lead.waitForExistence(timeout: 5), "Free lead should remain as the Not-live fallback when live fails")
        XCTAssertTrue(lead.label.localizedCaseInsensitiveContains("Not live"), "Fallback lead must be marked Not live")
        XCTAssertFalse(lead.label.localizedCaseInsensitiveContains("Go now"), "A lead must never present as Go now")

        let retry = app.buttons.matching(NSPredicate(format: "label CONTAINS[cd] %@", "Try again")).firstMatch
        XCTAssertTrue(retry.waitForExistence(timeout: 3), "Live failure did not expose retry")
        attachScreenshot(named: "adverse-live-unavailable-dark-medium")
    }

    func testStaleStatusIsNotRouteReadyAndDetailMakesSourcePrimary() throws {
        stubServer.configureLiveResponses([.ok(StubFixtures.staleLiveChargersJSON)])
        let app = launchedApp(style: "Light")
        selectPlace(in: app)

        let row = app.buttons["stationRow-uitest-live-go"]
        XCTAssertTrue(row.waitForExistence(timeout: 5), "Stale station should remain inspectable")
        XCTAssertTrue(row.label.contains("Stale status"))
        XCTAssertFalse(row.label.contains("Go now"), "Stale station must not claim route readiness")
        XCTAssertFalse(row.label.contains("2 free"), "Stale connector counts must not appear current")
        // The complete list retains source-backed leads alongside explicitly stale stations.
        scrollUntilHittable(app.buttons["stationRow-uitest-lead-01"], in: app)
        XCTAssertTrue(app.buttons["stationRow-uitest-lead-01"].waitForExistence(timeout: 5),
                      "Free lead should remain visible (Not live) when live status is stale")
        scrollUntilHittable(row, in: app)
        row.tap()
        let source = app.buttons["stationSourceAction"]
        let maps = app.buttons["stationMapsAction"]
        scrollUntilHittable(source, in: app)
        XCTAssertTrue(source.isHittable)
        XCTAssertTrue(anyElement(in: app, labelContains: "This status is stale").exists)
        XCTAssertTrue(maps.exists)
        XCTAssertLessThanOrEqual(source.frame.minY, maps.frame.minY)
        XCTAssertLessThan(source.frame.minX, maps.frame.minX, "Source must precede directions for stale stations")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        openSettings(in: app)
        XCTAssertTrue(app.descendants(matching: .any)["settingsDataWarning"].waitForExistence(timeout: 3),
                      "Stale-data explanation must remain available in Settings")
        attachScreenshot(named: "adverse-stale-source-first-light")
    }

    func testMixedLiveAndCachedStatusKeepsFreshRowReadyAndMarksCachedRowStale() throws {
        stubServer.configureLiveResponses([.ok(StubFixtures.mixedLiveChargersJSON)])
        let app = launchedApp(style: "Light")
        selectPlace(in: app)

        let fresh = app.buttons["stationRow-uitest-live-go"]
        XCTAssertTrue(fresh.waitForExistence(timeout: 5))
        XCTAssertTrue(fresh.label.localizedCaseInsensitiveContains("Go now"), "Fresh authority was demoted by a stale peer")

        let cached = app.buttons["stationRow-uitest-live-stale"]
        scrollUntilHittable(cached, in: app)
        XCTAssertTrue(cached.exists, "Cached peers remain inspectable in the complete result list")
        XCTAssertTrue(cached.label.contains("Stale status"))
        XCTAssertFalse(cached.label.contains("Go now"), "Cached peer must not claim route readiness")
        XCTAssertFalse(cached.label.contains("2 free"), "Cached availability must not appear current")
        openSettings(in: app)
        XCTAssertTrue(app.descendants(matching: .any)["settingsDataWarning"].waitForExistence(timeout: 3))
        attachScreenshot(named: "adverse-mixed-live-and-cached-light-medium")
    }

    func testAccessibilityXXXLDetailUsesOneFullIdentityAndKeepsRecoveryActionsReachable() throws {
        let app = launchedApp(
            style: "Dark",
            extraArguments: [
                "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL",
            ]
        )
        selectPlace(in: app)

        let row = app.buttons["stationRow-uitest-live-go"]
        revealStationRowFromHeader(row, in: app)
        XCTAssertTrue(row.waitForExistence(timeout: 5), "AXXXL stale fixture row never appeared")
        XCTAssertTrue(row.isHittable, "AXXXL stale fixture row is not reachable")
        row.tap()

        XCTAssertTrue(app.navigationBars["Charger details"].waitForExistence(timeout: 10),
                      "AXXXL detail must use a stable generic navigation title")
        // WS-1 (2026-07-18): a route-ready charger raises the transient route-moment card, whose
        // name Text is a second app-wide match for the identity. Let it auto-dismiss so the count
        // reflects only the detail screen (the point of the assertion — no duplicate identity there).
        let moment = app.buttons["Dismiss route-ready notice"]
        if moment.exists {
            let gone = NSPredicate(format: "exists == false")
            expectation(for: gone, evaluatedWith: moment)
            waitForExpectations(timeout: 8)
        }
        let fullIdentity = exactStaticTexts(StubFixtures.goNowStationName, in: app)
        XCTAssertTrue(fullIdentity.firstMatch.waitForExistence(timeout: 5),
                      "AXXXL detail omitted the full multiline station identity")
        XCTAssertEqual(fullIdentity.count, 1,
                       "AXXXL detail must expose exactly one full station identity")

        let source = app.buttons["stationSourceAction"]
        scrollUntilHittable(source, in: app)
        XCTAssertTrue(source.waitForExistence(timeout: 3), "AXXXL detail omitted the public source action")
        XCTAssertTrue(source.isHittable, "AXXXL source action is not reachable")

        let maps = app.buttons["stationMapsAction"]
        scrollUntilHittable(maps, in: app)
        XCTAssertTrue(maps.waitForExistence(timeout: 3), "AXXXL detail omitted the Maps recovery action")
        XCTAssertTrue(maps.isHittable, "AXXXL Maps action is not reachable")
        XCTAssertTrue(source.isHittable, "AXXXL source action moved out of reach beside Maps")
        attachScreenshot(named: "adverse-live-detail-dark-AXXXL")
    }

    func testDegradedAreaPreservesIndividuallyVerifiedGoNowAndMovesWarningToSettings() throws {
        stubServer.configureLiveResponses([.ok(StubFixtures.degradedLiveChargersJSON)])
        let app = launchedApp(style: "Dark")
        selectPlace(in: app)

        let row = app.buttons["stationRow-uitest-live-go"]
        XCTAssertTrue(row.waitForExistence(timeout: 10), "Degraded fixture row never appeared")
        XCTAssertTrue(row.label.localizedCaseInsensitiveContains("Go now"), "Verified live station lost its station-level decision")
        XCTAssertTrue(row.label.localizedCaseInsensitiveContains("2 free"), "Verified live station lost current availability")
        XCTAssertFalse(row.label.localizedCaseInsensitiveContains("Limited live coverage"), "Aggregate warning leaked into the primary card")

        openSettings(in: app)
        XCTAssertTrue(app.descendants(matching: .any)["settingsDataStatus"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.descendants(matching: .any)["settingsDataWarning"].waitForExistence(timeout: 3),
                      "Detailed coverage warning was not moved into Settings")
        attachScreenshot(named: "adverse-degraded-dark-medium")
    }

    // MARK: - Helpers

    private func launchedApp(
        style: String,
        extraArguments: [String] = []
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += [
            "-voltaBaseURL", stubServer.baseURLString,
            "-AppleLanguages", "(en)",
            "-AppleLocale", "en_US",
            "-VoltaUITestDiscoveryAppearance", style,
        ]
        if !extraArguments.contains("-UIPreferredContentSizeCategoryName") {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryL"]
        }
        app.launchArguments += extraArguments
        app.launch()
        let probe = app.staticTexts["discoveryAppearance"]
        XCTAssertTrue(probe.waitForExistence(timeout: 10), "Effective appearance probe is missing")
        XCTAssertEqual(probe.value as? String, style.lowercased())
        return app
    }

    private func selectPlace(in app: XCUIApplication) {
        openSearch(in: app)
        let searchField = app.textFields["Search a city…"]
        XCTAssertTrue(searchField.waitForExistence(timeout: 10), "Search field never appeared")
        searchField.tap()
        searchField.typeText(StubFixtures.geocodeQueryText)
        let result = placeResult(in: app)
        XCTAssertTrue(result.waitForExistence(timeout: 10), "Stub-backed place result never appeared")
        scrollUntilHittable(result, in: app)
        XCTAssertTrue(result.isHittable, "First place result is not reachable at the default medium detent")
        result.tap()
    }

    private func openSearch(in app: XCUIApplication) {
        let search = app.textFields["discoverySearchField"]
        XCTAssertTrue(search.waitForExistence(timeout: 10), "Persistent search field never appeared")
        search.tap()
    }

    private func openSettings(in app: XCUIApplication) {
        let settings = app.buttons["settingsButton"]
        XCTAssertTrue(settings.waitForExistence(timeout: 10), "Settings button never appeared")
        settings.tap()
    }

    private func placeResult(in app: XCUIApplication) -> XCUIElement {
        app.buttons
            .matching(NSPredicate(format: "label CONTAINS[cd] %@", StubFixtures.geocodeResultName))
            .firstMatch
    }

    private func anyElement(in app: XCUIApplication, labelContains text: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS[cd] %@", text))
            .firstMatch
    }

    private func exactStaticTexts(_ text: String, in app: XCUIApplication) -> XCUIElementQuery {
        app.staticTexts.matching(NSPredicate(format: "label == %@", text))
    }

    private func scrollUntilHittable(
        _ element: XCUIElement,
        in app: XCUIApplication,
        maxSwipes: Int = 8
    ) {
        for _ in 0 ..< maxSwipes where !element.isHittable {
            app.swipeUp()
        }
    }

    /// Target the list, not the window: a downward app swipe can collapse the sheet
    /// and then pan the underlying map away from the fixture's actual coordinates.
    private func revealStationRowFromHeader(
        _ row: XCUIElement,
        in app: XCUIApplication,
        maxSwipes: Int = 8
    ) {
        let list = app.collectionViews.firstMatch
        XCTAssertTrue(list.waitForExistence(timeout: 5))
        for _ in 0 ..< maxSwipes where !row.isHittable {
            list.swipeUp()
        }
    }

    private func assertSourcePrecedesMaps(
        source: XCUIElement,
        maps: XCUIElement,
        in app: XCUIApplication,
        vertical: Bool,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let labels = app.buttons.allElementsBoundByIndex.map(\.label)
        let sourceIndex = labels.firstIndex { $0.localizedCaseInsensitiveContains("MapaREVE") }
        let mapsIndex = labels.firstIndex { $0.localizedCaseInsensitiveContains("Open in Maps") }
        XCTAssertNotNil(sourceIndex, "Source action is missing from the accessibility order", file: file, line: line)
        XCTAssertNotNil(mapsIndex, "Maps action is missing from the accessibility order", file: file, line: line)
        if let sourceIndex, let mapsIndex {
            XCTAssertLessThan(sourceIndex, mapsIndex,
                              "Non-route-ready source action must precede Maps in accessibility order",
                              file: file, line: line)
        }

        if vertical {
            XCTAssertLessThan(source.frame.minY, maps.frame.minY,
                              "AXXXL source action must appear above Maps", file: file, line: line)
        } else {
            XCTAssertLessThan(source.frame.minX, maps.frame.minX,
                              "Source action must appear before Maps", file: file, line: line)
        }
    }

    private func attachScreenshot(named name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
