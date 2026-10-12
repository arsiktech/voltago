import SwiftUI

/// Small floating map chrome: the brand lockup, Find my location, and a "Search this
/// area" capsule that appears once the driver has zoomed in on the national overview.
/// Search, filters and Settings live in the sheet, so this row never hides discovery.
struct DiscoveryMapControls: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Label("Volta Go", systemImage: "bolt.fill")
                    .font(.system(.subheadline, design: .rounded).weight(.heavy))
                    .labelStyle(.titleAndIcon)
                    .padding(.horizontal, 14)
                    .frame(minHeight: 44)
                    .voltaGlass(in: Capsule())
                    .accessibilityAddTraits(.isHeader)

                Spacer(minLength: 0)

                Button { state.locate() } label: {
                    Image(systemName: state.locating ? "location.fill" : "location")
                        .font(.body.weight(.semibold))
                        .frame(width: 44, height: 44)
                        .contentShape(.circle)
                }
                .buttonStyle(VoltaPressButtonStyle())
                .voltaGlass(in: Circle())
                .symbolEffect(.pulse, isActive: state.locating && !reduceMotion)
                .accessibilityLabel(state.locating ? "Finding my location" : "Find my location")
                .accessibilityValue(state.locating ? "In progress" : "")
                .accessibilityHint("Centers the map and loads live charger status nearby")
                .accessibilityIdentifier("locateButton")
            }

            if showsSearchArea {
                Button { state.fetchLive(reason: "area") } label: {
                    Label("Search this area", systemImage: "arrow.clockwise")
                        .font(.subheadline.weight(.semibold))
                        .padding(.horizontal, 16)
                        .frame(minHeight: 44)
                        .contentShape(.capsule)
                }
                .buttonStyle(VoltaPressButtonStyle())
                .voltaGlass(in: Capsule())
                .accessibilityLabel("Search this area")
                .accessibilityHint("Loads live charger status for the visible map area")
                .accessibilityIdentifier("searchAreaButton")
                .transition(reduceMotion ? .opacity : .scale(scale: 0.9).combined(with: .opacity))
            } else if state.mode == .live, state.liveState == .loading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Updating live status…")
                        .font(.footnote.weight(.semibold))
                }
                .padding(.horizontal, 14)
                .frame(minHeight: 36)
                .voltaGlass(in: Capsule())
                .accessibilityElement(children: .combine)
                .transition(.opacity)
            }
        }
        .voltaGlassGroup(spacing: 10)
        .animation(reduceMotion ? VoltaMotion.state : VoltaMotion.spatial, value: showsSearchArea)
        .animation(VoltaMotion.state, value: state.liveState)
    }

    private var showsSearchArea: Bool {
        state.mode == .overview && state.visibleRegion.span.latitudeDelta < 4
    }
}
