import XCTest

/// STEP 1 (P3.2): a trivial smoke test that verifies the VoltaUITests target itself is wired
/// correctly (pbxproj target + shared-scheme Test action) BEFORE any real flow content is
/// added -- mirrors VoltaTests' own harness-first verification order (P3.1,
/// VoltaTestsHarness.swift). XCUITest UI-testing bundles launch the app under test as a
/// SEPARATE OS process (unlike VoltaTests' unit-test bundle, which is injected into the app
/// process via TEST_HOST/BUNDLE_LOADER) -- this is the first proof that pattern actually
/// builds and runs on this project's pbxproj before CoreFlowsUITests.swift adds any real
/// scenario or the hermetic stub server.
final class LaunchSmokeTests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testAppLaunches() throws {
        let app = XCUIApplication()
        app.launch()
        XCTAssertEqual(app.state, .runningForeground, "App did not reach the foreground after launch")
    }
}
