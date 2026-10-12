import Foundation
import StoreKit
import SwiftUI

enum PaywallMath {
    static func yearlySavingsFraction(monthlyPrice: Decimal, yearlyPrice: Decimal) -> Decimal? {
        let annualMonthlyPrice = monthlyPrice * 12
        guard annualMonthlyPrice > 0, yearlyPrice >= 0, yearlyPrice < annualMonthlyPrice else {
            return nil
        }
        return (annualMonthlyPrice - yearlyPrice) / annualMonthlyPrice
    }

    static func localizedPercent(_ fraction: Decimal, locale: Locale = .current) -> String {
        let value = NSDecimalNumber(decimal: fraction).doubleValue
        return value.formatted(
            .percent
                .precision(.fractionLength(0))
                .locale(locale)
        )
    }
}

enum PaywallPresentation {
    static func recurringPeriod(for product: Product) -> String? {
        guard let period = product.subscription?.subscriptionPeriod else { return nil }
        return period.formatted(product.subscriptionPeriodFormatStyle)
    }

    static func trialDuration(for offer: Product.SubscriptionOffer, product: Product) -> String {
        let period = offer.period.formatted(product.subscriptionPeriodFormatStyle)
        guard offer.periodCount != 1 else { return period }
        return String(localized: "\(offer.periodCount) × \(period)")
    }

    static func yearlySavingsText(monthly: Product, yearly: Product) -> String? {
        guard let fraction = PaywallMath.yearlySavingsFraction(
            monthlyPrice: monthly.price,
            yearlyPrice: yearly.price
        ) else { return nil }
        return String(localized: "Save \(PaywallMath.localizedPercent(fraction))")
    }

    static func disclosure(for product: Product, eligibleTrial: Product.SubscriptionOffer?) -> String {
        guard let period = recurringPeriod(for: product) else {
            return String(localized: "The App Store will show the full terms before purchase. Cancel in App Store subscription settings.")
        }
        if let eligibleTrial {
            let trial = trialDuration(for: eligibleTrial, product: product)
            return String(localized: "Eligible now: \(trial) free. Then \(product.displayPrice) every \(period) until cancelled. Eligibility is confirmed by the App Store. Cancel in App Store subscription settings.")
        }
        return String(localized: "\(product.displayPrice) is charged now and renews every \(period) until cancelled. Cancel in App Store subscription settings.")
    }

    static func purchaseButtonTitle(for product: Product, eligibleTrial: Product.SubscriptionOffer?) -> String {
        if let eligibleTrial {
            return String(localized: "Start \(trialDuration(for: eligibleTrial, product: product)) free")
        }
        return String(localized: "Subscribe for \(product.displayPrice)")
    }
}

struct PaywallNotice: Equatable {
    enum Tone: Equatable { case neutral, caution, danger, success }

    var title: String
    var detail: String
    var tone: Tone

    static func subscription(_ standing: SubscriptionStanding, detail: SubscriptionStatusDetail? = nil) -> Self? {
        switch standing {
        case .unknown:
            return nil
        case .active:
            guard detail?.willAutoRenew == false else { return nil }
            return .init(
                title: String(localized: "Auto-renewal is off"),
                detail: dated(
                    prefix: String(localized: "Your access continues through the current paid period"),
                    date: detail?.expirationDate
                ),
                tone: .neutral
            )
        case .gracePeriod:
            return .init(
                title: String(localized: "Billing grace period"),
                detail: dated(
                    prefix: String(localized: "Your access continues while Apple retries payment. Review your App Store payment method"),
                    date: detail?.gracePeriodExpirationDate
                ),
                tone: .caution
            )
        case .billingRetry:
            return .init(
                title: String(localized: "Payment needs attention"),
                detail: String(localized: "Apple is retrying the renewal. Update your App Store payment method, then restore purchases."),
                tone: .caution
            )
        case .expired:
            return .init(
                title: String(localized: "Subscription expired"),
                detail: String(localized: "Choose a plan to start a new subscription, or restore if you renewed on another device."),
                tone: .neutral
            )
        case .refunded:
            return .init(
                title: String(localized: "Purchase refunded"),
                detail: String(localized: "Apple revoked this subscription after a refund. Choose a plan to subscribe again."),
                tone: .danger
            )
        case .revoked:
            return .init(
                title: String(localized: "Subscription revoked or refunded"),
                detail: String(localized: "Older StoreKit versions cannot distinguish every revocation reason. This subscription no longer grants access; restore purchases or choose a plan."),
                tone: .danger
            )
        case .verificationFailed:
            return .init(
                title: String(localized: "Purchase could not be verified"),
                detail: String(localized: "Access was not granted. Retry or restore through the App Store."),
                tone: .danger
            )
        }
    }

    private static func dated(prefix: String, date: Date?) -> String {
        guard let date else { return prefix + "." }
        return String(localized: "\(prefix) until \(date.formatted(date: .abbreviated, time: .omitted)).")
    }

    static func purchase(_ state: PurchaseState) -> Self? {
        switch state {
        case .idle, .purchasing:
            nil
        case .succeeded:
            .init(title: String(localized: "Subscription active"), detail: String(localized: "Volta Plus is ready."), tone: .success)
        case .pending:
            .init(title: String(localized: "Purchase pending"), detail: String(localized: "Apple is waiting for approval. You can return later; access starts only after verification."), tone: .caution)
        case .cancelled:
            .init(title: String(localized: "Purchase cancelled"), detail: String(localized: "Nothing was charged."), tone: .neutral)
        case .failed:
            .init(title: String(localized: "Purchase not completed"), detail: String(localized: "The App Store could not complete the purchase. Please try again."), tone: .danger)
        case .failedVerification:
            .init(title: String(localized: "Purchase could not be verified"), detail: String(localized: "Access was not granted. Retry or restore through the App Store."), tone: .danger)
        case .entitlementPending:
            .init(title: String(localized: "Access is still syncing"), detail: String(localized: "Your purchase was verified. Restore purchases or try again in a moment."), tone: .caution)
        }
    }

    static func restore(_ state: RestoreState) -> Self? {
        switch state {
        case .idle, .restoring:
            nil
        case .restored:
            .init(title: String(localized: "Purchases restored"), detail: String(localized: "Your verified subscription is active."), tone: .success)
        case .noActiveSubscription:
            .init(title: String(localized: "No active subscription found"), detail: String(localized: "Apple found no active Volta Plus subscription for this Apple ID."), tone: .neutral)
        case .failed:
            .init(title: String(localized: "Restore failed"), detail: String(localized: "The App Store could not sync purchases. Check your connection and try again."), tone: .danger)
        }
    }
}

#if DEBUG
enum PaywallDebugState: String {
    case catalogUnavailable = "catalog-unavailable"
    case pending, cancelled, gracePeriod = "grace-period", billingRetry = "billing-retry"
    case expired, refunded, revoked
}

enum PaywallDebugLaunch {
    static var forcePaywall: Bool {
        ProcessInfo.processInfo.arguments.contains("-VoltaUITestPaywall")
    }

    /// Deterministic visual-test seam. It is both defined and consumed under `#if DEBUG`, so
    /// Release has no launch argument, parser, or color-scheme override to discover or invoke.
    static var preferredColorScheme: ColorScheme? {
        guard forcePaywall else { return nil }
        let arguments = ProcessInfo.processInfo.arguments
        guard
            let key = arguments.firstIndex(of: "-VoltaUITestAppearance"),
            arguments.indices.contains(key + 1)
        else { return nil }
        switch arguments[key + 1].lowercased() {
        case "dark": return .dark
        case "light": return .light
        default: return nil
        }
    }

    static var state: PaywallDebugState? {
        let arguments = ProcessInfo.processInfo.arguments
        guard
            let key = arguments.firstIndex(of: "-VoltaPaywallDebugState"),
            arguments.indices.contains(key + 1)
        else { return nil }
        return PaywallDebugState(rawValue: arguments[key + 1])
    }
}
#endif
