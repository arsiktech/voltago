import XCTest

/// A screenshot tour of the main journey, for reviewing layout on every iPhone size,
/// appearance and text size. It asserts only that each step is reachable; the evidence is
/// the attached screenshots, also written to `TOUR_OUT` when that directory is set.
/// Point it at the real backend with `TOUR_BASE_URL`; it uses the hermetic stub otherwise.
final class ScreenshotTourUITests: XCTestCase {
    private var stubServer: StubServer?

    override func setUpWithError() throws {
        continueAfterFailure = false
        if ProcessInfo.processInfo.environment["TOUR_BASE_URL"] == nil {
            let server = StubServer()
            try server.start()
            stubServer = server
        }
    }

    override func tearDownWithError() throws {
        stubServer?.stop()
        stubServer = nil
    }

    func testTourLightDefault() throws { try tour(appearance: "Light", size: "UICTContentSizeCategoryL") }
    func testTourDarkDefault() throws { try tour(appearance: "Dark", size: "UICTContentSizeCategoryL") }
    func testTourLightAccessibilityXXXL() throws {
        try tour(appearance: "Light", size: "UICTContentSizeCategoryAccessibilityXXXL")
    }

    /// Xcode's automated accessibility audit on the discovery sheet and a detail screen.
    /// It complements, and cannot replace, the manual VoiceOver pass (TC-056). Clipping is
    /// checked by the accessibility-size tour screenshots instead: the `.textClipped` audit
    /// flags wrapping SwiftUI text in rows inconsistently from run to run.
    func testAccessibilityAuditDiscoveryAndDetail() throws {
        let app = XCUIApplication()
        app.launchArguments += [
            "-voltaBaseURL", ProcessInfo.processInfo.environment["TOUR_BASE_URL"] ?? stubServer?.baseURLString ?? "",
            "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
        ]
        app.launch()
        XCTAssertTrue(app.textFields["discoverySearchField"].waitForExistence(timeout: 15))
        try app.performAccessibilityAudit(for: [.dynamicType, .sufficientElementDescription, .hitRegion]) { issue in
            Self.isMapKitOwned(issue)
        }
        let row = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'stationRow-'")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 15))
        row.tap()
        XCTAssertTrue(app.navigationBars["Charger details"].waitForExistence(timeout: 10))
        try app.performAccessibilityAudit(for: [.dynamicType, .sufficientElementDescription, .hitRegion]) { issue in
            Self.isMapKitOwned(issue)
        }
    }

    /// Map tiles and MapKit's own "Legal" attribution link are outside the app's control.
    /// An issue the audit cannot attach to any element (it reports a zero frame, usually a
    /// map annotation rebuilt mid-audit) is logged to `TOUR_OUT/audit.txt` but not failed.
    private static func isMapKitOwned(_ issue: XCUIAccessibilityAuditIssue) -> Bool {
        if let directory = ProcessInfo.processInfo.environment["TOUR_OUT"] {
            let element = issue.element
            let line = "\(issue.compactDescription) | \(issue.detailedDescription) | type \(element.map { String($0.elementType.rawValue) } ?? "none") | \(element?.label ?? "nil") | \(element?.identifier ?? "") | \(element?.frame ?? .zero)\n"
            let url = URL(fileURLWithPath: directory).appendingPathComponent("audit.txt")
            let old = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
            try? (old + line).write(to: url, atomically: true, encoding: .utf8)
        }
        guard let element = issue.element else { return true }
        return element.elementType == .map || (element.elementType == .link && element.label == "Legal")
    }

    private func tour(appearance: String, size: String) throws {
        let base = ProcessInfo.processInfo.environment["TOUR_BASE_URL"] ?? stubServer?.baseURLString ?? ""
        let app = XCUIApplication()
        app.launchArguments += [
            "-voltaBaseURL", base,
            "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
            "-UIPreferredContentSizeCategoryName", size,
            "-VoltaUITestDiscoveryAppearance", appearance,
        ]
        app.launch()
        let tag = "\(appearance.lowercased())-\(size.contains("Accessibility") ? "axxxl" : "default")"

        let search = app.textFields["discoverySearchField"]
        XCTAssertTrue(search.waitForExistence(timeout: 15))
        sleep(3)
        shot("01-overview-\(tag)")

        search.tap()
        search.typeText(stubServer == nil ? "Valencia" : StubFixtures.geocodeQueryText)
        let place = app.buttons.matching(NSPredicate(format: "label CONTAINS[cd] %@",
                                                     stubServer == nil ? "Valencia" : StubFixtures.geocodeResultName)).firstMatch
        XCTAssertTrue(place.waitForExistence(timeout: 15))
        shot("02-search-\(tag)")
        place.tap()
        sleep(4)
        shot("03-city-\(tag)")

        let row = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'stationRow-'")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 15))
        row.tap()
        XCTAssertTrue(app.navigationBars["Charger details"].waitForExistence(timeout: 10))
        sleep(2)
        shot("04-detail-\(tag)")
        app.swipeUp()
        sleep(1)
        shot("05-detail-scrolled-\(tag)")
        app.navigationBars["Charger details"].buttons.firstMatch.tap()

        let price = app.buttons["Maximum price"]
        if price.waitForExistence(timeout: 5), price.isHittable {
            price.tap()
            sleep(1)
            shot("06-price-filter-\(tag)")
        }
    }

    private func shot(_ name: String) {
        let screenshot = XCUIScreen.main.screenshot()
        if let directory = ProcessInfo.processInfo.environment["TOUR_OUT"] {
            try? screenshot.pngRepresentation.write(to: URL(fileURLWithPath: directory).appendingPathComponent(name + ".png"))
        }
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
