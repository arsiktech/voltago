import XCTest

/// Hermetic XCUITest smoke coverage for GOAL_CONTRACT.md Phase P3's three core flows: find near
/// me / inspect a charger / act on it (TC-124). See StubServer.swift's header comment for why
/// these use a real in-process HTTP server rather than a `URLProtocol` stub.
///
/// Location strategy (documented per the P3.2 dispatch, and the actual empirical finding): the
/// FIRST implementation tried `xcrun simctl privacy`/`xcrun simctl location` on the named
/// simulator from the host shell, then tapped "Find my location" (real `CLLocationManager`).
/// That was consistently unreliable in THIS xcodebuild setup: Xcode 26 runs `VoltaTests` and
/// `VoltaUITests` concurrently, each on its OWN ephemeral clone of the named simulator ("Clone 1
/// of iPhone 17 Pro - VoltaUITests-Runner", "Clone 2 of iPhone 17 Pro - Volta" in the actual
/// `xcodebuild test` output) -- `simctl location set` talks to the RUNNING device's location
/// daemon, and a clone created fresh by that same `xcodebuild test` invocation never received
/// it, so `CLLocationManager.requestLocation()` never resolved and the flow hung until timeout
/// (confirmed by two consecutive identical failures waiting for live station content after
/// ~33s each, both on `Clone 1`). Per the dispatch's own explicit fallback
/// instruction, this was switched to the SEARCH-A-PLACE flow instead: it drives the exact same
/// AppState.fetchLive()/render path with zero CoreLocation/simctl dependency, so it cannot be
/// affected by which simulator instance a given run happens to clone. Confirmed reliable across
/// two consecutive `xcodebuild test` runs post-switch (see the dispatch report).
final class CoreFlowsUITests: XCTestCase {
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

    private func launchedApp(extraLaunchArguments: [String] = []) -> XCUIApplication {
        let app = XCUIApplication()
        // The existing configurable-backend-URL seam (AppState.swift's
        // `@AppStorage("voltaBaseURL")`) reads this via UserDefaults' argument domain -- no
        // production code change needed to point the app at the hermetic stub.
        app.launchArguments += ["-voltaBaseURL", stubServer.baseURLString]
        app.launchArguments += ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        // Declare every test's text-size baseline explicitly for repeatable screenshots.
        if !extraLaunchArguments.contains("-UIPreferredContentSizeCategoryName") {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryL"]
        }
        if !extraLaunchArguments.contains("-VoltaUITestDiscoveryAppearance") {
            app.launchArguments += ["-VoltaUITestDiscoveryAppearance", "Light"]
        }
        app.launchArguments += extraLaunchArguments
        app.launch()
        let probe = app.staticTexts["discoveryAppearance"]
        XCTAssertTrue(probe.waitForExistence(timeout: 10), "Effective appearance probe is missing")
        let requestedStyle = extraLaunchArguments.firstIndex(of: "-VoltaUITestDiscoveryAppearance")
            .map { extraLaunchArguments[$0 + 1].lowercased() } ?? "light"
        XCTAssertEqual(probe.value as? String, requestedStyle, "Rendered appearance must match the screenshot label")
        return app
    }

    /// RootView's persistent bottom sheet now opens at `.medium`, which keeps search results and
    /// adverse-state recovery controls immediately reachable on a 390-point phone. These core
    /// detail tests still expand it because the provenance sections deliberately extend below
    /// the medium fold, and a blind `app.swipeUp()` can hit the interactive map BEHIND the sheet
    /// (`.presentationBackgroundInteraction(.enabled(upThrough: .medium))`) instead of changing
    /// detents. Dragging the accessible "Sheet Grabber" element
    /// (`.presentationDragIndicator(.visible)`) up to near the top of the screen forces the
    /// `.large` detent, exactly like a user manually expanding the sheet, giving the list its
    /// full-screen height for the rest of the test.
    private func expandSheetToLargeDetent(in app: XCUIApplication) {
        let grabber = app.buttons["Sheet Grabber"]
        guard grabber.waitForExistence(timeout: 10) else { return }
        let start = grabber.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.05))
        start.press(forDuration: 0.1, thenDragTo: end)
    }

    // MARK: - Flow 1: find near me (via search-a-place -- see the class doc comment)

    /// Typing a place and picking the geocode result drives AppState.pick() -> fetchLive() ->
    /// `GET /api/chargers` (bbox, no `overview` param) against the stub -- the same
    /// fetch-to-render path "find near me" exercises, deterministically. The fixture station
    /// card proves that live data reached the result list without relying on a decorative heading.
    func testFindNearMePopulatesLiveStationList() throws {
        let app = launchedApp()

        let goNowRow = try searchAndSelectPlace(in: app)
        XCTAssertTrue(goNowRow.exists, "Fixture station row never appeared in the list")
    }

    func testDiscoveryControlsRemainVisibleAfterMapPan() throws {
        let app = launchedApp()
        let search = app.textFields["discoverySearchField"]
        let settings = app.buttons["settingsButton"]
        XCTAssertTrue(search.waitForExistence(timeout: 10))
        XCTAssertTrue(search.isHittable, "Search must be usable on first launch")
        XCTAssertTrue(app.buttons["Maximum price"].isHittable)
        XCTAssertEqual(settings.label, "Settings")
        let panStart = app.coordinate(withNormalizedOffset: CGVector(dx: 0.78, dy: 0.28))
        let panEnd = app.coordinate(withNormalizedOffset: CGVector(dx: 0.28, dy: 0.28))
        panStart.press(forDuration: 0.1, thenDragTo: panEnd)
        XCTAssertTrue(search.isHittable, "Panning must not hide discovery")
        XCTAssertTrue(app.buttons["Maximum price"].isHittable)
        XCTAssertEqual(settings.label, "Settings")
        attachEvidenceScreenshot(named: "discovery_light_default")
        settings.tap()
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 5))
    }

    func testEmptyFilteredResultsCanRecoverInPlace() throws {
        let app = launchedApp()
        expandSheetToLargeDetent(in: app)
        let power = app.buttons["Minimum charging speed"]
        XCTAssertTrue(power.waitForExistence(timeout: 10))
        power.tap()
        let slider = app.sliders["minimumPowerSlider"]
        XCTAssertTrue(slider.waitForExistence(timeout: 3))
        slider.adjust(toNormalizedSliderPosition: 1)
        app.buttons["Done"].tap()
        XCTAssertTrue(app.staticTexts["No chargers in this view"].waitForExistence(timeout: 5))
        let clear = app.buttons["clearDiscoveryFilters"]
        XCTAssertTrue(clear.isHittable)
        app.textFields["discoverySearchField"].tap()
        app.buttons["Close search"].tap()
        XCTAssertTrue(clear.exists, "Search interactions must preserve the active filter")
        XCTAssertTrue(app.staticTexts["No chargers in this view"].exists)
        clear.tap()
        XCTAssertTrue(app.buttons["stationRow-uitest-lead-01"].waitForExistence(timeout: 5))
        XCTAssertFalse(clear.exists)
    }

    func testDiscoveryDarkAccessibilityLayout() throws {
        let app = launchedApp(extraLaunchArguments: [
            "-VoltaUITestDiscoveryAppearance", "Dark",
            "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL",
        ])
        let search = app.textFields["discoverySearchField"]
        XCTAssertTrue(search.waitForExistence(timeout: 10))
        XCTAssertTrue(search.isHittable)
        let row = app.buttons["stationRow-uitest-lead-01"]
        for _ in 0..<6 where !row.isHittable { app.swipeUp() }
        XCTAssertTrue(row.isHittable, "Large-text cards must be reachable by vertical scrolling")
        XCTAssertTrue(row.label.contains("Parking cost unknown"))
        attachEvidenceScreenshot(named: "discovery_dark_axxxl")
    }

    func testMapFiltersExposeNativeSlidersAndConnectorMultiSelect() throws {
        let app = launchedApp()
        XCTAssertTrue(app.textFields["discoverySearchField"].waitForExistence(timeout: 10))

        let price = app.buttons["Maximum price"]
        XCTAssertTrue(price.waitForExistence(timeout: 3))
        price.tap()
        XCTAssertTrue(app.sliders["maximumPriceSlider"].waitForExistence(timeout: 3), "Maximum price did not open a Slider")
        app.buttons["Done"].tap()

        let connectors = app.buttons["Connector types"]
        XCTAssertTrue(connectors.waitForExistence(timeout: 3))
        connectors.tap()
        let ccs = app.descendants(matching: .any)["CCS2"]
        let chademo = app.descendants(matching: .any)["CHAdeMO"]
        XCTAssertTrue(ccs.waitForExistence(timeout: 3))
        ccs.tap()
        chademo.tap()
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.45)).tap()

        let power = app.buttons["Minimum charging speed"]
        XCTAssertTrue(power.waitForExistence(timeout: 3))
        power.tap()
        XCTAssertTrue(app.sliders["minimumPowerSlider"].waitForExistence(timeout: 3), "Minimum power did not open a Slider")
    }

    // MARK: - Flow 2: inspect a charger

    /// Tapping a station row pushes StationDetailView with its honesty content: the decision
    /// pill, and the Prime-Directive-#1 separated charge/parking claim rows under "Trust &
    /// provenance".
    func testInspectChargerShowsHonestyContent() throws {
        let app = launchedApp()
        let goNowRow = try searchAndSelectPlace(in: app)
        goNowRow.tap()

        let navTitle = app.navigationBars["Charger details"]
        XCTAssertTrue(navTitle.waitForExistence(timeout: 10), "Detail screen never appeared for the tapped station")
        XCTAssertTrue(app.staticTexts[StubFixtures.goNowStationName].exists, "Full station identity is missing")

        let decisionPill = anyElement(in: app, labelContains: "Go now")
        XCTAssertTrue(decisionPill.waitForExistence(timeout: 5), "Decision pill text ('Go now') missing from detail")

        // "Trust & provenance" and its claim rows can sit below the fold -- SwiftUI `List` only
        // realizes rows near the viewport, so scroll them into view rather than assuming a
        // fixed screen size renders everything without scrolling.
        let provenanceHeader = anyElement(in: app, labelContains: "Trust & provenance")
        scrollUntilExists(provenanceHeader, in: app)
        XCTAssertTrue(provenanceHeader.exists, "'Trust & provenance' section never appeared")

        let chargingClaim = anyElement(in: app, labelContains: "Charging claim")
        scrollUntilExists(chargingClaim, in: app)
        XCTAssertTrue(chargingClaim.exists, "'Charging claim' honesty row never appeared")

        let parkingClaim = anyElement(in: app, labelContains: "Parking claim")
        scrollUntilExists(parkingClaim, in: app)
        XCTAssertTrue(parkingClaim.exists, "'Parking claim' honesty row never appeared")

        for metadata in [
            "Attribution: UITest City Open Data",
            "License: CC BY 4.0",
            "Transformation: Coordinates normalized to WGS84.",
            "Source updated: 2026-07-15",
            "Retrieved: 2026-07-15T08:00:00Z"
        ] {
            let element = anyElement(in: app, labelContains: metadata)
            scrollUntilExists(element, in: app)
            XCTAssertTrue(element.exists, "Public-source metadata missing from native detail: \(metadata)")
        }
    }

    // MARK: - Flow 3: act on it

    /// "Open in Maps" and "Source" must exist and be hittable. Smoke-level per the dispatch: we
    /// assert reachability WITHOUT tapping, so the test never leaves the app or opens a real
    /// external URL -- the run stays hermetic and fast.
    func testActOnItControlsExistAndAreHittable() throws {
        let app = launchedApp()
        let goNowRow = try searchAndSelectPlace(in: app)
        goNowRow.tap()

        let openInMaps = app.buttons["Open in Maps"]
        XCTAssertTrue(openInMaps.waitForExistence(timeout: 10), "'Open in Maps' button never appeared")
        XCTAssertTrue(openInMaps.isHittable, "'Open in Maps' exists but is not hittable")

        let source = app.buttons["stationSourceAction"]
        XCTAssertTrue(source.waitForExistence(timeout: 5), "Exact station source action never appeared")
        XCTAssertEqual(source.label, "Open MapaREVE")
        XCTAssertTrue(source.isHittable, "Station source exists but is not hittable")
    }

    // MARK: - Detail-screen evidence capture (P3.6-fix)

    /// P3.6's cold-launch/evidence unit could screenshot the main map+sheet screen via
    /// `xcrun simctl io screenshot` (a host-side command), but could NOT reach
    /// `StationDetailView` for a screenshot: this execution environment has 0
    /// ScreenCaptureKit-shareable displays, so there is no way to tap/HID-navigate a running
    /// simulator from the host shell (`simctl` itself has no tap/touch subcommand). The
    /// `testInspectChargerShowsHonestyContent` flow above already proves the app CAN be
    /// UI-test-driven all the way to `StationDetailView` -- these tests reuse that exact
    /// navigation, then attach a full-screen `XCUIScreen.main.screenshot()` to the run's own
    /// xcresult with `.keepAlways` (XCTest drops attachments from a PASSING test unless kept
    /// explicitly), varying appearance/Dynamic Type via launch arguments the same way P3.6
    /// varied them for the main-screen evidence. Exported afterwards with
    /// `xcrun xcresulttool export attachments` -- the same tool P3.2 used to pull failure-time
    /// AX-hierarchy dumps out of an xcresult bundle.
    func testDetailScreenEvidenceLightDefault() throws {
        try captureDetailScreenEvidence(
            name: "detail_light_dynamictype-default",
            extraLaunchArguments: ["-VoltaUITestDiscoveryAppearance", "Light"]
        )
    }

    func testDetailScreenEvidenceDarkDefault() throws {
        try captureDetailScreenEvidence(
            name: "detail_dark_dynamictype-default",
            extraLaunchArguments: ["-VoltaUITestDiscoveryAppearance", "Dark"]
        )
    }

    func testDetailScreenEvidenceLightAXXXL() throws {
        try captureDetailScreenEvidence(
            name: "detail_light_dynamictype-axxxl",
            extraLaunchArguments: [
                "-VoltaUITestDiscoveryAppearance", "Light",
                "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL",
            ]
        )
    }

    func testDetailScreenEvidenceDarkAXXXL() throws {
        try captureDetailScreenEvidence(
            name: "detail_dark_dynamictype-axxxl",
            extraLaunchArguments: [
                "-VoltaUITestDiscoveryAppearance", "Dark",
                "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL",
            ]
        )
    }

    /// Shared drive for the 4 evidence tests above: launch with the given appearance/Dynamic
    /// Type overrides, run the same "find near me" -> "inspect a charger" flow
    /// `testInspectChargerShowsHonestyContent` already exercises, wait for the detail screen's
    /// title + decision pill (proof real honesty content rendered, not a blank push), then
    /// attach a screenshot. Deliberately does NOT also scroll to "Trust & provenance" first --
    /// at accessibility-XXL Dynamic Type each row is much taller, and this is evidence capture,
    /// not a new correctness assertion (that's already covered by
    /// `testInspectChargerShowsHonestyContent`'s own scroll-and-assert).
    private func captureDetailScreenEvidence(name: String, extraLaunchArguments: [String]) throws {
        let app = launchedApp(extraLaunchArguments: extraLaunchArguments)
        let goNowRow = try searchAndSelectPlace(in: app)
        goNowRow.tap()

        let navTitle = app.navigationBars["Charger details"]
        XCTAssertTrue(navTitle.waitForExistence(timeout: 10), "Detail screen never appeared for the tapped station")
        XCTAssertTrue(app.staticTexts[StubFixtures.goNowStationName].exists, "Full station identity is missing")
        let decisionPill = anyElement(in: app, labelContains: "Go now")
        XCTAssertTrue(decisionPill.waitForExistence(timeout: 5), "Decision pill text ('Go now') missing from detail")

        attachEvidenceScreenshot(named: name)
    }

    // MARK: - Shared helpers

    /// Types "Valencia" into SheetView's search field, waits for the stub-backed geocode result,
    /// taps it (AppState.pick -> fetchLive), then waits for the fixture "Go now" station row to
    /// appear in the resulting live list. This is the shared "find near me" trigger for all 3
    /// flows -- see the class doc comment for why search replaced the locate button.
    @discardableResult
    private func searchAndSelectPlace(in app: XCUIApplication) throws -> XCUIElement {
        let searchField = app.textFields["Search a city…"]
        XCTAssertTrue(searchField.waitForExistence(timeout: 10), "Search field never appeared")
        searchField.tap()
        searchField.typeText(StubFixtures.geocodeQueryText)

        let placeResult = app.buttons
            .matching(NSPredicate(format: "label CONTAINS[cd] %@", StubFixtures.geocodeResultName))
            .firstMatch
        XCTAssertTrue(placeResult.waitForExistence(timeout: 10), "Stub-backed place result never appeared")
        placeResult.tap()

        expandSheetToLargeDetent(in: app)
        let goNowRow = goNowStationRow(in: app)
        for _ in 0..<8 where !goNowRow.isHittable {
            app.collectionViews.firstMatch.swipeUp()
        }
        XCTAssertTrue(goNowRow.waitForExistence(timeout: 15), "Fixture station row never appeared in the list")
        return goNowRow
    }

    /// SheetView's station row is a plain `Button { } label: { StationRow(...) } ` with no
    /// explicit `.accessibilityLabel` -- SwiftUI's default Button synthesis combines its label's
    /// text content into one element, so a naive `label CONTAINS <station name>` query is
    /// AMBIGUOUS: the same station also renders as a map-pin `Annotation` (RootView.swift's
    /// `PricePill`) with `.accessibilityLabel(st.name)` set to the EXACT station name, and
    /// `.firstMatch` resolution between the two isn't guaranteed stable run to run (confirmed by
    /// two consecutive real `xcodebuild test` runs -- see the dispatch report). Queries by the
    /// row-only `accessibilityIdentifier("stationRow-<id>")` added to SheetView.swift instead
    /// (a UI-test hook only; identifiers are never read by VoiceOver, so this doesn't touch any
    /// accessibility contract).
    private func goNowStationRow(in app: XCUIApplication) -> XCUIElement {
        app.buttons["stationRow-uitest-live-go"]
    }

    /// Attaches a full-screen screenshot to this test's xcresult, kept regardless of pass/fail
    /// so it survives long enough to export (see the "Detail-screen evidence capture" section
    /// above for why this exists and how it's exported).
    private func attachEvidenceScreenshot(named name: String) {
        let screenshot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// Broad, element-type-agnostic lookup for composed/`.accessibilityElement(children:
    /// .combine)` text content whose exact XCUIElementType isn't worth pinning down (e.g. List
    /// section headers, combined claim rows).
    private func anyElement(in app: XCUIApplication, labelContains text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS[cd] %@", text)).firstMatch
    }

    /// Bounded swipe-up loop. Stops as soon as the element exists, or after a fixed number of
    /// swipes, so a genuinely-missing element fails the test instead of hanging it.
    private func scrollUntilExists(_ element: XCUIElement, in app: XCUIApplication, maxSwipes: Int = 8) {
        var attempts = 0
        while !element.exists, attempts < maxSwipes {
            app.swipeUp()
            attempts += 1
        }
    }
}
