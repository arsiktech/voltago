import Foundation
import MapKit
import SwiftUI

private enum DiscoverySheet: String, Identifiable {
    case settings, aiConnection

    var id: String { rawValue }
}

/// One full-screen map and one persistent sheet, like Apple Maps. The sheet holds search,
/// filters and results; choosing a station from the map or the list opens its details in
/// the same sheet, so the driver never loses the map or their place in the list.
struct RootView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var sheetPresented = true
    @State private var activeSheet: DiscoverySheet?
    @State private var selectedDetent: PresentationDetent = .medium
    /// Height of the sheet's search row, measured, so the collapsed sheet shows exactly it.
    @State private var peekHeight: CGFloat = 92

    var body: some View {
        GeometryReader { proxy in
            // The map frames its content in the part the medium sheet leaves visible, so
            // The initial region, searched city or chosen charger lands above the sheet.
            MapLayer(onSelect: select)
                .safeAreaPadding(.top, 56)
                .safeAreaPadding(.bottom, proxy.size.height * 0.46)
        }
            .ignoresSafeArea()
            .overlay(alignment: .top) {
                VStack(spacing: 10) {
                    DiscoveryMapControls()
                        // Floating chrome grows with text size but stops before it would
                        // cover the map it controls; the sheet carries the full size.
                        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
                    if let moment = state.routeMoment {
                        RouteReadyMomentView(moment: moment)
                            .dynamicTypeSize(...DynamicTypeSize.accessibility2)
                            .transition(reduceMotion ? .opacity : .move(edge: .top).combined(with: .opacity))
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .animation(reduceMotion ? VoltaMotion.state : VoltaMotion.spatial, value: state.routeMoment?.id)
            }
            .sheet(isPresented: $sheetPresented) {
                SheetView(
                    detent: $selectedDetent,
                    onOpenSettings: { activeSheet = .settings },
                    onOpenAIConnection: { activeSheet = .aiConnection },
                    onSelect: select,
                    onPeekHeight: { peekHeight = $0 }
                )
                .presentationDetents(sheetDetents, selection: $selectedDetent)
                .presentationBackgroundInteraction(.enabled(upThrough: .medium))
                // Content is opaque: results, prices and evidence never sit on glass.
                .presentationBackground(Theme.canvas)
                .interactiveDismissDisabled()
                .presentationDragIndicator(.visible)
                .sheet(item: $activeSheet) { sheet in
                    switch sheet {
                    case .settings: SettingsView()
                    case .aiConnection: AIConnectionView()
                    }
                }
                .onChange(of: dynamicTypeSize, initial: true) { _, size in
                    if size.isAccessibilitySize, selectedDetent == peekDetent { selectedDetent = .large }
                }
            }
            .onChange(of: state.locationState, initial: true) { _, locationState in
                if locationState == .denied || locationState == .restricted || locationState == .failed {
                    revealDiscovery()
                }
            }
            .onChange(of: state.overviewState) { _, value in
                if value == .failed { revealDiscovery() }
            }
            .onChange(of: state.liveState) { _, value in
                if value == .failed { revealDiscovery() }
            }
    }

    private var peekDetent: PresentationDetent { .height(peekHeight) }

    private var sheetDetents: Set<PresentationDetent> {
        dynamicTypeSize.isAccessibilitySize ? [.medium, .large] : [peekDetent, .medium, .large]
    }

    private func revealDiscovery() {
        if selectedDetent == peekDetent { selectedDetent = .medium }
    }

    /// Opening a station from the collapsed sheet raises it to show the summary and its
    /// actions; a driver reading the full list keeps the full height, and the map under it
    /// stays put. At accessibility text sizes the summary needs the full height.
    private func select(_ station: Station) {
        state.select(station, moveMap: selectedDetent != .large)
        withAnimation(reduceMotion ? VoltaMotion.state : VoltaMotion.spatial) {
            if dynamicTypeSize.isAccessibilitySize {
                selectedDetent = .large
            } else if selectedDetent == peekDetent {
                selectedDetent = .medium
            }
        }
    }
}

// MARK: - Map

/// Map with price pins and screen-space clustering. SwiftUI's `Map` has no collision
/// engine, so nearby chargers merge into a counted cluster instead of being dropped.
struct MapLayer: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var onSelect: (Station) -> Void
    /// One shared toggle drives the selection haptic for every pin and cluster. It lives on
    /// the always-mounted map: annotations are rebuilt on most pans, and `.sensoryFeedback`
    /// never plays for a freshly mounted view's first value.
    @State private var mapTapFeedback = false
    @State private var mapSize = CGSize(width: 390, height: 844)

    var body: some View {
        Map(position: $state.camera) {
            UserAnnotation()

            ForEach(markers) { marker in
                switch marker {
                case .cluster(let cluster):
                    Annotation("", coordinate: cluster.coordinate) {
                        Button {
                            mapTapFeedback.toggle()
                            zoom(into: cluster)
                        } label: {
                            ClusterBubble(cluster: cluster)
                        }
                        .buttonStyle(VoltaPressButtonStyle())
                        .accessibilityLabel("Charger cluster")
                        .accessibilityValue(cluster.accessibilityValue)
                        .accessibilityHint("Zooms in to inspect this area")
                    }
                    .annotationTitles(.hidden)
                case .station(let st):
                    Annotation("", coordinate: st.coordinate) {
                        Button {
                            mapTapFeedback.toggle()
                            onSelect(st)
                        } label: {
                            PricePill(station: st, selected: state.focusedStationID == st.id)
                        }
                        .buttonStyle(VoltaPressButtonStyle())
                        .accessibilityLabel(st.name)
                        .accessibilityValue(stationAccessibilityValue(st))
                        .accessibilityHint("Opens this charger on the map")
                        .accessibilityAddTraits(state.focusedStationID == st.id ? .isSelected : [])
                    }
                    .annotationTitles(.hidden)
                }
            }
        }
        // Muted Apple Maps keeps roads and labels legible while the one saturated colour
        // on screen stays the Go-now green.
        .mapStyle(.standard(elevation: .flat, emphasis: .muted, pointsOfInterest: .excludingAll))
        .mapControls {
            MapCompass()
            MapScaleView()
        }
        .onGeometryChange(for: CGSize.self) { $0.size } action: { mapSize = $0 }
        .onMapCameraChange(frequency: .onEnd) { ctx in
            let areaChanged = state.visibleRegion.bbox != ctx.region.bbox
            state.visibleRegion = ctx.region
            // MapKit can expand a requested region to fit the screen. Refresh those actual
            // bounds too, so chargers near the fitted edges are included.
            if areaChanged {
                if state.mode == .live { state.loadNearby(reason: "pan") }
                else { Task { await state.loadOverview() } }
            }
        }
        // Sparing: pin and cluster taps are the only map haptic; pan, zoom and locate stay silent.
        .sensoryFeedback(.selection, trigger: mapTapFeedback)
    }

    /// Live first, then free, so the chargers a driver cares about lead each cluster.
    private var inViewRows: [Station] {
        let bbox = state.visibleRegion.paddedBbox
        return state.mapRows
            .filter { bbox.contains($0) }
            .sorted { a, b in
                if a.isRouteReady != b.isRouteReady { return a.isRouteReady }
                if a.isLead != b.isLead { return !a.isLead }
                return (a.isFree == true ? 0 : 1) < (b.isFree == true ? 0 : 1)
            }
    }

    /// Grid clustering in screen space: one cell is about 72 points, so pins never stack.
    /// A cell with one charger shows its pin; a cell with more shows a counted cluster.
    /// Close in (street level) every charger gets its own pin.
    private var markers: [MapMarker] {
        let rows = inViewRows
        let span = state.visibleRegion.span
        if span.latitudeDelta < 0.012 {
            return rows.prefix(160).map { .station($0) }
        }
        let cellLat = span.latitudeDelta / max(mapSize.height / 72, 1)
        let cellLon = span.longitudeDelta / max(mapSize.width / 72, 1)
        var order: [String] = []
        var buckets: [String: [Station]] = [:]
        for st in rows {
            let key = "\(Int((st.lat / cellLat).rounded(.down))):\(Int((st.lon / cellLon).rounded(.down)))"
            if buckets[key] == nil { order.append(key) }
            buckets[key, default: []].append(st)
        }
        return order.prefix(160).compactMap { key in
            guard let group = buckets[key] else { return nil }
            if group.count == 1 { return .station(group[0]) }
            return .cluster(MapCluster(stations: group))
        }
    }

    private func zoom(into cluster: MapCluster) {
        let region = cluster.fittingRegion(minimumSpan: state.visibleRegion.span.latitudeDelta / 8)
        state.visibleRegion = region
        withAnimation(reduceMotion ? nil : VoltaMotion.spatial) {
            state.camera = .region(region)
        }
        if state.mode == .live { state.loadNearby(reason: "cluster") }
    }

    private func stationAccessibilityValue(_ station: Station) -> String {
        var parts: [String] = []
        if station.hasVerifiedPrice { parts.append(station.priceText) }
        parts.append(station.decisionLabel)
        switch station.trustKind {
        case .lead: parts.append(String(localized: "Not live"))
        case .fresh: parts.append(String(localized: "Live status"))
        case .degraded: parts.append(String(localized: "Limited live coverage"))
        case .stale: parts.append(String(localized: "Stale status"))
        case .unavailable: parts.append(String(localized: "Current status unavailable"))
        }
        if let distance = station.distanceText { parts.append(distance) }
        return parts.joined(separator: ", ")
    }
}

enum MapMarker: Identifiable {
    case station(Station)
    case cluster(MapCluster)

    var id: String {
        switch self {
        case .station(let station): "s:" + station.id
        case .cluster(let cluster): "c:" + cluster.id
        }
    }
}

struct MapCluster: Identifiable {
    var coordinate: CLLocationCoordinate2D
    var count: Int
    var hasRouteReady: Bool
    var hasFreeLead: Bool
    var bbox: BBox
    var id: String

    init(stations: [Station]) {
        let lat = stations.map(\.lat).reduce(0, +) / Double(stations.count)
        let lon = stations.map(\.lon).reduce(0, +) / Double(stations.count)
        coordinate = .init(latitude: lat, longitude: lon)
        count = stations.count
        hasRouteReady = stations.contains(where: \.isRouteReady)
        hasFreeLead = stations.contains { $0.isLead && $0.isFree == true }
        bbox = BBox(
            south: stations.map(\.lat).min() ?? lat,
            west: stations.map(\.lon).min() ?? lon,
            north: stations.map(\.lat).max() ?? lat,
            east: stations.map(\.lon).max() ?? lon
        )
        // The first (best) member names the cluster, so it keeps its identity while the
        // map settles instead of re-animating on every small pan.
        id = (stations.first?.id ?? "") + ":\(count)"
    }

    func fittingRegion(minimumSpan: Double) -> MKCoordinateRegion {
        let latSpan = max((bbox.north - bbox.south) * 1.6, minimumSpan)
        let lonSpan = max((bbox.east - bbox.west) * 1.6, minimumSpan)
        return MKCoordinateRegion(
            center: .init(latitude: (bbox.north + bbox.south) / 2, longitude: (bbox.east + bbox.west) / 2),
            span: .init(latitudeDelta: latSpan, longitudeDelta: lonSpan)
        )
    }

    var accessibilityValue: String {
        var parts = [String(localized: "\(count) chargers")]
        if hasRouteReady { parts.append(String(localized: "includes a route-ready option")) }
        else if hasFreeLead { parts.append(String(localized: "includes a free charging lead")) }
        return parts.joined(separator: ", ")
    }
}

/// A counted cluster: ink disc, white count. A green ring appears only when a member is
/// route-ready, so the map's one saturated signal still means "free right now".
struct ClusterBubble: View {
    let cluster: MapCluster

    var body: some View {
        Text(cluster.count, format: .number)
            .font(.system(size: 13, weight: .bold, design: .rounded))
            .monospacedDigit()
            .foregroundStyle(Theme.darkInk)
            .padding(.horizontal, 7)
            .frame(minWidth: 34, minHeight: 34)
            .background(Theme.darkSurface, in: .capsule)
            .overlay {
                Capsule().strokeBorder(cluster.hasRouteReady ? Theme.go : Color.white.opacity(0.9),
                                       lineWidth: cluster.hasRouteReady ? 3 : 1.5)
            }
            .shadow(color: .black.opacity(0.18), radius: 4, y: 2)
            // Compact visual, standard 44-point annotation target.
            .frame(minWidth: 44, minHeight: 44)
            .contentShape(.rect)
    }
}

/// A charger on the map. Only an explicitly verified price is printed on the map; every
/// other charger is a dot. Green fill is reserved for route-ready chargers.
struct PricePill: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let station: Station
    var selected = false

    private var fill: Color {
        if selected { return Theme.darkSurface }
        return station.isRouteReady ? Theme.go : Theme.surface
    }

    private var label: Color {
        if selected { return Theme.darkInk }
        return station.isRouteReady ? .white : Theme.ink
    }

    var body: some View {
        Group {
            if station.hasVerifiedPrice {
                Text(station.priceText)
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(label)
                    .padding(.horizontal, 9)
                    .frame(minHeight: 28)
                    .background(fill, in: .capsule)
                    .overlay {
                        Capsule().strokeBorder(selected ? Theme.darkInk : Theme.hairline, lineWidth: selected ? 2 : 1)
                    }
            } else {
                Circle()
                    .fill(selected ? Theme.darkSurface : (station.isRouteReady ? Theme.go : Theme.verify))
                    .frame(width: 14, height: 14)
                    .overlay { Circle().strokeBorder(.white, lineWidth: 2) }
            }
        }
        .shadow(color: .black.opacity(selected ? 0.28 : 0.16), radius: selected ? 6 : 3, y: 2)
        .scaleEffect(selected && !reduceMotion ? 1.15 : 1)
        .animation(reduceMotion ? VoltaMotion.state : VoltaMotion.spatial, value: selected)
        // The visual stays compact; this transparent frame is the 44-point touch target.
        .frame(minWidth: 44, minHeight: 44)
        .contentShape(.rect)
    }
}

extension MKCoordinateRegion {
    /// Slightly padded so pins at the edge don't pop in and out while panning.
    var paddedBbox: BBox {
        BBox(
            south: center.latitude - span.latitudeDelta * 0.62,
            west: center.longitude - span.longitudeDelta * 0.62,
            north: center.latitude + span.latitudeDelta * 0.62,
            east: center.longitude + span.longitudeDelta * 0.62
        )
    }
}
