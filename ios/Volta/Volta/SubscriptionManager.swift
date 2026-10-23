import Foundation
import StoreKit

/// The two "Volta Plus" auto-renewable subscription product identifiers (GOAL_CONTRACT.md
/// Section 6). IDs only -- never a price. Real prices/currency are set in App Store Connect
/// (production) or the local `Volta.storekit` configuration (Simulator/tests) and are read
/// live from StoreKit's own `Product` values; nothing in this file hardcodes a number.
enum VoltaPlusProduct {
    static let monthlyID = "volta.plus.monthly"
    static let yearlyID = "volta.plus.yearly"
    static let all: [String] = [monthlyID, yearlyID]
}

/// Failures this manager raises itself, beyond whatever `Product.purchase()` already throws.
enum SubscriptionError: Error, LocalizedError {
    /// A transaction StoreKit could not cryptographically verify (jailbroken/tampered
    /// device, corrupted receipt, etc.). Never treated as a successful purchase.
    case failedVerification
    /// StoreKit verified the purchase, but the authoritative entitlement ledger did not
    /// publish access within our bounded wait. This is recoverable and is not mislabeled as
    /// a signature/receipt failure.
    case entitlementNotAvailable

    var errorDescription: String? {
        switch self {
        case .failedVerification:
            return String(localized: "The App Store transaction could not be verified. Access was not granted.")
        case .entitlementNotAvailable:
            return String(localized: "The purchase was verified, but access is not available yet. Restore purchases or try again.")
        }
    }
}

enum ProductCatalogState: Equatable {
    case idle, loading, available, unavailable
}

enum PurchaseState: Equatable {
    case idle, purchasing, succeeded, pending, cancelled, failed, failedVerification, entitlementPending
}

enum RestoreState: Equatable {
    case idle, restoring, restored, noActiveSubscription, failed
}

enum SubscriptionStanding: Equatable {
    case unknown, active, gracePeriod, billingRetry, expired, refunded, revoked, verificationFailed

    static func preferred(_ values: [Self]) -> Self {
        values.max { $0.priority < $1.priority } ?? .unknown
    }

    var priority: Int {
        switch self {
        case .active: 100
        case .gracePeriod: 90
        case .billingRetry: 80
        case .refunded: 70
        case .revoked: 60
        case .expired: 50
        case .verificationFailed: 40
        case .unknown: 0
        }
    }
}

struct SubscriptionStatusDetail: Equatable {
    var willAutoRenew: Bool
    var expirationDate: Date?
    var gracePeriodExpirationDate: Date?
}

/// A one-way process-lifetime latch. Once a verified entitlement admits the driver, later
/// StoreKit updates may enrich status but can never replace an active route with a paywall.
struct LaunchAccessLatch: Equatable {
    private(set) var hasAccess = false

    mutating func observeEntitlement(_ isEntitled: Bool) {
        if isEntitled { hasAccess = true }
    }
}

/// StoreKit 2 subscription engine for "Volta Plus" (GOAL_CONTRACT.md Section 6).
///
/// Owns product loading, purchase, and entitlement state; mirrors `AppState`'s shape
/// (`@MainActor final class ... : ObservableObject`) so a future paywall view (P4.2) can
/// `@StateObject`/`@EnvironmentObject` it the same way `RootView` uses `AppState`.
///
/// NEVER hardcodes a price or currency: every price/period the UI will show comes from
/// `Product.displayPrice` / `Product.priceFormatStyle` / `Product.SubscriptionOffer
/// .displayPrice`, fetched live from StoreKit for whichever storefront/locale the device is
/// in. That is a hard contract, not a style preference -- see docs/backend-engine.md's monetization
/// rule ("never hardcode prices") and GOAL_CONTRACT.md Section 6.
@MainActor
final class SubscriptionManager: ObservableObject {
    /// Loaded "Volta Plus" products, `nil` until `loadProducts()` succeeds (or if the store
    /// has no such product configured, which should never happen once ASC/the local
    /// `.storekit` config is wired correctly).
    @Published private(set) var monthly: Product?
    @Published private(set) var yearly: Product?

    /// Derived purely from `Transaction.currentEntitlements` (see `refreshEntitlement()`) --
    /// never a locally-invented flag. `false` until the first `refreshEntitlement()` call
    /// resolves, which is the safe fail-closed default (an unknown entitlement state must
    /// never be treated as "subscribed").
    @Published private(set) var isSubscribed = false

    /// Resolves once the cold-launch entitlement check has completed. `hasGrantedAccessThisLaunch`
    /// is intentionally one-way; a lapse/revocation is enforced by the next process launch.
    @Published private(set) var isLaunchResolved = false
    @Published private(set) var hasGrantedAccessThisLaunch = false

    @Published private(set) var catalogState: ProductCatalogState = .idle
    @Published private(set) var purchaseState: PurchaseState = .idle
    @Published private(set) var restoreState: RestoreState = .idle
    @Published private(set) var standing: SubscriptionStanding = .unknown
    @Published private(set) var standingDetail: SubscriptionStatusDetail?
    @Published private(set) var introOfferEligibility: [String: Bool] = [:]

    /// Set when `loadProducts()`/`purchase(_:)` fails, for a future paywall UI to surface.
    /// Never a price/currency value -- always an `Error.localizedDescription`.
    @Published private(set) var lastLoadError: String?

    /// Both products in a stable, price-independent order (monthly then yearly) for the
    /// future paywall to lay out -- this code never sorts or branches on price.
    var products: [Product] {
        [monthly, yearly].compactMap { $0 }
    }

    private var transactionUpdatesTask: Task<Void, Never>?
    private var accessLatch = LaunchAccessLatch()

    init() {
        transactionUpdatesTask = listenForTransactionUpdates()
    }

    deinit {
        transactionUpdatesTask?.cancel()
    }

    // MARK: - Loading

    /// Cold-launch admission. Entitlement resolves before product catalog work so a returning
    /// subscriber is admitted without a paywall flash; non-subscribers wait for the real catalog
    /// so the first locked screen never contains invented pricing.
    func prepareForLaunch() async {
        isLaunchResolved = false
        await refreshEntitlement()
        if isSubscribed {
            grantAccessForThisLaunch()
            isLaunchResolved = true
        }

        await loadProducts()
        await refreshSubscriptionStanding()
        isLaunchResolved = true
    }

    /// Fetches `Product`s for both subscription IDs from StoreKit -- App Store Connect in
    /// production, the local `Volta.storekit` configuration under Simulator/`SKTestSession`
    /// with no ASC dependency. Sorts purely by product id, never by price.
    func loadProducts() async {
        catalogState = .loading
        do {
            let loaded = try await Product.products(for: VoltaPlusProduct.all)
            let monthly = loaded.first { $0.id == VoltaPlusProduct.monthlyID }
            let yearly = loaded.first { $0.id == VoltaPlusProduct.yearlyID }
            guard
                let monthly,
                let yearly,
                monthly.type == .autoRenewable,
                yearly.type == .autoRenewable,
                let monthlySubscription = monthly.subscription,
                let yearlySubscription = yearly.subscription,
                monthlySubscription.subscriptionPeriod.unit == .month,
                monthlySubscription.subscriptionPeriod.value == 1,
                yearlySubscription.subscriptionPeriod.unit == .year,
                yearlySubscription.subscriptionPeriod.value == 1
            else {
                self.monthly = nil
                self.yearly = nil
                introOfferEligibility = [:]
                catalogState = .unavailable
                lastLoadError = String(localized: "The App Store did not return the complete Volta Plus catalog.")
                return
            }

            self.monthly = monthly
            self.yearly = yearly
            var eligibility: [String: Bool] = [:]
            for product in [monthly, yearly] {
                eligibility[product.id] = await product.subscription?.isEligibleForIntroOffer ?? false
            }
            introOfferEligibility = eligibility
            lastLoadError = nil
            catalogState = .available
        } catch {
            lastLoadError = error.localizedDescription
            monthly = nil
            yearly = nil
            introOfferEligibility = [:]
            catalogState = .unavailable
        }
    }

    func retryCatalog() async {
        await loadProducts()
        await refreshSubscriptionStanding()
    }

    // MARK: - Purchase

    /// Buys `product`. Returns `true` only once a verified transaction has been finished and
    /// entitlement refreshed. A user cancellation or a pending purchase (e.g. Ask to Buy
    /// awaiting approval) is NOT an error -- both return `false` and leave `isSubscribed`
    /// exactly as it was; if a pending purchase later resolves, the `Transaction.updates`
    /// listener (below) picks it up and refreshes entitlement on its own.
    @discardableResult
    func purchase(_ product: Product) async throws -> Bool {
        purchaseState = .purchasing
        do {
            let result = try await product.purchase()
            switch result {
            case let .success(verification):
                let transaction = try checkVerified(verification)
                await transaction.finish()
                await refreshEntitlementUntilGranted()
                guard isSubscribed else {
                    purchaseState = .entitlementPending
                    throw SubscriptionError.entitlementNotAvailable
                }
                grantAccessForThisLaunch()
                await refreshSubscriptionStanding()
                purchaseState = .succeeded
                return true
            case .userCancelled:
                purchaseState = .cancelled
                return false
            case .pending:
                purchaseState = .pending
                return false
            @unknown default:
                purchaseState = .failed
                return false
            }
        } catch SubscriptionError.failedVerification {
            purchaseState = .failedVerification
            throw SubscriptionError.failedVerification
        } catch SubscriptionError.entitlementNotAvailable {
            purchaseState = .entitlementPending
            throw SubscriptionError.entitlementNotAvailable
        } catch {
            purchaseState = .failed
            throw error
        }
    }

    // MARK: - Entitlement

    /// Re-derives `isSubscribed` from `Transaction.currentEntitlements` -- StoreKit's own
    /// ledger, which already accounts for renewals, refunds, Family Sharing, and restores.
    /// `true` iff a non-revoked entitlement exists for either "Volta Plus" product id.
    func refreshEntitlement() async {
        var subscribed = false
        var sawUnverified = false
        for await result in Transaction.currentEntitlements {
            let transaction: Transaction
            switch result {
            case let .verified(verified):
                transaction = verified
            case let .unverified(unverified, _):
                if VoltaPlusProduct.all.contains(unverified.productID) { sawUnverified = true }
                continue
            }
            if VoltaPlusProduct.all.contains(transaction.productID), transaction.revocationDate == nil {
                subscribed = true
            }
        }
        isSubscribed = subscribed
        if subscribed { grantAccessForThisLaunch() }
        if sawUnverified, !subscribed { standing = .verificationFailed }
    }

    /// Restores purchases: asks the App Store to resync this device's transactions, then
    /// re-derives `isSubscribed` from the (now up to date) entitlement ledger. A sync error
    /// still falls through to whatever verified entitlements already exist on-device.
    func restore() async {
        restoreState = .restoring
        var syncFailed = false
        do {
            try await AppStore.sync()
        } catch {
            syncFailed = true
        }
        await refreshEntitlementUntilGranted()
        await refreshSubscriptionStanding()
        if isSubscribed {
            grantAccessForThisLaunch()
            restoreState = .restored
        } else if syncFailed {
            restoreState = .failed
        } else {
            restoreState = .noActiveSubscription
        }
    }

    /// Renewal state enriches user-facing recovery copy only. Admission remains derived from
    /// `Transaction.currentEntitlements`; every status transaction AND renewalInfo is verified.
    func refreshSubscriptionStanding() async {
        standingDetail = nil
        guard let subscription = monthly?.subscription ?? yearly?.subscription else {
            if standing != .verificationFailed { standing = isSubscribed ? .active : .unknown }
            return
        }

        do {
            let statuses = try await subscription.status
            var verifiedStates: [SubscriptionStanding] = []
            var verifiedDetails: [(standing: SubscriptionStanding, detail: SubscriptionStatusDetail)] = []
            var sawUnverified = false

            for status in statuses {
                do {
                    let transaction = try checkVerified(status.transaction)
                    let renewal = try checkVerified(status.renewalInfo)
                    let mapped: SubscriptionStanding
                    switch status.state {
                    case .subscribed:
                        mapped = .active
                    case .inGracePeriod:
                        mapped = .gracePeriod
                    case .inBillingRetryPeriod:
                        mapped = .billingRetry
                    case .expired:
                        mapped = .expired
                    case .revoked:
                        // Before iOS 26.4 StoreKit cannot say whether a revocation was a
                        // refund; `.revoked` carries the copy that admits exactly that.
                        if #available(iOS 26.4, *),
                           transaction.revocationType == .fullRefund
                            || transaction.revocationType == .proratedRefund {
                            mapped = .refunded
                        } else {
                            mapped = .revoked
                        }
                    default:
                        mapped = .unknown
                    }
                    verifiedStates.append(mapped)
                    verifiedDetails.append((
                        standing: mapped,
                        detail: .init(
                            willAutoRenew: renewal.willAutoRenew,
                            expirationDate: renewal.renewalDate,
                            gracePeriodExpirationDate: renewal.gracePeriodExpirationDate
                        )
                    ))
                } catch {
                    sawUnverified = true
                }
            }

            if verifiedStates.isEmpty, sawUnverified {
                standing = .verificationFailed
            } else if verifiedStates.isEmpty {
                standing = isSubscribed ? .active : .unknown
            } else {
                let preferred = SubscriptionStanding.preferred(verifiedStates)
                standing = preferred
                standingDetail = verifiedDetails
                    .filter { $0.standing == preferred }
                    .max { $0.standing.priority < $1.standing.priority }?
                    .detail
            }
        } catch {
            if standing != .verificationFailed { standing = isSubscribed ? .active : .unknown }
        }
    }

    func eligibleTrialOffer(for product: Product) -> Product.SubscriptionOffer? {
        guard
            introOfferEligibility[product.id] == true,
            let offer = product.subscription?.introductoryOffer,
            offer.paymentMode == .freeTrial
        else { return nil }
        return offer
    }

    /// `purchase()` / `AppStore.sync()` may return just before current entitlements publishes
    /// the verified transaction. Poll the real ledger briefly instead of inventing local access
    /// or mislabeling propagation latency as a verification failure.
    private func refreshEntitlementUntilGranted() async {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(2))
        repeat {
            await refreshEntitlement()
            if isSubscribed || clock.now >= deadline { return }
            try? await Task.sleep(for: .milliseconds(50))
        } while true
    }

    // MARK: - Verification

    /// Unwraps a `VerificationResult`, throwing `SubscriptionError.failedVerification`
    /// instead of ever treating an unverified (unsigned/tampered) transaction as valid.
    /// `nonisolated` because it touches no actor-isolated state -- it is pure verification
    /// logic, callable from the (non-MainActor) transaction-updates loop below without a hop.
    nonisolated func checkVerified<T>(_ result: VerificationResult<T>) throws -> T {
        switch result {
        case .unverified:
            throw SubscriptionError.failedVerification
        case let .verified(safe):
            return safe
        }
    }

    // MARK: - Transaction updates

    /// Listens for transactions StoreKit reports OUTSIDE an explicit `purchase(_:)` call in
    /// this process -- renewals, revocations, Ask-to-Buy approvals, restores initiated from
    /// another device. Started in `init`, cancelled in `deinit`, so `isSubscribed` keeps
    /// reflecting reality even if the user never reopens a paywall screen.
    private func listenForTransactionUpdates() -> Task<Void, Never> {
        Task.detached { [weak self] in
            for await result in Transaction.updates {
                guard let self else { return }
                if let transaction = try? self.checkVerified(result),
                   VoltaPlusProduct.all.contains(transaction.productID) {
                    await transaction.finish()
                }
                await self.refreshEntitlement()
                await self.refreshSubscriptionStanding()
            }
        }
    }

    private func grantAccessForThisLaunch() {
        accessLatch.observeEntitlement(true)
        hasGrantedAccessThisLaunch = accessLatch.hasAccess
    }
}

extension Product {
    /// The 7-day free-trial introductory offer, if StoreKit currently reports one eligible.
    /// A future paywall (P4.2) reads this for "7-day free trial" copy and its own
    /// `displayPrice`/period -- both come from StoreKit, never a literal in this codebase.
    var introductoryTrialOffer: Product.SubscriptionOffer? {
        subscription?.introductoryOffer
    }
}
