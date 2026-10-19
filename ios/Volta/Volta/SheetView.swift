import Foundation
import SwiftUI
import UIKit

/// The sheet: search and filters stay pinned at the top, and everything below them is one
/// scroller (results heading, status notes, then ranked stations). Choosing a station pushes
/// its details inside the same sheet. Mirrors the web results panel (docs/design-system.md 5).
struct SheetView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.openURL) private var openURL
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    #if DEBUG
    @Environment(\.colorScheme) private var colorScheme
    #endif
    @FocusState private var searchFocused: Bool
    @Binding var detent: PresentationDetent
    var onOpenSettings: () -> Void = {}
    var onSelect: (Station) -> Void = { _ in }
    var onPeekHeight: (CGFloat) -> Void = { _ in }
    /// Selection haptic for a station row tap. One shared toggle on the always-mounted list:
    /// rows are closures inside ForEach, so a per-row trigger would miss taps.
    @State private var rowTapFeedback = false

    private var searching: Bool { searchFocused || !state.searchText.isEmpty }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                searchRow
                    .padding(.horizontal, 16)
                    .padding(.top, 14)
                    .padding(.bottom, 10)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height in
                        // The collapsed sheet shows the search row plus the drag indicator.
                        onPeekHeight(height + 12)
                    }

                if !searching && !overviewFailed {
                    MapFilterBar()
                        .padding(.bottom, 6)
                }

                Divider().overlay(Theme.hairline)

                List {
                    if searching {
                        Section {
                            searchContent
                        } header: {
                            Text("Places")
                        }
                    } else {
                        NavigationLink {
                            VehicleGarageView()
                        } label: {
                            VehicleSummaryCard()
                        }
                        .buttonStyle(VoltaPressButtonStyle(pressedScale: 0.985))
                        .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
                        .listRowSeparator(.hidden)
                        .listRowBackground(Color.clear)
                        .accessibilityHint("Opens your car's battery, range and connection")
                        resultsHeading
                        statusRows
                        if !state.savedStationsInView.isEmpty || state.showingSavedOnly {
                            savedRow
                        }
                        stationRows
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .scrollDismissesKeyboard(.immediately)
            }
            .background(Theme.canvas)
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(item: $state.selected) { station in
                StationDetailView(station: station)
            }
            .sensoryFeedback(.selection, trigger: rowTapFeedback)
            .onChange(of: searchFocused) { _, focused in
                if focused { detent = .large }
            }
        }
    }

    // MARK: - Pinned search

    private var searchRow: some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
                TextField("Search a city…", text: $state.searchText)
                    .focused($searchFocused)
                    .autocorrectionDisabled()
                    .submitLabel(.search)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("discoverySearchField")
                    .onChange(of: state.searchText) { _, text in state.searchChanged(text) }
                if !state.searchText.isEmpty {
                    Button {
                        state.searchText = ""
                        state.searchChanged("")
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.tertiary)
                            .frame(width: 44, height: 44)
                            .contentShape(.circle)
                    }
                    .buttonStyle(VoltaPressButtonStyle())
                    .accessibilityLabel("Clear search text")
                }
            }
            .padding(.leading, 12)
            .background(Theme.surface, in: .rect(cornerRadius: 14, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(searchFocused ? Theme.actionInk : Theme.hairline, lineWidth: searchFocused ? 1.5 : 1)
            }

            if searching {
                Button {
                    state.clearSearch()
                    searchFocused = false
                } label: {
                    Text("Done")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.actionInk)
                        .frame(minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(VoltaPressButtonStyle())
                .accessibilityLabel("Close search")
                .transition(.opacity)
            } else {
                Button(action: onOpenSettings) {
                    Image(systemName: "gearshape")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                        .frame(width: 44, height: 44)
                        .background(Theme.surface, in: .circle)
                        .overlay { Circle().strokeBorder(Theme.hairline, lineWidth: 1) }
                        .contentShape(.circle)
                }
                .buttonStyle(VoltaPressButtonStyle())
                .accessibilityLabel("Settings")
                .accessibilityIdentifier("settingsButton")
                .transition(.opacity)
            }
        }
        .animation(VoltaMotion.state, value: searching)
    }

    // MARK: - Place search

    @ViewBuilder
    private var searchContent: some View {
        switch state.searchState {
        case .loading:
            HStack(spacing: 10) {
                ProgressView()
                Text("Searching places…")
                    .foregroundStyle(.secondary)
            }
            .frame(minHeight: 44)
            .accessibilityElement(children: .combine)
            .listRowBackground(Color.clear)
        case .failed:
            VStack(alignment: .leading, spacing: 10) {
                Label("Place search is unavailable. Check your connection and try again.",
                      systemImage: "exclamationmark.magnifyingglass")
                    .font(.subheadline)
                    .foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Retry search") { state.retrySearch() }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.action)
                    .frame(minHeight: 44)
            }
            .listRowBackground(Color.clear)
        case .empty:
            Label("No matching places found. Try a nearby city or postcode.",
                  systemImage: "mappin.slash")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .listRowBackground(Color.clear)
        case .results:
            ForEach(state.searchResults) { result in
                Button {
                    searchFocused = false
                    state.pick(result)
                    detent = .medium
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "mappin.circle.fill")
                            .font(.title2)
                            .foregroundStyle(Theme.actionInk)
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(result.name)
                                .font(.body.weight(.semibold))
                                .foregroundStyle(Theme.ink)
                            Text(result.label)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(.rect)
                }
                .buttonStyle(VoltaPressButtonStyle(pressedScale: 0.98))
                .listRowBackground(Color.clear)
            }
        case .idle:
            Text("Type at least 3 characters to search worldwide.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .listRowBackground(Color.clear)
        }
    }

    // MARK: - Results heading and status

    private var resultsHeading: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(state.mode == .overview ? "Explore chargers" : "Chargers nearby")
                .font(.title3.weight(.bold))
                .foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
                .layoutPriority(1)
                .accessibilityAddTraits(.isHeader)
                #if DEBUG
                .accessibilityIdentifier("discoveryAppearance")
                .accessibilityValue(colorScheme == .dark ? "dark" : "light")
                #endif
            Text(state.listRows.count, format: .number)
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .contentTransition(.numericText())
                .animation(reduceMotion ? nil : VoltaMotion.state, value: state.listRows.count)
                .accessibilityLabel("Chargers in this view")
                .accessibilityValue(String(state.listRows.count))
            Spacer(minLength: 8)
            if state.hasActiveFilters {
                Button("Clear filters") { state.clearFilters() }
                    .buttonStyle(VoltaPressButtonStyle())
                    .font(.subheadline.weight(.semibold))
                    .underline()
                    .foregroundStyle(Theme.actionInk)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("clearDiscoveryFilters")
            }
        }
        .frame(minHeight: 44)
        .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 2, trailing: 16))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    /// Status notes scroll away with the results. Each one offers its fix as a button.
    @ViewBuilder
    private var statusRows: some View {
        if state.resultsTruncated {
            Text("Showing part of this area’s charger inventory. Zoom in or search a city to see more.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .listRowSeparator(.hidden)
                .listRowBackground(Color.clear)
        }
        if overviewFailed {
            StatusNote(tone: Theme.danger, systemImage: "exclamationmark.triangle.fill") {
                Text("Known chargers could not be loaded")
                    .font(.subheadline.weight(.semibold))
                Text(state.serviceFailureMessage)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } actions: {
                Button("Retry") { Task { await state.loadOverview() } }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.action)
                    .accessibilityIdentifier("overviewEmptyRetry")
                Button("Settings") { onOpenSettings() }
                    .buttonStyle(.bordered)
                    .tint(Theme.ink)
                    .accessibilityIdentifier("overviewEmptySettings")
            }
        }

        if state.locationState == .denied || state.locationState == .restricted || state.locationState == .failed {
            StatusNote(tone: Theme.check, systemImage: "location.slash.fill") {
                Text(locationRecoveryMessage)
                    .font(.footnote)
            } actions: {
                Button("Search city") { searchFocused = true }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.action)
                    .accessibilityIdentifier("locationRecoverySearch")
                Button("Open Settings") {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                    openURL(url)
                }
                .buttonStyle(.bordered)
                .tint(Theme.ink)
                .accessibilityIdentifier("locationRecoverySettings")
            }
        }

        if state.liveState == .failed {
            Button {
                state.fetchLive(reason: "retry")
            } label: {
                StatusNote(tone: Theme.danger, systemImage: "exclamationmark.triangle.fill") {
                    Text(state.warning
                        ?? (state.leads.isEmpty
                            ? state.serviceFailureMessage
                            : String(localized: "Live source unreachable — showing source-backed leads only (“Not live”).")))
                        .font(.footnote)
                    Text("Try again")
                        .font(.footnote.weight(.bold))
                        .underline()
                }
            }
            .buttonStyle(VoltaPressButtonStyle(pressedScale: 0.98))
            .accessibilityHint("Retries the live charger request")
            .listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 4, trailing: 16))
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)
        }
    }

    private var locationRecoveryMessage: String {
        switch state.locationState {
        case .denied: String(localized: "Location access is off. Search a city or allow location in Settings.")
        case .restricted: String(localized: "Location access is restricted. Search a city or review Settings.")
        case .failed: String(localized: "Your location could not be read. Search a city or review Settings.")
        case .idle, .requesting, .located: ""
        }
    }

    private var overviewFailed: Bool {
        state.mode == .overview && state.overviewState == .failed && state.listRows.isEmpty
    }

    private var savedRow: some View {
        Toggle(isOn: $state.showingSavedOnly) {
            Label(
                state.showingSavedOnly
                    ? String(localized: "Showing saved chargers")
                    : String(localized: "Saved on this iPhone"),
                systemImage: "star.fill"
            )
            .font(.subheadline.weight(.semibold))
        }
        .tint(Theme.action)
        .frame(minHeight: 44)
        .accessibilityHint("Limits the list to chargers you saved on this iPhone. Missing saved chargers stay unknown, not invented.")
        .listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 4, trailing: 16))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    // MARK: - Stations

    @ViewBuilder
    private var stationRows: some View {
        if overviewFailed {
            EmptyView()
        } else if state.listRows.isEmpty {
            emptyState
        } else {
            ForEach(state.listRows) { st in
                Button {
                    rowTapFeedback.toggle()
                    onSelect(st)
                } label: {
                    StationCard(station: st, selected: state.focusedStationID == st.id)
                }
                .buttonStyle(VoltaPressButtonStyle(pressedScale: 0.985))
                .listRowInsets(EdgeInsets(top: 5, leading: 16, bottom: 5, trailing: 16))
                .listRowSeparator(.hidden)
                .listRowBackground(Color.clear)
                .id(st.id)
                .accessibilityIdentifier("stationRow-\(st.id)")
                .accessibilityAddTraits(state.focusedStationID == st.id ? .isSelected : [])
                .accessibilityHint("Opens details and shows this charger on the map")
            }
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 10) {
            if state.mode == .overview, state.overviewState == .loading {
                ForEach(0..<3, id: \.self) { _ in StationCardSkeleton() }
                    .accessibilityHidden(true)
                Text("Loading known chargers…")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
            } else {
                MomentMark(tone: state.liveState == .failed ? .offline : .quiet)
                Text("No chargers in this view")
                    .font(.headline)
                    .foregroundStyle(Theme.ink)
                Text("Pan the map, search a city, or loosen a filter.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) { emptyActions }
                    VStack(spacing: 10) { emptyActions }
                }
                .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 16)
        .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 16))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    @ViewBuilder
    private var emptyActions: some View {
        if state.hasActiveFilters {
            Button {
                state.clearFilters()
            } label: {
                Text("Clear filters").frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.action)
        }
        Button {
            searchFocused = true
        } label: {
            Label("Search city", systemImage: "magnifyingglass")
                .frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.bordered)
        .tint(Theme.ink)
        Button {
            state.locate()
        } label: {
            Label("Near me", systemImage: "location")
                .frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.bordered)
        .tint(Theme.ink)
        .accessibilityLabel(state.locating ? "Finding my location" : "Find my location")
    }
}

/// A tinted note in the results scroller with its fix as buttons, never only a sentence.
private struct StatusNote<Content: View, Actions: View>: View {
    let tone: Color
    let systemImage: String
    @ViewBuilder var content: Content
    @ViewBuilder var actions: Actions

    init(tone: Color, systemImage: String,
         @ViewBuilder content: () -> Content,
         @ViewBuilder actions: () -> Actions = { EmptyView() }) {
        self.tone = tone
        self.systemImage = systemImage
        self.content = content()
        self.actions = actions()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Image(systemName: systemImage)
                    .foregroundStyle(tone)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) { content }
                    .foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            FlowLayout(spacing: 8, lineSpacing: 8) { actions }
                .controlSize(.large)
        }
        .padding(14)
        .background(tone.opacity(0.12), in: .rect(cornerRadius: 14, style: .continuous))
        .accessibilityElement(children: .contain)
        .listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 4, trailing: 16))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }
}
