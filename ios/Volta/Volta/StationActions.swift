import SwiftUI

/// One action hierarchy for every station surface. Only explicit route readiness earns the
/// primary Maps action; otherwise the public source is primary and directions stay
/// available as the secondary. Peer actions share the row's width and height, and stack
/// at accessibility text sizes.
struct StationActions: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.openURL) private var openURL
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var confirmingDirections = false
    let station: Station

    var body: some View {
        // Out of service: full-width stacked actions, so "Find nearby chargers" never
        // truncates (it is longer than the side-by-side width in every language).
        if dynamicTypeSize.isAccessibilitySize || station.isReportedOutOfService {
            VStack(spacing: 10) { orderedActions(icons: true) }
        } else {
            // Labels stay on one line: narrow phones drop the icons before anything wraps.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { orderedActions(icons: true) }
                HStack(spacing: 10) { orderedActions(icons: false) }
                VStack(spacing: 10) { orderedActions(icons: true) }
            }
        }
    }

    @ViewBuilder
    private func orderedActions(icons: Bool) -> some View {
        // The source itself reports this charger out of service: the next step is an
        // alternative, and directions stay reachable only behind a confirmation.
        if station.isReportedOutOfService {
            nearbyAction(icons: icons)
            mapsAction(prominent: false, icons: icons)
        } else if station.isRouteReady {
            mapsAction(prominent: true, icons: icons)
            sourceAction(prominent: false, icons: icons)
        } else {
            sourceAction(prominent: true, icons: icons)
            mapsAction(prominent: false, icons: icons)
        }
    }

    @ViewBuilder
    private func sourceAction(prominent: Bool, icons: Bool) -> some View {
        if let url = station.sourceURL {
            Button { openURL(url) } label: {
                Label(prominent ? station.sourceActionLabel : String(localized: "Source"),
                      systemImage: "arrow.up.right.square")
                    .labelStyle(VoltaActionLabelStyle(icons: icons))
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .buttonStyle(VoltaActionButtonStyle(prominent: prominent))
            .accessibilityHint("Opens the public evidence page outside Volta")
            .accessibilityIdentifier("stationSourceAction")
        }
    }

    private func nearbyAction(icons: Bool) -> some View {
        Button { state.showChargersNear(station) } label: {
            Label("Find nearby chargers", systemImage: "magnifyingglass")
                .labelStyle(VoltaActionLabelStyle(icons: icons))
                .frame(maxWidth: .infinity, minHeight: 44)
                .fixedSize(horizontal: false, vertical: true)
        }
        .buttonStyle(VoltaActionButtonStyle(prominent: true))
        .accessibilityHint("Closes this charger and shows other chargers around it")
        .accessibilityIdentifier("stationNearbyAction")
    }

    @ViewBuilder
    private func mapsAction(prominent: Bool, icons: Bool) -> some View {
        if let url = station.mapsURL {
            Button {
                if station.isReportedOutOfService {
                    confirmingDirections = true
                } else {
                    openURL(url)
                }
            } label: {
                Label("Open in Maps", systemImage: "arrow.triangle.turn.up.right.diamond.fill")
                    .labelStyle(VoltaActionLabelStyle(icons: icons))
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .buttonStyle(VoltaActionButtonStyle(prominent: prominent))
            .accessibilityHint(prominent
                ? "Opens directions to this route-ready charger"
                : "Opens directions only; verify the station before driving")
            .accessibilityIdentifier("stationMapsAction")
            .confirmationDialog("This charger is reported out of service",
                                isPresented: $confirmingDirections,
                                titleVisibility: .visible) {
                Button("Navigate anyway") { openURL(url) }
                Button("Find nearby chargers") { state.showChargersNear(station) }
            } message: {
                Text("You may not be able to charge there. Navigate anyway?")
            }
        }
    }
}

/// Primary: ink fill with a white label. Secondary: surface fill with a visible border.
/// Both answer the press at once with scale and opacity (one press mechanism).
struct VoltaActionButtonStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var prominent: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .multilineTextAlignment(.center)
            .padding(.horizontal, 12)
            .padding(.vertical, 4)
            .foregroundStyle(prominent ? Color.white : Theme.ink)
            .background(prominent ? Theme.action : Theme.surface, in: .rect(cornerRadius: 14, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(prominent ? Theme.action : Theme.hairlineStrong, lineWidth: 1)
            }
            .contentShape(.rect(cornerRadius: 14, style: .continuous))
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.97 : 1)
            .opacity(configuration.isPressed ? 0.8 : 1)
            .animation(reduceMotion ? nil : VoltaMotion.press, value: configuration.isPressed)
    }
}

private struct VoltaActionLabelStyle: LabelStyle {
    var icons: Bool

    func makeBody(configuration: Configuration) -> some View {
        ActionLabel(icon: icons ? configuration.icon : nil, title: configuration.title)
    }
}

/// One line at standard sizes; wraps freely at accessibility sizes.
private struct ActionLabel<Icon: View, Title: View>: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let icon: Icon?
    let title: Title

    var body: some View {
        HStack(spacing: 8) {
            if let icon { icon }
            title
        }
        .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
    }
}
