import XCTest

/// The pinned discovery header must stay usable while its actions and text size change.
final class DiscoveryHeaderUITests: XCTestCase {
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

    private func launch(style: String, category: String) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-voltaBaseURL", server.baseURLString,
            // Keep permission prompts and real GPS callbacks out of header geometry checks.
            "-VoltaUITestLocationState", "denied",
            "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
            "-VoltaUITestDiscoveryAppearance", style,
            "-UIPreferredContentSizeCategoryName", category,
        ]
        app.launch()
        XCTAssertTrue(app.textFields["discoverySearchField"].waitForExistence(timeout: 10))
        return app
    }

    func testPromptHeaderLightAccessibilityXXXL() {
        checkPromptHeader(style: "Light", category: "UICTContentSizeCategoryAccessibilityXXXL")
    }

    func testPromptHeaderDarkAccessibilityXXXL() {
        checkPromptHeader(style: "Dark", category: "UICTContentSizeCategoryAccessibilityXXXL")
    }

    func testPromptHeaderLightDefault() {
        checkPromptHeader(style: "Light", category: "UICTContentSizeCategoryL")
    }

    func testPromptHeaderDarkDefault() {
        checkPromptHeader(style: "Dark", category: "UICTContentSizeCategoryL")
    }

    private func checkPromptHeader(style: String, category: String) {
        let app = launch(style: style, category: category)
        let search = app.textFields["discoverySearchField"]
        let prompt = app.buttons["copyAIPromptButton"]
        // Speed is intentionally offscreen in the horizontal filter scroller at
        // large text sizes. The first visible chip bounds the whole filter row.
        let filter = app.buttons["freeFilterChip"]
        XCTAssertTrue(prompt.waitForExistence(timeout: 5))
        attachScreenshot(named: "prompt-header-\(style.lowercased())-\(category)-before-copy")
        XCTAssertTrue(prompt.isHittable)
        XCTAssertTrue(search.isHittable)
        XCTAssertTrue(filter.isHittable)
        var frames = ["search": search.frame.debugDescription,
                      "prompt": prompt.frame.debugDescription,
                      "firstFilter": filter.frame.debugDescription]
        for title in ["Copy AI prompt", "Paste into your AI chat"] {
            let text = app.staticTexts[title]
            XCTAssertTrue(text.exists, "The prompt must retain both visible lines")
            frames[title] = text.frame.debugDescription
            XCTAssertLessThanOrEqual(search.frame.maxY, text.frame.minY, "Prompt text overlaps search")
            XCTAssertLessThanOrEqual(text.frame.maxY, filter.frame.minY, "Prompt text overlaps filters")
            XCTAssertTrue(prompt.frame.contains(text.frame), "Prompt text overflows its button")
        }
        let geometry = XCTAttachment(string: frames.description)
        geometry.name = "header-frames-\(style.lowercased())-\(category)"
        geometry.lifetime = .keepAlways
        add(geometry)
        let before = prompt.frame
        prompt.tap()
        XCTAssertTrue(app.buttons["copyAIPromptButton"].label.contains("Prompt copied"))
        XCTAssertTrue(app.staticTexts["Prompt copied"].waitForExistence(timeout: 3))
        XCTAssertEqual(app.buttons["copyAIPromptButton"].frame, before, "Copy feedback must not move the header")
        attachScreenshot(named: "prompt-header-\(style.lowercased())-\(category)-copied")
    }

    private func attachScreenshot(named name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
