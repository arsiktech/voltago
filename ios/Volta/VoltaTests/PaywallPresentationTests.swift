import XCTest
@testable import Volta

final class PaywallPresentationTests: XCTestCase {
    func testPaywallRemainsDisabledByDefault() {
        XCTAssertFalse(PaywallConfig.enabled)
    }

    func testYearlySavingsUsesDecimalMathAndOmitsNonSavings() throws {
        let fraction = try XCTUnwrap(
            PaywallMath.yearlySavingsFraction(monthlyPrice: Decimal(string: "4.99")!, yearlyPrice: Decimal(string: "39.99")!)
        )
        XCTAssertGreaterThan(fraction, Decimal(string: "0.33")!)
        XCTAssertLessThan(fraction, Decimal(string: "0.34")!)
        XCTAssertNil(PaywallMath.yearlySavingsFraction(monthlyPrice: 4, yearlyPrice: 48))
        XCTAssertNil(PaywallMath.yearlySavingsFraction(monthlyPrice: 4, yearlyPrice: 60))
        XCTAssertNil(PaywallMath.yearlySavingsFraction(monthlyPrice: 0, yearlyPrice: 1))
    }

    func testLocalizedSavingsPercentUsesRequestedLocale() {
        XCTAssertEqual(
            PaywallMath.localizedPercent(Decimal(string: "0.25")!, locale: Locale(identifier: "en_US")),
            "25%"
        )
    }

    func testAccessLatchNeverRetractsAccessWithinAProcess() {
        var latch = LaunchAccessLatch()
        latch.observeEntitlement(false)
        XCTAssertFalse(latch.hasAccess)
        latch.observeEntitlement(true)
        XCTAssertTrue(latch.hasAccess)
        latch.observeEntitlement(false)
        XCTAssertTrue(latch.hasAccess, "a lapse must be deferred until the next process launch")
    }

    func testStandingChoosesEntitledOrMostActionableVerifiedState() {
        XCTAssertEqual(SubscriptionStanding.preferred([.expired, .billingRetry]), .billingRetry)
        XCTAssertEqual(SubscriptionStanding.preferred([.revoked, .gracePeriod]), .gracePeriod)
        XCTAssertEqual(SubscriptionStanding.preferred([.refunded, .active]), .active)
        XCTAssertEqual(SubscriptionStanding.preferred([]), .unknown)
    }

    func testEveryAdverseSubscriptionStateHasPlainLanguageRecoveryCopy() throws {
        for standing in [
            SubscriptionStanding.gracePeriod, .billingRetry, .expired, .refunded,
            .revoked, .verificationFailed,
        ] {
            let notice = try XCTUnwrap(PaywallNotice.subscription(standing))
            XCTAssertFalse(notice.title.isEmpty)
            XCTAssertFalse(notice.detail.isEmpty)
        }
        XCTAssertNil(PaywallNotice.subscription(.active))
        XCTAssertNil(PaywallNotice.subscription(.unknown))
    }

    func testAutoRenewOffAndGraceDeadlineProduceDatedStatusCopy() throws {
        let date = Date(timeIntervalSince1970: 1_800_000_000)
        let renewalOff = try XCTUnwrap(PaywallNotice.subscription(
            .active,
            detail: .init(willAutoRenew: false, expirationDate: date, gracePeriodExpirationDate: nil)
        ))
        XCTAssertTrue(renewalOff.detail.contains("2027"))

        let grace = try XCTUnwrap(PaywallNotice.subscription(
            .gracePeriod,
            detail: .init(willAutoRenew: true, expirationDate: nil, gracePeriodExpirationDate: date)
        ))
        XCTAssertTrue(grace.detail.contains("2027"))
    }

    func testPendingCancelledAndFailureAreNotReportedAsSuccess() throws {
        let pending = try XCTUnwrap(PaywallNotice.purchase(.pending))
        let cancelled = try XCTUnwrap(PaywallNotice.purchase(.cancelled))
        let failed = try XCTUnwrap(PaywallNotice.purchase(.failedVerification))
        let syncing = try XCTUnwrap(PaywallNotice.purchase(.entitlementPending))
        XCTAssertEqual(pending.tone, .caution)
        XCTAssertEqual(cancelled.tone, .neutral)
        XCTAssertEqual(failed.tone, .danger)
        XCTAssertEqual(syncing.tone, .caution)
        XCTAssertTrue(syncing.detail.contains("verified"))
        XCTAssertNotEqual(pending.title, PaywallNotice.purchase(.succeeded)?.title)
        XCTAssertNotEqual(cancelled.title, PaywallNotice.purchase(.succeeded)?.title)
    }

    func testRestoreOutcomesAreDistinct() throws {
        let restored = try XCTUnwrap(PaywallNotice.restore(.restored))
        let none = try XCTUnwrap(PaywallNotice.restore(.noActiveSubscription))
        let failed = try XCTUnwrap(PaywallNotice.restore(.failed))
        XCTAssertEqual(restored.tone, .success)
        XCTAssertEqual(none.tone, .neutral)
        XCTAssertEqual(failed.tone, .danger)
        XCTAssertNotEqual(restored.title, none.title)
        XCTAssertNotEqual(none.title, failed.title)
    }

    #if DEBUG
    func testDebugStateParserDoesNotTurnUnknownValuesIntoAState() {
        XCTAssertNil(PaywallDebugState(rawValue: "not-a-real-state"))
        XCTAssertEqual(PaywallDebugState(rawValue: "billing-retry"), .billingRetry)
    }
    #endif
}
