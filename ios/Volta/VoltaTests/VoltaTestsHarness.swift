import XCTest
@testable import Volta

/// Step 1 of standing up the VoltaTests target (P3.1): a trivial test that must build and
/// pass BEFORE any real test is added, proving the target/scheme/TEST_HOST wiring in
/// project.pbxproj actually works end to end (build the app, build the test bundle, launch it
/// against the app as its host, run XCTest, report results) rather than assuming pbxproj
/// surgery was correct. `@testable import Volta` here is deliberate, not incidental: if the
/// test target's module search / TEST_HOST / BUNDLE_LOADER settings were wrong, this import
/// alone would fail to link before `testHarness` ever ran.
final class VoltaTestsHarness: XCTestCase {
    func testHarness() {
        XCTAssertTrue(true)
    }
}
