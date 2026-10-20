import Foundation
import XCTest

/// Synthetic source claims travel through the real API decoder, station card and detail UI.
/// Fixed dates distinguish an old community observation from a newer download.
final class PriceEvidenceUITests: XCTestCase {
    private var stubServer: StubServer!

    private static let stationID = "uitest-price-evidence"
    private static let stationName = "UITest Price Evidence Charger"
    private static let fetchedDate = "2026-10-20"
    private static let checkedText = "Checked: 2026-10-20"
    private static let evidence: [(name: String, label: String, text: String)] = [
        (
            "repsol",
            "Repsol published tariff · station may differ",
            "from €0.39/kWh; idle fee €0.10/min after 30 min grace."
        ),
        (
            "electromaps",
            "Electromaps community price · 2025-08-16",
            "from €0.25/kWh; €1 session fee; idle fee €0.08/min after 20 min grace."
        ),
        (
            "chargemap",
            "Price with Chargemap Pass",
            "€0.59 per started kWh; idle fee €0.15/min after 10 min grace."
        ),
    ]

    override func setUpWithError() throws {
        continueAfterFailure = false
        let server = StubServer()
        try server.start()
        stubServer = server
        // A single step repeats across startup reconciliation and any subsequent refresh.
        server.configureOverviewResponses([.ok(Self.overviewJSON)])
        server.configureLiveResponses([.ok(Self.overviewJSON)])
    }

    override func tearDownWithError() throws {
        stubServer?.stop()
        stubServer = nil
    }

    func testPriceEvidenceLightDefault() throws {
        exercisePriceEvidence(style: "Light", size: "UICTContentSizeCategoryL", name: "light_default")
    }

    func testPriceEvidenceDarkAXXXL() throws {
        exercisePriceEvidence(
            style: "Dark",
            size: "UICTContentSizeCategoryAccessibilityXXXL",
            name: "dark_axxxl"
        )
    }

    private func exercisePriceEvidence(style: String, size: String, name: String) {
        let app = XCUIApplication()
        app.launchArguments = [
            "-voltaBaseURL", stubServer.baseURLString,
            "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
            "-VoltaUITestDiscoveryAppearance", style,
            "-UIPreferredContentSizeCategoryName", size,
            // Keep a previously granted Simulator location from changing the overview.
            "-VoltaUITestLocationState", "denied",
        ]
        app.launch()
        defer { app.terminate() }

        let grabber = app.buttons["Sheet Grabber"]
        XCTAssertTrue(grabber.waitForExistence(timeout: 10), "Discovery sheet never appeared")
        grabber.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
            .press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.05)))

        let cards = scrollingList(in: app)
        let appearance = reveal(app.staticTexts.matching(identifier: "discoveryAppearance"), in: cards, app: app)
        XCTAssertEqual(appearance.value as? String, style.lowercased(), "Effective appearance must match the attachment name")
        let row = reveal(app.buttons.matching(identifier: "stationRow-\(Self.stationID)"), in: cards, app: app)
        XCTAssertTrue(row.label.contains(Self.stationName))
        XCTAssertTrue(row.label.contains("—"), "The primary charging price must stay unknown")
        XCTAssertTrue(row.label.contains("Not live"), "Source prices must not grant live authority")
        XCTAssertTrue(row.label.contains("Price lead"))
        XCTAssertFalse(row.label.contains("Go now"))
        for item in Self.evidence {
            XCTAssertTrue(row.label.contains(item.label), "Card lost the source label: \(item.label)")
            XCTAssertTrue(row.label.contains(item.text), "Card changed the source terms: \(item.text)")
        }
        XCTAssertEqual(row.label.components(separatedBy: Self.checkedText).count - 1, 2,
                       "Only the published tariff and Pass price show the download date as Checked")
        XCTAssertFalse(row.label.contains("Electromaps community price · \(Self.fetchedDate)"))
        XCTAssertFalse(row.label.contains("Checked: 2020-01-01"))
        XCTAssertFalse(row.label.contains("Checked: 2021-02-03"))
        captureCardViewports(row, in: cards, app: app, name: name)

        // Use the row identifier; the same station can also appear as a map annotation.
        XCTAssertTrue(row.isHittable, "Station card must remain tappable after scrolling")
        row.tap()
        XCTAssertTrue(app.navigationBars["Charger details"].waitForExistence(timeout: 10))
        let detail = scrollingList(in: app)
        let primaryPrice = reveal(
            app.descendants(matching: .any).matching(NSPredicate(
                format: "label BEGINSWITH[cd] %@ AND label CONTAINS %@", "Charging", "—"
            )),
            in: detail, app: app
        )
        XCTAssertFalse(primaryPrice.label.contains("€"), "An annotation must not become the primary charging price")
        attachScreenshot(named: "price_evidence_detail_\(name)_primary_unknown")

        // Combined accessibility labels include the source terms and date. Match substrings,
        // not an exact staticText label, and require the terms so Source links cannot match.
        for item in Self.evidence {
            let element = reveal(elements(containing: [item.label, item.text], in: app), in: detail, app: app)
            XCTAssertTrue(element.label.contains(item.label))
            XCTAssertTrue(element.label.contains(item.text), "Detail changed qualifiers, billing units, fees or grace terms")
            if item.name == "electromaps" {
                XCTAssertFalse(element.label.contains(Self.fetchedDate), "Download time must not replace the community observation date")
                XCTAssertFalse(element.label.contains("Checked:"), "A community observation is not a freshly checked tariff")
            } else {
                XCTAssertTrue(element.label.contains(Self.checkedText), "Checked must use fetched_at, not source_updated_at")
                XCTAssertFalse(element.label.contains("Checked: 2020-01-01"))
                XCTAssertFalse(element.label.contains("Checked: 2021-02-03"))
            }
            attachScreenshot(named: "price_evidence_detail_\(name)_\(item.name)")
        }
        _ = reveal(elements(containing: ["Not live"], in: app), in: detail, app: app)
        XCTAssertFalse(elements(containing: ["Go now"], in: app).firstMatch.exists)
        attachScreenshot(named: "price_evidence_detail_\(name)_not_live")
    }

    private func elements(containing fragments: [String], in app: XCUIApplication) -> XCUIElementQuery {
        app.descendants(matching: .any).matching(NSCompoundPredicate(andPredicateWithSubpredicates:
            fragments.map { NSPredicate(format: "label CONTAINS %@", $0) }
        ))
    }

    private func scrollingList(in app: XCUIApplication) -> XCUIElement {
        // SwiftUI List uses a collection view on current iOS, and a table on older runtimes.
        let list = app.collectionViews.firstMatch
        if list.waitForExistence(timeout: 5) { return list }
        let table = app.tables.firstMatch
        if table.exists { return table }
        let scrollView = app.scrollViews.firstMatch
        XCTAssertTrue(scrollView.exists, "Native list scroll container is missing")
        return scrollView
    }

    /// Choose the smallest matching label so a containing List cell cannot stand in for its
    /// evidence block. Re-query after every drag because List realizes content as it scrolls.
    @discardableResult
    private func reveal(_ query: XCUIElementQuery, in list: XCUIElement, app: XCUIApplication) -> XCUIElement {
        for _ in 0..<24 {
            let viewport = visibleViewport(of: list, in: app)
            if let element = query.allElementsBoundByIndex.min(by: { $0.label.count < $1.label.count }) {
                let frame = element.frame
                if element.isHittable && !frame.isEmpty {
                    if viewport.contains(frame) { return element }
                    // A whole card can exceed the viewport; its top must first be visible.
                    if frame.height > viewport.height,
                       frame.minY >= viewport.minY, frame.minY < viewport.maxY { return element }
                }
                let desiredTop = frame.height > viewport.height
                    ? viewport.minY + 8 : viewport.midY - frame.height / 2
                dragList(list, in: app, distance: frame.minY - desiredTop)
            } else {
                dragList(list, in: app, distance: viewport.height * 0.5)
            }
        }
        XCTFail("Could not reveal native content matching \(query)")
        return query.firstMatch
    }

    /// Card buttons can combine all three claims into one accessibility element. Capture
    /// overlapping real viewports through the entire card, including at AX XXXL, rather than
    /// treating the button's existence as proof that its offscreen terms are visible.
    private func captureCardViewports(_ row: XCUIElement, in list: XCUIElement, app: XCUIApplication, name: String) {
        for index in 0..<24 {
            attachScreenshot(named: "price_evidence_card_\(name)_\(index + 1)")
            let viewport = visibleViewport(of: list, in: app)
            if row.frame.maxY <= viewport.maxY { return }
            dragList(list, in: app, distance: min(viewport.height * 0.5, row.frame.maxY - viewport.maxY))
        }
        XCTFail("Could not scroll through the complete station card")
    }

    private func visibleViewport(of list: XCUIElement, in app: XCUIApplication) -> CGRect {
        list.frame.intersection(app.frame).insetBy(dx: 8, dy: 12)
    }

    private func dragList(_ list: XCUIElement, in app: XCUIApplication, distance: CGFloat) {
        let viewport = visibleViewport(of: list, in: app)
        let limit = viewport.height * 0.5
        // Cross the native pan threshold even when the last few points are below the fold.
        let magnitude = min(limit, max(24, abs(distance)))
        let delta = distance < 0 ? -magnitude : magnitude
        let origin = app.coordinate(withNormalizedOffset: .zero)
        let x = viewport.midX - app.frame.minX
        let y = viewport.midY - app.frame.minY
        let start = origin.withOffset(CGVector(dx: x, dy: y + delta / 2))
        let end = origin.withOffset(CGVector(dx: x, dy: y - delta / 2))
        start.press(forDuration: 0.1, thenDragTo: end)
    }

    private func attachScreenshot(named name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// Deliberately synthetic. No source page or navigation action is opened by these tests.
    /// Numeric annotations are present while the station's primary price stays null.
    private static var overviewJSON: String {
        """
        {
          "stations": [],
          "leads": [{
            "id": "\(stationID)",
            "name": "\(stationName)",
            "city": "Valencia", "address": "UITest Example Street",
            "lat": 39.4699, "lon": -0.3763,
            "lead": true, "status": "UNKNOWN",
            "price_min": null, "is_free": false,
            "reliability": "official_list",
            "decision": {"level": "lead", "label": "Price lead"},
            "verification": {
              "level": "directory", "label": "Directory listing",
              "location_verified": true, "price_verified": false, "live_verified": false, "route_checked": false
            },
            "source_url": "https://example.com/uitest/price-evidence",
            "price_evidence": [
              {
                "kind": "repsol_network_tariff", "source": "Repsol",
                "label": "\(evidence[0].label)", "text": "\(evidence[0].text)",
                "source_url": "https://example.com/uitest/repsol-tariff",
                "source_updated_at": "2020-01-01T00:00:00Z",
                "fetched_at": "2026-10-20T10:20:00Z",
                "price_per_kwh": 0.39, "currency": "EUR"
              },
              {
                "kind": "electromaps_community", "source": "Electromaps",
                "label": "\(evidence[1].label)", "text": "\(evidence[1].text)",
                "source_url": "https://example.com/uitest/electromaps-community",
                "source_updated_at": "2025-08-16T23:30:00-05:00",
                "fetched_at": "2026-10-20T10:20:00Z",
                "price_per_kwh": 0.25, "currency": "EUR"
              },
              {
                "kind": "chargemap_pass", "source": "Chargemap",
                "label": "\(evidence[2].label)", "text": "\(evidence[2].text)",
                "source_url": "https://example.com/uitest/chargemap-pass",
                "source_updated_at": "2021-02-03T00:00:00Z",
                "fetched_at": "2026-10-20T10:20:00Z",
                "price_per_kwh": 0.59, "currency": "EUR"
              }
            ]
          }],
          "meta": {"coverage": {"mode": "standard"}}
        }
        """
    }
}
