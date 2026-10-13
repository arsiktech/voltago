import CryptoKit
import Foundation
import XCTest

/// Synthetic saved-reference journeys against the existing loopback-only UI stub.
/// Saves and removes only a unique fixture entry; never resets other favorites.
final class ChargerReferenceUITests: XCTestCase {
    @MainActor
    func testSavedReferenceMigrationLight() throws {
        try journey(appearance: "Light", size: "UICTContentSizeCategoryL")
    }

    @MainActor
    func testSavedReferenceMigrationDarkAccessibility() throws {
        try journey(appearance: "Dark", size: "UICTContentSizeCategoryAccessibilityXXXL")
    }

    @MainActor
    private func journey(appearance: String, size: String) throws {
        continueAfterFailure = false
        let token = UUID().uuidString
        let originalID = "uitest-reference-original-\(token)"
        let canonicalID = "uitest-reference-canonical-\(token)"
        let originalName = "UITest saved charger \(token.prefix(6))"
        let currentName = "UITest current charger \(token.prefix(6))"
        let server = StubServer()
        try server.start()
        defer { server.stop() }
        let app = XCUIApplication()
        app.launchArguments = [
            "-voltaBaseURL", server.baseURLString,
            "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
            "-VoltaUITestDiscoveryAppearance", appearance,
            "-UIPreferredContentSizeCategoryName", size,
        ]
        defer { app.terminate() }

        let initial = try payload(id: originalID, name: originalName, revision: "a", referenceID: nil, state: nil)
        configure(server, payload: initial)
        app.launch()
        let originalRow = app.buttons["stationRow-\(originalID)"]
        XCTAssertTrue(originalRow.waitForExistence(timeout: 20))
        originalRow.tap()
        let save = app.buttons["saveChargerButton"]
        try reveal(save, in: app)
        XCTAssertEqual(save.label, "Save charger")
        save.tap()
        XCTAssertTrue(app.buttons["saveChargerButton"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons["saveChargerButton"].label, "Saved on this iPhone")
        app.terminate()

        let aliased = try payload(id: canonicalID, name: currentName, revision: "b", referenceID: originalID, state: "alias")
        configure(server, payload: aliased)
        app.launch()
        try showSaved(in: app)
        let currentRow = app.buttons["stationRow-\(canonicalID)"]
        try reveal(currentRow, in: app)
        XCTAssertFalse(app.buttons["stationRow-\(originalID)"].exists)
        shot(app, "reference-alias-\(appearance)-\(size)")
        currentRow.tap()
        try reveal(app.buttons["saveChargerButton"], in: app)
        XCTAssertEqual(app.buttons["saveChargerButton"].label, "Saved on this iPhone")
        app.terminate()

        let withheld = try payload(id: nil, name: nil, revision: "c", referenceID: originalID, state: "withheld")
        configure(server, payload: withheld)
        app.launch()
        try showSaved(in: app)
        shot(app, "reference-withheld-before-controls-\(appearance)")
        let remove = app.buttons["removeSavedReference-\(originalID)"]
        try reveal(remove, in: app)
        XCTAssertTrue(app.staticTexts[originalName].exists)
        XCTAssertTrue(app.staticTexts["Details for this saved charger are unavailable. Your saved entry is still here."].exists)
        XCTAssertFalse(app.buttons["stationRow-\(canonicalID)"].exists)
        XCTAssertFalse(app.buttons["Open in Maps"].exists)
        shot(app, "reference-withheld-\(appearance)-\(size)")

        let unresolved = try payload(id: nil, name: nil, revision: "d", referenceID: originalID, state: "unresolved", cached: true)
        configure(server, payload: unresolved)
        app.buttons["retrySavedReference-\(originalID)"].tap()
        XCTAssertTrue(app.staticTexts["This saved charger needs verification. No replacement has been selected."].waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["Cached reference · Not live"].exists)
        XCTAssertFalse(app.buttons["Open in Maps"].exists)
        try reveal(remove, in: app)
        shot(app, "reference-unresolved-cached-\(appearance)-\(size)")
        remove.tap()
        XCTAssertTrue(remove.waitForNonExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts[originalName].exists)

        let key = SHA256.hash(data: Data(("volta-reference-v1\0" + originalID).utf8))
            .map { String(format: "%02x", $0) }.joined()
        XCTAssertFalse(server.recordedRequestPaths().contains { $0.contains(originalID) || $0.contains(key) })
    }

    private func configure(_ server: StubServer, payload: String) {
        server.configureOverviewResponses([.ok(payload)])
        server.configureLiveResponses([.ok(payload)])
    }

    private func payload(id: String?, name: String?, revision: String, referenceID: String?, state: String?, cached: Bool = false) throws -> String {
        var body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(StubFixtures.overviewChargersJSON.utf8)) as? [String: Any])
        let rev = String(repeating: revision, count: 64)
        let baseRows = try XCTUnwrap(body["leads"] as? [[String: Any]])
        var rows: [[String: Any]] = []
        if let id, let name, var row = baseRows.first {
            row["id"] = id
            row["name"] = name
            row["reference_revision"] = rev
            rows = [row]
        }
        var entries: [[String: Any]] = []
        if let referenceID, let state {
            let key = SHA256.hash(data: Data(("volta-reference-v1\0" + referenceID).utf8))
                .map { String(format: "%02x", $0) }.joined()
            var entry: [String: Any] = ["key": key, "state": state]
            if state == "alias", let id { entry["canonical_id"] = id }
            entries = [entry]
        }
        body["leads"] = rows
        body["references"] = ["version": 1, "revision": rev, "entries": entries]
        body["cache"] = ["hit": cached, "stale": cached, "overview": true]
        return String(decoding: try JSONSerialization.data(withJSONObject: body), as: UTF8.self)
    }

    @MainActor
    private func showSaved(in app: XCUIApplication) throws {
        let toggle = app.switches["savedOnlyToggle"]
        try reveal(toggle, in: app)
        if (toggle.value as? String) != "1" {
            let control = toggle.switches.firstMatch
            if control.exists { control.tap() } else { toggle.tap() }
        }
        let enabled = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", "1"), object: toggle)
        XCTAssertEqual(XCTWaiter.wait(for: [enabled], timeout: 5), .completed)
    }

    @MainActor
    private func reveal(_ element: XCUIElement, in app: XCUIApplication) throws {
        for _ in 0..<8 {
            if element.waitForExistence(timeout: 1), element.isHittable { return }
            if app.collectionViews.firstMatch.exists { app.collectionViews.firstMatch.swipeUp() }
            else if app.scrollViews.firstMatch.exists { app.scrollViews.firstMatch.swipeUp() }
            else { app.swipeUp() }
        }
        XCTAssertTrue(element.exists && element.isHittable)
    }

    @MainActor
    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
