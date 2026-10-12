import XCTest

final class PaywallUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testDefaultOffLaunchesNormalAppWithoutPaywall() throws {
        let server = StubServer()
        try server.start()
        defer { server.stop() }

        let app = launchDefaultOffApp(baseURL: server.baseURLString)
        XCTAssertTrue(app.textFields["discoverySearchField"].waitForExistence(timeout: 10))
        XCTAssertFalse(anyElement(identifier: "paywallProofChain", in: app).exists)
        XCTAssertFalse(app.buttons["paywallProduct-volta.plus.monthly"].exists)
    }

    func testRealCatalogChoiceAndLocalLegalDocumentsAreAccessible() throws {
        let app = launchPaywall(appearance: "light")
        XCTAssertTrue(app.otherElements["paywallProofChain"].waitForExistence(timeout: 15))
        assertAppearance("light", in: app)

        let monthly = app.buttons["paywallProduct-volta.plus.monthly"]
        let yearly = app.buttons["paywallProduct-volta.plus.yearly"]
        XCTAssertTrue(monthly.waitForExistence(timeout: 15), "real monthly StoreKit product did not render")
        XCTAssertTrue(yearly.waitForExistence(timeout: 5), "real yearly StoreKit product did not render")
        let monthlyValue = monthly.value as? String ?? ""
        let yearlyValue = yearly.value as? String ?? ""
        XCTAssertFalse(monthlyValue.isEmpty)
        XCTAssertFalse(yearlyValue.isEmpty)
        XCTAssertTrue(monthlyValue.contains("Selected"))
        XCTAssertTrue(app.buttons["paywallPurchaseButton"].exists)
        attachScreenshot(named: "paywall-default-light-monthly")

        yearly.tap()
        let selectedYearly = app.buttons["paywallProduct-volta.plus.yearly"]
        let deselectedMonthly = app.buttons["paywallProduct-volta.plus.monthly"]
        let selectionUpdated = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value CONTAINS[c] %@", "Selected"),
            object: selectedYearly
        )
        XCTAssertEqual(XCTWaiter.wait(for: [selectionUpdated], timeout: 3), .completed)
        XCTAssertTrue((selectedYearly.value as? String ?? "").contains("Selected"))
        let deselectedMonthlyValue = deselectedMonthly.value as? String ?? ""
        XCTAssertFalse(deselectedMonthlyValue.isEmpty)
        XCTAssertFalse(deselectedMonthlyValue.contains("Selected"))
        XCTAssertTrue(app.buttons["paywallPurchaseButton"].exists)

        let terms = app.buttons["paywallTermsLink"]
        scrollUntilHittable(terms, in: app)
        terms.tap()
        XCTAssertTrue(app.navigationBars["Terms of Use"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Using Volta"].exists)
        app.navigationBars.buttons.firstMatch.tap()

        let privacy = app.buttons["paywallPrivacyLink"]
        scrollUntilHittable(privacy, in: app)
        privacy.tap()
        XCTAssertTrue(app.navigationBars["Privacy"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Configured Volta service"].waitForExistence(timeout: 5))

        attachScreenshot(named: "paywall-privacy-light")
    }

    func testCatalogUnavailableFailsClosedWithRetryRestoreAndLegalAccess() throws {
        let app = launchPaywall(debugState: "catalog-unavailable", appearance: "light")
        XCTAssertTrue(app.staticTexts["Subscriptions are temporarily unavailable"].waitForExistence(timeout: 10))
        assertAppearance("light", in: app)
        XCTAssertTrue(app.buttons["Retry"].exists)
        XCTAssertTrue(app.buttons["Restore purchases"].exists)
        XCTAssertFalse(app.buttons["paywallPurchaseButton"].exists, "no catalog must mean no guessed purchase offer")
        attachScreenshot(named: "paywall-catalog-unavailable")

        let terms = app.buttons["paywallTermsLink"]
        scrollUntilHittable(terms, in: app)
        XCTAssertTrue(terms.isHittable)
    }

    func testPendingStateAndControlsRemainReachableAtAccessibilityXXXL() throws {
        let app = launchPaywall(
            debugState: "pending",
            appearance: "light",
            extraArguments: ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        )
        let notice = app.staticTexts["Purchase pending"]
        XCTAssertTrue(notice.waitForExistence(timeout: 15))
        assertAppearance("light", in: app)
        scrollUntilFullyVisible(notice, in: app)
        XCTAssertTrue(isFullyVisible(notice, in: app), "Purchase pending must be visibly contained in the screenshot viewport")
        attachScreenshot(named: "paywall-pending-visible-AXXXL")

        let restore = app.buttons["paywallRestoreButton"]
        scrollUntilHittable(restore, in: app)
        XCTAssertTrue(restore.isHittable)
        let privacy = app.buttons["paywallPrivacyLink"]
        scrollUntilHittable(privacy, in: app)
        XCTAssertTrue(privacy.isHittable)
        let terms = app.buttons["paywallTermsLink"]
        scrollUntilHittable(terms, in: app)
        XCTAssertTrue(terms.isHittable)
    }

    func testRealCatalogRendersInDarkMode() throws {
        let app = launchPaywall(appearance: "dark")
        XCTAssertTrue(app.otherElements["paywallProofChain"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.buttons["paywallProduct-volta.plus.monthly"].waitForExistence(timeout: 15))
        assertAppearance("dark", in: app)
        attachScreenshot(named: "paywall-default-dark")
    }

    private func launchPaywall(
        debugState: String? = nil,
        appearance: String,
        extraArguments: [String] = []
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += [
            "-VoltaUITestPaywall",
            "-VoltaUITestAppearance", appearance,
            "-AppleLanguages", "(en)",
            "-AppleLocale", "en_US",
        ]
        if let debugState {
            app.launchArguments += ["-VoltaPaywallDebugState", debugState]
        }
        app.launchArguments += extraArguments
        app.launch()
        return app
    }

    private func launchDefaultOffApp(baseURL: String) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += [
            "-voltaBaseURL", baseURL,
            "-AppleLanguages", "(en)",
            "-AppleLocale", "en_US",
        ]
        app.launch()
        return app
    }

    private func assertAppearance(_ expected: String, in app: XCUIApplication) {
        let probe = anyElement(identifier: "paywallAppearance", in: app)
        XCTAssertTrue(probe.waitForExistence(timeout: 5), "effective appearance probe is missing")
        XCTAssertEqual(probe.value as? String, expected)
    }

    private func anyElement(identifier: String, in app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    private func scrollUntilHittable(_ element: XCUIElement, in app: XCUIApplication, maxSwipes: Int = 10) {
        var attempts = 0
        while (!element.exists || !element.isHittable), attempts < maxSwipes {
            app.swipeUp()
            attempts += 1
        }
    }

    private func scrollUntilFullyVisible(_ element: XCUIElement, in app: XCUIApplication, maxSwipes: Int = 20) {
        var attempts = 0
        while !isFullyVisible(element, in: app), attempts < maxSwipes {
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.74))
            let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.54))
            start.press(forDuration: 0.05, thenDragTo: end)
            attempts += 1
        }
    }

    private func isFullyVisible(_ element: XCUIElement, in app: XCUIApplication) -> Bool {
        guard element.exists else { return false }
        let frame = app.frame
        let visibleFrame = CGRect(
            x: frame.minX,
            y: frame.minY + 80,
            width: frame.width,
            height: max(0, frame.height - 180)
        )
        return visibleFrame.contains(element.frame)
    }

    private func attachScreenshot(named name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
