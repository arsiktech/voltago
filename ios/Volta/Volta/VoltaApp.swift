import SwiftUI

@main
struct VoltaApp: App {
    private var shouldUsePaywallGate: Bool {
        #if DEBUG
        return PaywallConfig.enabled || PaywallDebugLaunch.forcePaywall
        #else
        return PaywallConfig.enabled
        #endif
    }

    var body: some Scene {
        WindowGroup {
            if shouldUsePaywallGate {
                #if DEBUG
                PaywallLaunchGate()
                    .preferredColorScheme(PaywallDebugLaunch.preferredColorScheme)
                #else
                PaywallLaunchGate()
                #endif
            } else {
                VoltaContentRoot()
                    #if DEBUG
                    .preferredColorScheme(DiscoveryDebugLaunch.colorScheme)
                    #endif
            }
        }
    }
}

#if DEBUG
/// Scope deterministic discovery screenshots to Debug; Release always follows the device.
enum DiscoveryDebugLaunch {
    static var colorScheme: ColorScheme? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let key = arguments.firstIndex(of: "-VoltaUITestDiscoveryAppearance"),
              arguments.indices.contains(key + 1) else { return nil }
        switch arguments[key + 1].lowercased() {
        case "dark": return .dark
        case "light": return .light
        default: return nil
        }
    }
}
#endif

/// AppState is created only when the full app is actually admitted. This keeps an enabled
/// paywall's dwell time out of the cold-launch signpost and prevents location/network work while
/// locked. With the literal feature flag off, this view is created immediately as before.
private struct VoltaContentRoot: View {
    @StateObject private var state = AppState()
    @StateObject private var garage = VehicleGarage()
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        RootView()
            .environmentObject(state)
            .environmentObject(garage)
            .task { state.start() }
            .task(id: state.api.baseURL.absoluteString + String(describing: scenePhase)) {
                guard scenePhase == .active else { return }
                await garage.run(baseURL: state.api.baseURL)
            }
    }
}

/// Exists only on the enabled/debug branch. It never starts location/network work behind the
/// paywall, and its one-way StoreKit latch keeps an admitted route alive for this process.
private struct PaywallLaunchGate: View {
    @StateObject private var subscriptions = SubscriptionManager()
    @State private var didPresentForcedPaywall = false

    var body: some View {
        Group {
            if subscriptions.hasGrantedAccessThisLaunch && canAdmitAfterForcedPresentation {
                VoltaContentRoot()
            } else if subscriptions.isLaunchResolved || debugCatalogUnavailable {
                PaywallView(manager: subscriptions)
                    .onAppear { didPresentForcedPaywall = true }
            } else {
                VStack(spacing: 14) {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: 28, weight: .bold))
                        .foregroundStyle(Theme.brand)
                    ProgressView("Checking App Store access…")
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("paywallLaunchCheck")
            }
        }
        .task {
            if debugCatalogUnavailable { return }
            await subscriptions.prepareForLaunch()
        }
    }

    private var canAdmitAfterForcedPresentation: Bool {
        #if DEBUG
        return !PaywallDebugLaunch.forcePaywall || didPresentForcedPaywall
        #else
        return true
        #endif
    }

    private var debugCatalogUnavailable: Bool {
        #if DEBUG
        return PaywallDebugLaunch.state == .catalogUnavailable
        #else
        return false
        #endif
    }
}
