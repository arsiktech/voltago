import StoreKit
import StoreKitTest
import XCTest
@testable import Volta

/// StoreKit 2 subscription-engine tests (GOAL_CONTRACT.md Section 6, P4.1). Every test runs
/// against the local `Volta.storekit` configuration via `SKTestSession` -- a fully simulated
/// App Store IN-PROCESS, no App Store Connect dependency, no network. Once a session is
/// active, EVERY StoreKit call `SubscriptionManager` makes (`Product.products(for:)`,
/// `product.purchase()`, `Transaction.currentEntitlements`, `Transaction.updates`,
/// `AppStore.sync()`) is documented by Apple to transparently route to that simulated store.
///
/// `Volta.storekit` lives in `VoltaTests/` only. The shared scheme's Run/Test StoreKit
/// Configuration points there, and the synchronized test target copies it into the test bundle
/// so `SKTestSession` can resolve it by filename. It must never be a resource of `Volta.app`.
///
/// CURRENT PLATFORM CONTRACT: iOS 27+ only. StoreKitTest must pass on the selected iOS 27
/// runtime without mocks, skips, or expected failures. If Apple's simulator runtime fails,
/// move to a newer Xcode/iOS 27 runtime pair. Run this class serially
/// (`-parallel-testing-enabled NO`) because
/// `SKTestSession` mutates process-wide StoreKit state. Never weaken verification assertions to
/// accommodate a broken runtime.
@MainActor
final class SubscriptionTests: XCTestCase {
    private var session: SKTestSession!

    override func setUp() async throws {
        try await super.setUp()
        session = try SKTestSession(configurationFileNamed: "Volta")
        session.resetToDefaultState()
        session.disableDialogs = true
        session.clearTransactions()
        // Ask-to-Buy off: these tests exercise the direct purchase/entitlement/restore path,
        // not the separate family-approval flow.
        session.askToBuyEnabled = false
    }

    override func tearDown() async throws {
        session.clearTransactions()
        session = nil
        try await super.tearDown()
    }

    // MARK: - Product load / offer display

    func testBothProductsLoadWithNonEmptyDisplayPriceAndSevenDayTrialOffer() async throws {
        let manager = SubscriptionManager()
        await manager.loadProducts()

        let monthly = try XCTUnwrap(manager.monthly, "monthly product failed to load")
        let yearly = try XCTUnwrap(manager.yearly, "yearly product failed to load")
        XCTAssertEqual(monthly.id, VoltaPlusProduct.monthlyID)
        XCTAssertEqual(yearly.id, VoltaPlusProduct.yearlyID)
        XCTAssertEqual(manager.products.map(\.id), [VoltaPlusProduct.monthlyID, VoltaPlusProduct.yearlyID])

        // Read straight from `Product` -- never a literal -- proving the price the UI (P4.2)
        // will show is not hardcoded anywhere on this path.
        XCTAssertFalse(monthly.displayPrice.isEmpty)
        XCTAssertFalse(yearly.displayPrice.isEmpty)

        for product in [monthly, yearly] {
            let offer = try XCTUnwrap(
                product.subscription?.introductoryOffer,
                "\(product.id) is missing its 7-day free-trial introductory offer"
            )
            XCTAssertEqual(offer.paymentMode, .freeTrial, "\(product.id)'s intro offer must be a free trial, not a discounted price")
            XCTAssertEqual(
                approximateDays(of: offer.period) * offer.periodCount, 7,
                "\(product.id)'s introductory offer period is not 7 days"
            )
        }
    }

    // MARK: - Purchase

    func testPurchasingMonthlyGrantsEntitlement() async throws {
        let manager = SubscriptionManager()
        await manager.loadProducts()
        let monthly = try XCTUnwrap(manager.monthly)

        XCTAssertFalse(manager.isSubscribed, "must start unsubscribed (fail-closed default)")
        let purchased = try await manager.purchase(monthly)
        XCTAssertTrue(purchased)
        XCTAssertTrue(manager.isSubscribed)

        // Independently confirm the entitlement landed in StoreKit's OWN ledger, not just the
        // manager's local flag.
        var sawEntitlement = false
        for await result in Transaction.currentEntitlements {
            if case let .verified(transaction) = result, transaction.productID == VoltaPlusProduct.monthlyID {
                sawEntitlement = true
            }
        }
        XCTAssertTrue(sawEntitlement, "purchased monthly transaction is not in Transaction.currentEntitlements")
    }

    // MARK: - Trial eligibility

    /// The `AA` prefix is deliberate. StoreKitTest exposes one process-wide test account and
    /// introductory offers are redeemable once per subscription group. Xcode 27 does not reset
    /// that eligibility after `clearTransactions()` within the same test process, so the two
    /// no-purchase eligibility checks run before every transaction-creating test. The suite is
    /// also required to run serially (class contract above).
    func testAAFirstTimeSubscriberIsEligibleForTheFreeTrial() async throws {
        let manager = SubscriptionManager()
        await manager.loadProducts()
        let monthly = try XCTUnwrap(manager.monthly)

        let eligible = await monthly.subscription?.isEligibleForIntroOffer
        XCTAssertEqual(eligible, true, "a first-time subscriber must be offered the 7-day free trial")
        XCTAssertNotNil(monthly.introductoryTrialOffer)
    }

    // MARK: - Expiry

    func testEntitlementLapsesAfterSubscriptionExpires() async throws {
        session.timeRate = .oneRenewalEveryTwoSeconds
        let manager = SubscriptionManager()
        await manager.loadProducts()
        let monthly = try XCTUnwrap(manager.monthly)

        _ = try await manager.purchase(monthly)
        XCTAssertTrue(manager.isSubscribed, "precondition: purchase must succeed before testing expiry")

        let transaction = try XCTUnwrap(
            session.allTransactions().last { $0.productIdentifier == VoltaPlusProduct.monthlyID },
            "StoreKitTest did not expose the purchased monthly transaction"
        )
        try session.disableAutoRenewForTransaction(identifier: transaction.identifier)
        try await waitForSubscriptionState(false, manager: manager, timeout: .seconds(6))
        XCTAssertFalse(manager.isSubscribed, "isSubscribed must drop to false once the entitlement expires -- never strand a lapsed user as subscribed")
        XCTAssertTrue(
            manager.hasGrantedAccessThisLaunch,
            "an entitlement lapse must not replace an in-progress route with the paywall"
        )

        let nextLaunch = SubscriptionManager()
        await nextLaunch.prepareForLaunch()
        XCTAssertTrue(nextLaunch.isLaunchResolved)
        XCTAssertFalse(nextLaunch.isSubscribed)
        XCTAssertFalse(
            nextLaunch.hasGrantedAccessThisLaunch,
            "the next cold launch must enforce the expired entitlement"
        )
    }

    // MARK: - Restore

    func testRestoreOnAFreshManagerRecoversAnExistingEntitlement() async throws {
        let purchaser = SubscriptionManager()
        await purchaser.loadProducts()
        let yearly = try XCTUnwrap(purchaser.yearly)
        _ = try await purchaser.purchase(yearly)
        XCTAssertTrue(purchaser.isSubscribed)

        // A brand-new manager instance (e.g. the next cold launch) starts with no local
        // state -- restore() must recover the SAME entitlement purely from StoreKit's own
        // records, not anything cached by the first instance.
        let fresh = SubscriptionManager()
        XCTAssertFalse(fresh.isSubscribed)
        await fresh.restore()
        XCTAssertTrue(fresh.isSubscribed, "restore() must recover the existing yearly entitlement on a fresh manager")
        XCTAssertEqual(fresh.restoreState, .restored)
        XCTAssertTrue(fresh.hasGrantedAccessThisLaunch)
    }

    // MARK: - Cold-launch admission

    func testColdLaunchAdmitsExistingSubscriberWithoutShowingLockedState() async throws {
        let purchaser = SubscriptionManager()
        await purchaser.loadProducts()
        let monthly = try XCTUnwrap(purchaser.monthly)
        _ = try await purchaser.purchase(monthly)

        let coldLaunch = SubscriptionManager()
        XCTAssertFalse(coldLaunch.isLaunchResolved)
        await coldLaunch.prepareForLaunch()

        XCTAssertTrue(coldLaunch.isLaunchResolved)
        XCTAssertTrue(coldLaunch.isSubscribed)
        XCTAssertTrue(coldLaunch.hasGrantedAccessThisLaunch)
        XCTAssertEqual(coldLaunch.catalogState, .available)
    }

    func testABEligibleTrialHelperRequiresBothOfferAndStoreKitEligibility() async throws {
        let manager = SubscriptionManager()
        await manager.loadProducts()
        let monthly = try XCTUnwrap(manager.monthly)
        let offer = try XCTUnwrap(manager.eligibleTrialOffer(for: monthly))

        XCTAssertEqual(offer.paymentMode, .freeTrial)
        XCTAssertEqual(approximateDays(of: offer.period) * offer.periodCount, 7)
        XCTAssertFalse(PaywallPresentation.trialDuration(for: offer, product: monthly).isEmpty)
        XCTAssertFalse(PaywallPresentation.disclosure(for: monthly, eligibleTrial: offer).isEmpty)
    }

    // MARK: - Helpers

    /// StoreKitTest commits an expiry before `Transaction.currentEntitlements` necessarily
    /// reflects it. Poll the real manager for a tightly bounded window instead of racing that
    /// eventually-consistent ledger.
    private func waitForSubscriptionState(
        _ expected: Bool,
        manager: SubscriptionManager,
        timeout: Duration = .seconds(2)
    ) async throws {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: timeout)

        repeat {
            await manager.refreshEntitlement()
            if manager.isSubscribed == expected {
                return
            }

            guard clock.now < deadline else {
                XCTFail("isSubscribed did not become \(expected) within the bounded StoreKitTest propagation window")
                return
            }
            try await Task.sleep(for: .milliseconds(50))
        } while true
    }

    /// Converts a `Product.SubscriptionPeriod` to an approximate day count so the "7-day
    /// free trial" assertion is robust to which ISO-8601 unit `Volta.storekit` expresses it
    /// in (`P7D` and `P1W` both mean the same 7 days to a driver reading "7-day free trial").
    private func approximateDays(of period: Product.SubscriptionPeriod) -> Int {
        switch period.unit {
        case .day: return period.value
        case .week: return period.value * 7
        case .month: return period.value * 30
        case .year: return period.value * 365
        @unknown default: return -1
        }
    }
}
