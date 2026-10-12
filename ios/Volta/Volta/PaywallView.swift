import StoreKit
import SwiftUI

struct PaywallView: View {
    @ObservedObject var manager: SubscriptionManager
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    #if DEBUG
    @Environment(\.colorScheme) private var colorScheme
    #endif
    @State private var selectedProductID = VoltaPlusProduct.monthlyID

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: dynamicTypeSize.isAccessibilitySize ? 28 : 22) {
                    header
                    proofChain

                    if effectiveCatalogUnavailable {
                        unavailableCatalog
                    } else if manager.catalogState == .loading || manager.catalogState == .idle {
                        loadingCatalog
                    } else {
                        planChoices
                        if let notice = visibleNotice { noticeView(notice) }
                        purchaseSection
                    }

                    recoveryAndLegal
                }
                .frame(maxWidth: 640, alignment: .leading)
                .padding(.horizontal, 20)
                .padding(.vertical, 24)
            }
            .background(Theme.canvas)
            .navigationTitle("Volta Plus")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: LegalDocument.self) { LegalDocumentView(document: $0) }
            .onChange(of: manager.products.map(\.id), initial: true) { _, ids in
                if !ids.contains(selectedProductID), let first = ids.first {
                    selectedProductID = first
                }
            }
            .accessibilityIdentifier("voltaPaywall")
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: "bolt.fill")
                    .font(.title2.weight(.bold))
                    .foregroundStyle(Theme.brand)
                    .accessibilityHidden(true)
                Text("Volta Go")
                    .font(.system(.title, design: .rounded, weight: .bold))
            }
            Text("Choose chargers with evidence, not guesswork.")
                .font(.title2.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            Text("One subscription unlocks the cleaned national database and the complete decision experience. Charger safety labels and warnings always stay honest.")
                .font(.body)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
        #if DEBUG
        // The value comes from SwiftUI's effective environment, not from the requested launch
        // argument. A dark test therefore cannot pass while the rendered hierarchy is light.
        .accessibilityValue(colorScheme == .dark ? "dark" : "light")
        .accessibilityIdentifier("paywallAppearance")
        #endif
    }

    private var proofChain: some View {
        VStack(alignment: .leading, spacing: 0) {
            proofStep(
                index: 1,
                title: String(localized: "Evidence-checked charger database"),
                detail: String(localized: "Public sources cleaned and matched, with verification shown only where evidence supports it."),
                color: Theme.verify,
                drawsLine: true
            )
            proofStep(
                index: 2,
                title: String(localized: "Honest live status"),
                detail: String(localized: "Live, stale, directory, and community evidence never blur together."),
                color: Theme.brand,
                drawsLine: true
            )
            proofStep(
                index: 3,
                title: String(localized: "Instructions before you drive"),
                detail: String(localized: "Price, parking, connector, and source facts stay in one decision."),
                color: .primary,
                drawsLine: false
            )
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("paywallProofChain")
    }

    private func proofStep(index: Int, title: String, detail: String, color: Color, drawsLine: Bool) -> some View {
        HStack(alignment: .top, spacing: 13) {
            VStack(spacing: 0) {
                Text("\(index)")
                    .font(.system(.caption, design: .rounded, weight: .bold))
                    .monospacedDigit()
                    .foregroundStyle(.primary)
                    .frame(width: 28, height: 28)
                    .background(color.opacity(0.13), in: .circle)
                if drawsLine {
                    Rectangle().fill(color.opacity(0.28)).frame(width: 2, height: 30)
                }
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.headline)
                Text(detail).font(.subheadline).foregroundStyle(.secondary)
            }
            .padding(.bottom, drawsLine ? 14 : 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Step \(index): \(title). \(detail)")
    }

    private var planChoices: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Choose your plan").font(.headline)
            ForEach(manager.products, id: \.id) { product in
                productChoice(product)
            }
        }
    }

    private func productChoice(_ product: Product) -> some View {
        let isSelected = selectedProductID == product.id
        let period = PaywallPresentation.recurringPeriod(for: product)
        let savings = product.id == VoltaPlusProduct.yearlyID
            ? manager.monthly.flatMap { PaywallPresentation.yearlySavingsText(monthly: $0, yearly: product) }
            : nil

        return Button {
            selectedProductID = product.id
        } label: {
            Group {
                if dynamicTypeSize.isAccessibilitySize {
                    VStack(alignment: .leading, spacing: 8) { productChoiceContent(product, period: period, savings: savings, isSelected: isSelected) }
                } else {
                    HStack(alignment: .center, spacing: 12) { productChoiceContent(product, period: period, savings: savings, isSelected: isSelected) }
                }
            }
            .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
            .padding(14)
            .background(Theme.raisedSurface, in: .rect(cornerRadius: 12, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .strokeBorder(isSelected ? Theme.brand : Color.primary.opacity(0.13), lineWidth: isSelected ? 2 : 1)
            )
        }
        .buttonStyle(VoltaPressButtonStyle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel(product.displayName)
        .accessibilityValue([product.displayPrice, period, savings, isSelected ? String(localized: "Selected") : nil].compactMap { $0 }.joined(separator: ", "))
        .accessibilityHint("Selects this subscription plan")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        .accessibilityIdentifier("paywallProduct-\(product.id)")
    }

    @ViewBuilder
    private func productChoiceContent(_ product: Product, period: String?, savings: String?, isSelected: Bool) -> some View {
        Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
            .font(.title3)
            .foregroundStyle(isSelected ? Theme.brand : .secondary)
            .accessibilityHidden(true)
        VStack(alignment: .leading, spacing: 3) {
            Text(product.displayName).font(.headline)
            if let period { Text(period).font(.subheadline).foregroundStyle(.secondary) }
        }
        if !dynamicTypeSize.isAccessibilitySize { Spacer(minLength: 8) }
        VStack(alignment: dynamicTypeSize.isAccessibilitySize ? .leading : .trailing, spacing: 3) {
            Text(product.displayPrice)
                .font(.system(.headline, design: .rounded, weight: .bold))
                .monospacedDigit()
            if let savings {
                Text(savings)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.primary)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Theme.brand.opacity(0.14), in: .capsule)
            }
        }
    }

    private var purchaseSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let product = selectedProduct {
                let offer = manager.eligibleTrialOffer(for: product)
                Text(PaywallPresentation.disclosure(for: product, eligibleTrial: offer))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                Button {
                    Task {
                        do { _ = try await manager.purchase(product) }
                        catch { /* manager publishes a fail-closed, user-facing state */ }
                    }
                } label: {
                    HStack(spacing: 8) {
                        if manager.purchaseState == .purchasing { ProgressView().tint(.white) }
                        Text(manager.purchaseState == .purchasing
                             ? String(localized: "Contacting App Store…")
                             : PaywallPresentation.purchaseButtonTitle(for: product, eligibleTrial: offer))
                            .font(.headline)
                    }
                    .frame(maxWidth: .infinity)
                    .frame(minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.action)
                .disabled(manager.purchaseState == .purchasing || manager.restoreState == .restoring)
                .accessibilityHint("Apple will show the final purchase confirmation")
                .accessibilityIdentifier("paywallPurchaseButton")
            }
        }
    }

    private var loadingCatalog: some View {
        HStack(spacing: 12) {
            ProgressView()
            Text("Loading App Store plans…").font(.headline)
        }
        .frame(maxWidth: .infinity, minHeight: 88)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Loading App Store plans")
        .accessibilityValue("In progress")
    }

    private var unavailableCatalog: some View {
        ContentUnavailableView {
            Label("Subscriptions are temporarily unavailable", systemImage: "exclamationmark.arrow.trianglehead.2.clockwise.rotate.90")
        } description: {
            Text("No price or plan was guessed. Retry the App Store catalog or restore an existing purchase.")
        } actions: {
            Button("Retry") { Task { await manager.retryCatalog() } }
                .buttonStyle(.borderedProminent)
                .tint(Theme.action)
                .frame(minHeight: 44)
                .accessibilityIdentifier("paywallRetryButton")
        }
        .accessibilityIdentifier("paywallCatalogUnavailable")
    }

    private var recoveryAndLegal: some View {
        VStack(spacing: 14) {
            Button {
                Task { await manager.restore() }
            } label: {
                HStack(spacing: 8) {
                    if manager.restoreState == .restoring { ProgressView() }
                    Text(manager.restoreState == .restoring ? "Restoring purchases…" : "Restore purchases")
                }
                .frame(maxWidth: .infinity)
                .frame(minHeight: 44)
            }
            .buttonStyle(.bordered)
            .disabled(manager.restoreState == .restoring || manager.purchaseState == .purchasing)
            .accessibilityHint("Checks this Apple ID for a verified Volta Plus subscription")
            .accessibilityIdentifier("paywallRestoreButton")

            if let restoreNotice = PaywallNotice.restore(manager.restoreState) {
                noticeView(restoreNotice)
            }

            Text("No Volta account. Your subscription follows the Apple ID used for App Store purchases.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            Group {
                if dynamicTypeSize.isAccessibilitySize {
                    VStack(spacing: 4) { legalLinks }
                } else {
                    HStack(spacing: 24) { legalLinks }
                }
            }
            .font(.footnote.weight(.semibold))
        }
        .frame(maxWidth: .infinity)
    }

    private var selectedProduct: Product? {
        manager.products.first { $0.id == selectedProductID }
    }

    @ViewBuilder
    private var legalLinks: some View {
        NavigationLink("Terms", value: LegalDocument.terms)
            .frame(minHeight: 44)
            .accessibilityIdentifier("paywallTermsLink")
        NavigationLink("Privacy", value: LegalDocument.privacy)
            .frame(minHeight: 44)
            .accessibilityIdentifier("paywallPrivacyLink")
    }

    private var effectiveCatalogUnavailable: Bool {
        #if DEBUG
        if PaywallDebugLaunch.state == .catalogUnavailable { return true }
        #endif
        return manager.catalogState == .unavailable
    }

    private var visibleNotice: PaywallNotice? {
        #if DEBUG
        switch PaywallDebugLaunch.state {
        case .pending: return PaywallNotice.purchase(.pending)
        case .cancelled: return PaywallNotice.purchase(.cancelled)
        case .gracePeriod: return PaywallNotice.subscription(.gracePeriod)
        case .billingRetry: return PaywallNotice.subscription(.billingRetry)
        case .expired: return PaywallNotice.subscription(.expired)
        case .refunded: return PaywallNotice.subscription(.refunded)
        case .revoked: return PaywallNotice.subscription(.revoked)
        case .catalogUnavailable, nil: break
        }
        #endif
        return PaywallNotice.purchase(manager.purchaseState)
            ?? PaywallNotice.subscription(manager.standing, detail: manager.standingDetail)
    }

    private func noticeView(_ notice: PaywallNotice) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(notice.title).font(.headline)
            Text(notice.detail).font(.subheadline).fixedSize(horizontal: false, vertical: true)
        }
        // Status tints remain in the container. All regular-sized copy uses the adaptive
        // primary label color so warning/success hues never lower text contrast below 4.5:1.
        .foregroundStyle(.primary)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(noticeColor(notice.tone).opacity(0.10), in: .rect(cornerRadius: 14))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("paywallNotice")
    }

    private func noticeColor(_ tone: PaywallNotice.Tone) -> Color {
        switch tone {
        case .neutral: .secondary
        case .caution: Theme.check
        case .danger: Theme.danger
        case .success: Theme.go
        }
    }
}
