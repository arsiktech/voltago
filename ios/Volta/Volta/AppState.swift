import CoreLocation
import Foundation
import MapKit
import os
import SwiftUI
import UIKit

struct RouteMoment: Identifiable, Equatable {
    let id = UUID()
    let stationID: String
    let stationName: String
}

/// App orchestration, mirroring the web client's honesty rules:
/// - default = national leads-only overview (nothing pretends to be live),
/// - live mode = viewport fetch near the user / a searched place,
/// - instant leads paint while the live fetch runs,
/// - FAIL-CLOSED: a failed live fetch never keeps stale live cards — fall back to leads.
@MainActor
final class AppState: NSObject, ObservableObject {
    enum Mode { case overview, live }
    enum LiveState: Equatable { case idle, loading, ok, failed }
    enum OverviewState: Equatable { case idle, loading, loaded, failed }
    enum SearchState: Equatable { case idle, loading, results, empty, failed }
    enum LiveQuality: Equatable { case overview, loading, fresh, degraded, mixed, stale, unavailable }
    enum LocationState: Equatable { case idle, requesting, located, denied, restricted, failed }

    struct LocationAuthorizationTransition: Equatable {
        var locationState: LocationState
        var locating: Bool
        var awaitingAuthorization: Bool
        var shouldRequestLocation: Bool
    }

    // Local cleartext endpoints and user-editable overrides are development tools only.
    // Release ignores UserDefaults and reads one validated HTTPS URL from its built Info.
    #if DEBUG
    @AppStorage("voltaBaseURL") var baseURLString = VoltaRuntimeConfiguration.debugDefaultBaseURLString
    #else
    let baseURLString = VoltaRuntimeConfiguration.baseURL.absoluteString
    #endif

    @Published var mode: Mode = .overview
    @Published var overviewState: OverviewState = .idle
    @Published var liveState: LiveState = .idle
    @Published var liveQuality: LiveQuality = .overview
    @Published var liveCache: ChargersResponse.Cache?
    @Published var stations: [Station] = []
    @Published var leads: [Station] = []
    @Published var resultsTruncated = false
    @Published var warning: String?
    /// Tracks whether `warning` currently holds the client-composed "can't reach the Volta
    /// server" message (set alongside `warning` at every assignment site below), so
    /// `loadOverview()`'s stale-warning recovery clear can key off a locale-INVARIANT flag
    /// instead of `warning?.hasPrefix(...)` -- comparing against the message's own text would
    /// silently stop matching once that text is localized (String(localized:), non-English
    /// device language).
    private var warningIsServerUnreachable = false
    @Published var routeMoment: RouteMoment?

    // Filters. The iOS product surface intentionally exposes verified station rows only.
    @Published var freeOnly = false { didSet { objectWillChange.send() } }
    /// 5.0 is the public-view ceiling (matches the API). Cheap/free remain optional filters.
    @Published var maxPrice = 5.0 { didSet { refetch() } }
    /// Empty means every connector. Multiple selected values use OR semantics: a station stays
    /// visible when it offers at least one connector the driver selected.
    @Published var connectorTypes: Set<String> = []
    @Published var minimumPowerKW = 0.0 { didSet { objectWillChange.send() } }

    /// Compatibility seam for existing tests and callers that set one connector at a time.
    var connectorType: String {
        get { connectorTypes.count == 1 ? connectorTypes.first! : (connectorTypes.isEmpty ? "Any" : connectorTypes.sorted().joined(separator: ", ")) }
        set { connectorTypes = newValue == "Any" ? [] : [newValue] }
    }

    func toggleConnectorType(_ connector: String) {
        if connectorTypes.contains(connector) {
            connectorTypes.remove(connector)
        } else {
            connectorTypes.insert(connector)
        }
    }

    /// Recovery restores the same discovery defaults as a fresh launch, without
    /// moving the map, removing saved chargers, or changing evidence rules.
    var hasActiveFilters: Bool {
        freeOnly || maxPrice < 4.99 || !connectorTypes.isEmpty
            || minimumPowerKW > 0 || showingSavedOnly
    }

    func clearFilters() {
        freeOnly = false
        connectorTypes = []
        minimumPowerKW = 0
        showingSavedOnly = false
        if maxPrice != 5.0 { maxPrice = 5.0 }
    }

    @Published var camera: MapCameraPosition = .region(AppState.spainRegion)
    @Published var visibleRegion: MKCoordinateRegion = AppState.spainRegion
    @Published var focusedStationID: String?
    @Published var selected: Station?
    @Published var favoriteIDs: [String] = FavoritesStore.load().map(\.id)
    @Published var showingSavedOnly = false

    @Published var searchText = ""
    @Published var searchResults: [GeocodeResult] = []
    @Published var searchState: SearchState = .idle

    @Published var userCoordinate: CLLocationCoordinate2D?
    @Published var locating = false
    @Published var locationState: LocationState = .idle

    private let locationManager = CLLocationManager()
    private let apiOverride: VoltaAPI?
    private var liveTask: Task<Void, Never>?
    /// Only the latest overview request may publish. The endpoint reset also invalidates
    /// this token because callers own the tasks that await loadOverview().
    private var overviewRequestID = UUID()
    private var searchTask: Task<Void, Never>?
    private var routeMomentTask: Task<Void, Never>?
    private var refreshTimer: Timer?
    private var previousRouteReadyCount = 0
    private var awaitingLocationAuthorization = false

    // MARK: - Cold-launch instrumentation
    //
    // GOAL_CONTRACT.md Section 5 performance bar: "Cold launch to first meaningful
    // content < 1.5s ... measure with signposts; record the number." This interval
    // measures app start -> first meaningful content, defined precisely as:
    //
    // - START: the instant this AppState is constructed. SwiftUI creates the
    //   `@StateObject` backing VoltaApp's WindowGroup during app launch, before the
    //   first view body is ever evaluated -- the earliest hook available without a
    //   custom AppDelegate/main.swift, and effectively equivalent to VoltaApp.init().
    // - END: the FIRST time `loadOverview()` (called unconditionally from `start()`)
    //   successfully resolves. That is the real first content the driver sees --
    //   `leads` populate `filteredRows`/`mapRows`/`listRows`, replacing the loading
    //   state with actual map pins and list rows -- matching this class's own
    //   documented "instant leads paint" architecture (the fast intelligence-only
    //   query `start()` always fires first, before any live/location fetch).
    //
    // Honest limitation: if location permission is already granted, `start()` also
    // fires `locate()` -> `fetchLive()` concurrently; on the rare run where THAT
    // resolves first, true first content is marginally earlier than what this
    // records. `loadOverview()` is the documented always-runs fast path, so it is
    // the correct, non-cherry-picked measurement anchor.
    //
    // The signpost itself is cheap and safe to ship in Release (standard
    // OSSignposter use, no PII, no user content). Only the convenience `Logger`
    // line used to read the number via `log stream`/Console without attaching
    // Instruments is DEBUG-gated (plain `print()` does not reliably reach the
    // Simulator's unified log/`log stream` capture in this Xcode/runtime -- a
    // `Logger` line does, hence `Logger` over `print` here specifically).
    private static let launchSignposter = OSSignposter(subsystem: "tech.arsik.volta", category: "ColdLaunch")
    private let launchSignpostState = AppState.launchSignposter.beginInterval("ColdLaunchToFirstContent")
    private let launchStart = ContinuousClock.now
    private var didRecordFirstMeaningfulContent = false

    private var cameraAnimation: Animation? {
        UIAccessibility.isReduceMotionEnabled ? nil : .smooth(duration: 0.42)
    }

    /// Announcement notifications speak asynchronous state changes without moving VoiceOver
    /// focus. They are emitted only while VoiceOver is running; TC-056 still requires a real
    /// human traversal and is intentionally not closed by this automated support.
    private func announce(_ message: String) {
        guard UIAccessibility.isVoiceOverRunning else { return }
        UIAccessibility.post(notification: .announcement, argument: message)
    }

    static let spainRegion = MKCoordinateRegion(
        center: .init(latitude: 40.0, longitude: -3.7),
        span: .init(latitudeDelta: 9.5, longitudeDelta: 11.5)
    )

    var isServiceConfigured: Bool {
        if apiOverride != nil { return true }
        #if DEBUG
        return true
        #else
        return ReleaseEndpointPolicy.isConfigured(VoltaRuntimeConfiguration.baseURL)
        #endif
    }

    var serviceFailureMessage: String {
        #if DEBUG
        return String(localized: "Can't reach the Volta server at \(baseURLString) — tap Volta Go and point the app at your Mac.")
        #else
        return isServiceConfigured
            ? String(localized: "Volta service is unavailable. Try again.")
            : String(localized: "Volta service is not configured for this build.")
        #endif
    }

    var api: VoltaAPI {
        apiOverride ?? VoltaAPI(baseURL: resolvedBaseURL)
    }

    private var resolvedBaseURL: URL {
        #if DEBUG
        return VoltaRuntimeConfiguration.debugURL(from: baseURLString)
        #else
        return VoltaRuntimeConfiguration.baseURL
        #endif
    }

    override init() {
        apiOverride = nil
        super.init()
        migrateRetiredDebugEndpointIfNeeded()
        locationManager.delegate = self
        applyDebugLocationOverride()
    }

    init(api: VoltaAPI) {
        apiOverride = api
        super.init()
        migrateRetiredDebugEndpointIfNeeded()
        locationManager.delegate = self
        applyDebugLocationOverride()
    }

    private func migrateRetiredDebugEndpointIfNeeded() {
        #if DEBUG
        let migrated = VoltaRuntimeConfiguration.migratedDebugBaseURLString(baseURLString)
        if migrated != baseURLString {
            baseURLString = migrated
        }
        #endif
    }

    private func applyDebugLocationOverride() {
        #if DEBUG
        if let override = Self.debugLocationOverride { locationState = override }
        #endif
    }

    #if DEBUG
    /// Hermetic UI-evidence seam. The entire parser and its argument string are removed from
    /// Release, so production location authorization always comes from CLLocationManager.
    private static var debugLocationOverride: LocationState? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let flag = arguments.firstIndex(of: "-VoltaUITestLocationState"),
              arguments.indices.contains(flag + 1)
        else { return nil }
        switch arguments[flag + 1].lowercased() {
        case "denied": return .denied
        case "restricted": return .restricted
        case "failed": return .failed
        default: return nil
        }
    }
    #endif

    var detailStation: Station? { selected }

    /// Detail views retain their safe routing/source context when a refreshed response no
    /// longer contains the row, but all live/Go-now claims are immediately demoted.
    func detailStation(fallback: Station) -> Station {
        if let selected, selected.id == fallback.id { return selected }
        if let current = (stations + leads).first(where: { $0.id == fallback.id }) { return current }
        return fallback.withTrust(.unavailable)
    }

    // MARK: - Rows the UI shows

    /// The product surface = verified live stations PLUS the source-backed free-charger leads
    /// (Feníe, Lidl, OSM, Barcelona Endolla, …) that are the whole reason a "free chargers" app
    /// exists. Leads enter the LIST clearly marked "Not live" (trustKind == .lead) and earn a MAP
    /// PIN only when `isMapLocationVerified` passes — they are never presented as live or Go-now,
    /// and `isRouteReady` (route/celebration gate) still excludes every lead. Restoring lead
    /// visibility (WS-1, 2026-07-18 QA audit): the prior "verified stations only" surface hid the
    /// entire free-charger database on iOS, so the national overview rendered empty.
    var filteredRows: [Station] {
        var rows = stations.filter { !$0.isLead } + leads
        if freeOnly { rows = rows.filter { $0.isFree == true || $0.priceMin == 0 } }
        if maxPrice < 4.99 {
            rows = rows.filter { station in
                guard let price = station.priceMin ?? (station.isFree == true ? 0 : nil) else { return false }
                return price <= maxPrice
            }
        }
        if !connectorTypes.isEmpty {
            rows = rows.filter { station in
                let labels = (station.connectorLabels ?? []) + (station.connectors ?? []).flatMap { [$0.type, $0.label].compactMap { $0 } }
                return labels.contains { label in
                    connectorTypes.contains { ConnectorKind.matches(label, selection: $0) }
                }
            }
        }
        if minimumPowerKW > 0 {
            rows = rows.filter { station in
                let connectorMaximum = station.connectors?.compactMap(\.powerKw).max() ?? 0
                return max(station.maxPowerKw ?? 0, connectorMaximum) >= minimumPowerKW
            }
        }
        if showingSavedOnly {
            let saved = Set(favoriteIDs)
            rows = rows.filter { saved.contains($0.id) }
        }
        return rows
    }

    /// The map set: only rows whose location/price contract is verified enough
    /// to make a pin. Directory/community leads stay in the list until verified.
    var mapRows: [Station] {
        filteredRows.filter(\.isMapLocationVerified)
    }

    /// The list = what's in the current viewport, best first.
    var listRows: [Station] {
        let bbox = visibleRegion.bbox
        return filteredRows
            .filter { bbox.contains($0) }
            .sorted { score($0) > score($1) }
    }

    private func score(_ st: Station) -> Double {
        var s = 0.0
        if !st.isLead { s += 100 }
        if st.canShowLiveAvailability, (st.available ?? 0) > 0 { s += 40 }
        if st.isFree == true { s += 20 }
        // Paid and unpriced chargers follow every free/cheap option within the 60 km band.
        if st.decisionKind == .lead { s -= 60 }
        if st.isRouteReady { s += 30 }
        if let d = st.distanceKm { s -= min(d, 60) }
        return s
    }

    var liveReadyCount: Int { stations.filter(\.isRouteReady).count }

    func focus(_ station: Station, centerMap: Bool = false) {
        focusedStationID = station.id
        selected = nil
        guard centerMap else { return }
        let region = MKCoordinateRegion(center: station.coordinate, span: visibleRegion.span)
        visibleRegion = region
        camera = .region(region)
    }

    func clearFocus() {
        focusedStationID = nil
    }

    /// Opens a station from the map or the list. The map moves only when the charger is
    /// outside the visible part of the map (`visibleRegion` excludes the area under the
    /// sheet), and `moveMap` is false when the full-height sheet hides the map anyway.
    func select(_ station: Station, moveMap: Bool) {
        focusedStationID = station.id
        selected = station
        guard moveMap, !visibleRegion.bbox.contains(station) else { return }
        let next = MKCoordinateRegion(center: station.coordinate, span: visibleRegion.span)
        visibleRegion = next
        if UIAccessibility.isReduceMotionEnabled {
            camera = .region(next)
        } else {
            withAnimation(VoltaMotion.spatial) { camera = .region(next) }
        }
    }

    var focusedStation: Station? {
        guard let id = focusedStationID else { return nil }
        return (stations + leads).first(where: { $0.id == id })
    }

    func dismissRouteMoment() {
        routeMomentTask?.cancel()
        routeMoment = nil
    }

    func openRouteMomentCard() {
        guard let moment = routeMoment else { return }
        if let station = (stations + leads).first(where: { $0.id == moment.stationID }) {
            selected = station
        }
        dismissRouteMoment()
    }

    // MARK: - Fetching

    func start() {
        Task { await loadOverview() }
        #if DEBUG
        // UI tests can pin an adverse authorization state. Do not let an already-authorized
        // simulator immediately replace that state with a real location request.
        if Self.debugLocationOverride != nil { return }
        #endif
        // Approved UX: near-me first when permission already exists, else the national
        // overview invites locate/search (same default as the web client).
        if locationManager.authorizationStatus == .authorizedWhenInUse
            || locationManager.authorizationStatus == .authorizedAlways {
            locate()
        }
    }

    /// Ends the cold-launch signpost interval exactly once, the first time real
    /// content is ready to render. See the "Cold-launch instrumentation" MARK above
    /// for what this measures and why `loadOverview()`'s success is the chosen point.
    private func recordFirstMeaningfulContentIfNeeded() {
        guard !didRecordFirstMeaningfulContent else { return }
        didRecordFirstMeaningfulContent = true
        AppState.launchSignposter.endInterval("ColdLaunchToFirstContent", launchSignpostState)
        #if DEBUG
        let elapsed = ContinuousClock.now - launchStart
        let ms = Double(elapsed.components.seconds) * 1000 + Double(elapsed.components.attoseconds) / 1e15
        let msText = String(format: "%.1f", ms)
        // `privacy: .public` is required -- os.Logger redacts interpolated values by
        // default, which would otherwise show `<private>` instead of the number in
        // `log stream`/Console. There is no PII here (a duration), so public is correct.
        Logger(subsystem: "tech.arsik.volta", category: "ColdLaunch")
            .notice("first meaningful content in \(msText, privacy: .public) ms")
        #endif
    }

    func loadOverview() async {
        let requestID = UUID()
        overviewRequestID = requestID
        overviewState = .loading
        guard isServiceConfigured else {
            if mode == .overview {
                warning = serviceFailureMessage
                warningIsServerUnreachable = true
            }
            overviewState = .failed
            return
        }
        do {
            let resp = try await api.overview(maxPrice: maxPrice, bbox: visibleRegion.bbox)
            guard overviewRequestID == requestID, !Task.isCancelled else { return }
            if mode == .overview {
                leads = resp.leads
                resultsTruncated = resp.meta?.truncated == true
                stations = resp.stations // [] by contract
                previousRouteReadyCount = 0
                dismissRouteMoment()
                warning = resp.warning
                warningIsServerUnreachable = false
                announce(String(localized: "Known chargers loaded."))
            } else if (liveState == .loading || liveState == .failed),
                      stations.isEmpty,
                      leads.isEmpty {
                // The always-started overview can resolve while live is pending or after it
                // fails. In either ordering, recover the honest, non-live fallback rows without
                // touching live's warning, stations, or route notice. A successful live response
                // already owns its presentation and never satisfies this branch.
                leads = resp.leads
                resultsTruncated = resp.meta?.truncated == true
            }
            overviewState = .loaded
            recordFirstMeaningfulContentIfNeeded()
        } catch {
            guard overviewRequestID == requestID, !Task.isCancelled else { return }
            if mode == .overview {
                warning = serviceFailureMessage
                warningIsServerUnreachable = true
                announce(String(localized: "Known chargers could not be loaded. Retry or open Settings."))
            }
            overviewState = .failed
        }
    }

    #if DEBUG
    /// Re-check the backend after the base URL changed in Settings.
    func baseURLChanged() {
        // Rows, selection and pending responses belong to the previous service.
        // Invalidate them together before the replacement overview starts.
        overviewRequestID = UUID()
        liveTask?.cancel()
        refreshTimer?.invalidate()
        clearSearch()
        selected = nil
        focusedStationID = nil
        warning = nil
        warningIsServerUnreachable = false
        stations = []
        leads = []
        resultsTruncated = false
        dismissRouteMoment()
        previousRouteReadyCount = 0
        liveState = .idle
        liveQuality = .overview
        liveCache = nil
        overviewState = .idle
        mode = .overview
        Task { await loadOverview() }
    }

    /// One-shot connectivity probe for the Settings sheet.
    func testConnection() async -> String {
        do {
            _ = try await api.overview(maxPrice: maxPrice, bbox: visibleRegion.bbox)
            return String(localized: "✓ Connected — server and data reachable")
        } catch {
            return String(localized: "✗ Unreachable: \(error.localizedDescription)")
        }
    }
    #endif

    /// Enter/refresh live mode for the current viewport (debounced by the map's onEnd events).
    func fetchLive(reason: String = "") {
        // Continental/world views use stored evidence; live providers have a local budget.
        guard visibleRegion.bbox.supportsLiveLookup else {
            liveTask?.cancel()
            refreshTimer?.invalidate()
            mode = .overview
            stations = []
            previousRouteReadyCount = 0
            dismissRouteMoment()
            liveState = .idle
            liveQuality = .overview
            liveCache = nil
            Task { await loadOverview() }
            return
        }
        mode = .live
        liveTask?.cancel()
        let qualityBeforeRequest = liveQuality
        guard isServiceConfigured else {
            stations = []
            previousRouteReadyCount = 0
            dismissRouteMoment()
            warning = serviceFailureMessage
            warningIsServerUnreachable = true
            liveState = .failed
            liveQuality = .unavailable
            liveCache = nil
            reconcileSelected(with: leads)
            announce(String(localized: "Live status is unavailable. Known chargers remain for source checking."))
            return
        }
        let bbox = visibleRegion.bbox
        let coords = userCoordinate
        liveState = .loading
        liveQuality = .loading
        liveTask = Task {
            do {
                let resp = try await api.live(bbox: bbox, coords: coords, maxPrice: maxPrice, availableOnly: false)
                guard !Task.isCancelled else { return }

                let quality = Self.liveQuality(for: resp)
                let currentStations = quality == .unavailable
                    ? []
                    : Self.stationsWithResponseTrust(from: resp, quality: quality)

                stations = currentStations
                leads = resp.leads
                resultsTruncated = resp.meta?.truncated == true
                liveCache = resp.cache
                warning = resp.warning ?? resp.meta?.coverage?.warning
                warningIsServerUnreachable = false
                liveQuality = quality
                liveState = quality == .unavailable ? .failed : .ok
                reconcileSelected(with: currentStations + resp.leads)
                reconcileRouteReadyMoment(from: currentStations)
                if reason != "auto" || qualityBeforeRequest != quality {
                    announce(liveAnnouncement(for: quality))
                }
            } catch is CancellationError {
            } catch {
                guard !Task.isCancelled else { return }
                // FAIL-CLOSED: drop live rows entirely; leads stay (honestly "Not live").
                stations = []
                previousRouteReadyCount = 0
                dismissRouteMoment()
                liveState = .failed
                liveQuality = .unavailable
                liveCache = nil
                warning = serviceFailureMessage
                warningIsServerUnreachable = true
                reconcileSelected(with: leads)
                if reason != "auto" || qualityBeforeRequest != .unavailable {
                    announce(String(localized: "Live status is unavailable. Known chargers remain for source checking."))
                }
            }
        }
        scheduleAutoRefresh()
    }

    private static func liveQuality(for response: ChargersResponse) -> LiveQuality {
        if response.cache?.liveSupported == false || response.meta?.coverage?.mode == "directory" { return .overview }
        if response.cache?.mixedLive == true { return .mixed }
        if response.cache?.freshLiveAuthority == true {
            if response.meta?.coverage?.mode == "degraded" { return .degraded }
            return .fresh
        }
        if response.cache?.liveFailed == true { return .unavailable }
        if response.cache?.stale == true { return .stale }
        if response.meta?.coverage?.mode == "degraded" { return .degraded }
        return .fresh
    }

    private static func stationsWithResponseTrust(
        from response: ChargersResponse,
        quality: LiveQuality
    ) -> [Station] {
        switch quality {
        case .fresh:
            return response.stations.map { $0.withTrust(.fresh) }
        case .degraded:
            // Coverage is an aggregate description of the visible area, not evidence that
            // every individual live row is bad. Keep the warning in Settings, but preserve a
            // station that independently proves live status + a Go decision. Rows without
            // that complete contract remain visible with demoted trust; they cannot gain
            // route-ready authority or present connector counts as current availability.
            return response.stations.map { station in
                if station.cacheStale == true { return station.withTrust(.stale) }
                let level = station.verification?.level
                let hasLiveContract = station.verification?.liveVerified == true
                    && (level == nil || level?.isEmpty == true || level == "live")
                return station.withTrust(hasLiveContract ? .fresh : .degraded)
            }
        case .mixed, .stale:
            let hasExplicitStaleRows = response.stations.contains { $0.cacheStale == true }
            return response.stations.map { station in
                if station.cacheStale == true { return station.withTrust(.stale) }
                // Older servers did not emit cache_stale per row. If no row is marked at all,
                // preserve fail-closed behavior by treating the whole stale payload as stale.
                guard hasExplicitStaleRows else { return station.withTrust(.stale) }
                let level = station.verification?.level
                let hasLiveContract = station.verification?.liveVerified == true
                    && (level == nil || level?.isEmpty == true || level == "live")
                return station.withTrust(hasLiveContract ? .fresh : .degraded)
            }
        case .unavailable, .overview, .loading:
            return response.stations.map { $0.withTrust(.unavailable) }
        }
    }

    private func liveAnnouncement(for quality: LiveQuality) -> String {
        switch quality {
        case .fresh: String(localized: "Live charger status refreshed.")
        case .degraded: String(localized: "Live coverage is limited. Check sources before routing.")
        case .mixed: String(localized: "Fresh live status is available; cached rows remain marked stale.")
        case .stale: String(localized: "Live status is stale. Refresh before routing.")
        case .unavailable: String(localized: "Live status is unavailable. Known chargers remain for source checking.")
        case .overview, .loading: ""
        }
    }

    private func reconcileSelected(with currentRows: [Station]) {
        guard let prior = selected else { return }
        if let current = currentRows.first(where: { $0.id == prior.id }) {
            selected = current
        } else {
            selected = prior.withTrust(.unavailable)
        }
    }

    private func reconcileRouteReadyMoment(from rows: [Station]) {
        let routeReady = rows.filter(\.isRouteReady)

        // A notice is about one concrete station, not merely the aggregate ready count. If A
        // becomes stale while B remains fresh, retaining A's notice would offer a route to a
        // station that no longer passes the same honesty gate as the card underneath it.
        if let routeMoment,
           !routeReady.contains(where: { $0.id == routeMoment.stationID }) {
            dismissRouteMoment()
        }

        guard !routeReady.isEmpty else {
            previousRouteReadyCount = 0
            dismissRouteMoment()
            return
        }

        if !routeReady.isEmpty && previousRouteReadyCount == 0 {
            let first = routeReady[0]
            routeMoment = RouteMoment(stationID: first.id, stationName: first.name)
            routeMomentTask?.cancel()
            routeMomentTask = Task { [weak self, stationID = first.id] in
                try? await Task.sleep(for: .seconds(5))
                await MainActor.run {
                    guard let self, self.routeMoment?.stationID == stationID else { return }
                    self.routeMoment = nil
                }
            }
        }
        previousRouteReadyCount = routeReady.count
    }

    private func scheduleAutoRefresh() {
        refreshTimer?.invalidate()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.mode == .live, self.liveState != .loading else { return }
                self.fetchLive(reason: "auto")
            }
        }
    }

    private func refetch() {
        if mode == .live { fetchLive(reason: "filter") }
    }

    // MARK: - Saved chargers

    func isFavorite(_ station: Station) -> Bool {
        favoriteIDs.contains(station.id)
    }

    func toggleFavorite(_ station: Station) {
        var records = FavoritesStore.load()
        if let index = records.firstIndex(where: { $0.id == station.id }) {
            records.remove(at: index)
            announce(String(localized: "Removed from saved chargers."))
        } else {
            records.insert(FavoriteRecord(id: station.id, name: station.name, savedAt: Date()), at: 0)
            announce(String(localized: "Saved this charger on this iPhone."))
        }
        FavoritesStore.save(records)
        favoriteIDs = records.map(\.id)
    }

    var savedStationsInView: [Station] {
        let saved = Set(favoriteIDs)
        return (stations + leads).filter { saved.contains($0.id) }
    }

    // MARK: - Community (plan 013: "did charging actually work?")

    /// Submit a driver check-in, then refresh so the updated community count appears. Returns
    /// true on success. Community evidence is display + a fail-closed downgrade only — it never
    /// creates live status. Verdicts: charged / failed / occupied.
    func submitCheckin(station: Station, verdict: String) async -> Bool {
        guard isServiceConfigured else { return false }
        do {
            try await api.checkin(stationID: station.id, verdict: verdict)
            announce(String(localized: "Thanks — your check-in was recorded."))
            if mode == .live { fetchLive(reason: "checkin") } else { await loadOverview() }
            return true
        } catch {
            announce(String(localized: "Check-in could not be sent. Try again."))
            return false
        }
    }

    // MARK: - Search

    func searchChanged(_ text: String) {
        searchTask?.cancel()
        searchResults = []
        let query = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard query.count >= 3 else {
            searchState = .idle
            return
        }
        searchState = .loading
        guard isServiceConfigured else {
            searchState = .failed
            return
        }
        searchTask = Task {
            try? await Task.sleep(for: .milliseconds(300))
            guard !Task.isCancelled else { return }
            do {
                let results = try await api.geocode(query)
                guard !Task.isCancelled else { return }
                guard searchText.trimmingCharacters(in: .whitespacesAndNewlines) == query else { return }
                searchResults = results
                searchState = results.isEmpty ? .empty : .results
                announce(results.isEmpty
                    ? String(localized: "No matching places found.")
                    : String(localized: "\(results.count) places found."))
            } catch {
                guard !Task.isCancelled else { return }
                guard searchText.trimmingCharacters(in: .whitespacesAndNewlines) == query else { return }
                searchResults = []
                searchState = .failed
                announce(String(localized: "Place search is unavailable. Retry search."))
            }
        }
    }

    func retrySearch() {
        searchChanged(searchText)
    }

    func clearSearch() {
        searchTask?.cancel()
        searchText = ""
        searchResults = []
        searchState = .idle
    }

    func pick(_ result: GeocodeResult) {
        clearSearch()
        let region: MKCoordinateRegion
        if let bb = result.bbox {
            let bounds = BBox(south: bb.latitudeSw, west: bb.longitudeSw,
                              north: bb.latitudeNe, east: bb.longitudeNe)
            region = MKCoordinateRegion(
                center: .init(latitude: (bounds.south + bounds.north) / 2,
                              longitude: bounds.centerLongitude),
                span: .init(latitudeDelta: min(180, max(0.04, (bounds.north - bounds.south) * 1.2)),
                            longitudeDelta: min(360, max(0.04, bounds.longitudeSpan * 1.2)))
            )
        } else {
            region = MKCoordinateRegion(center: .init(latitude: result.lat, longitude: result.lon),
                                        span: .init(latitudeDelta: 0.12, longitudeDelta: 0.12))
        }
        visibleRegion = region
        withAnimation(cameraAnimation) { camera = .region(region) }
        fetchLive(reason: "search")
    }

    /// From an out-of-service charger: close its detail and show the chargers around it, so
    /// the driver's next step is an alternative rather than directions to a dead charger.
    func showChargersNear(_ station: Station) {
        selected = nil
        focusedStationID = nil
        let region = MKCoordinateRegion(center: station.coordinate,
                                        span: .init(latitudeDelta: 0.08, longitudeDelta: 0.08))
        visibleRegion = region
        withAnimation(cameraAnimation) { camera = .region(region) }
        fetchLive(reason: "nearby-alternatives")
    }

    // MARK: - Location

    /// Pure authorization callback reducer. `locate()` directly requests a location when
    /// authorization already exists, so a subsequent authorized callback must not repeat that
    /// request. A grant from the system prompt, or a denied/restricted → authorized return from
    /// Settings, does need exactly one request and clears the prior recovery state.
    static func authorizationTransition(
        status: CLAuthorizationStatus,
        locationState: LocationState,
        locating: Bool,
        awaitingAuthorization: Bool
    ) -> LocationAuthorizationTransition {
        switch status {
        case .authorizedAlways, .authorizedWhenInUse:
            let recoveringFromSettings = locationState == .denied || locationState == .restricted
            let shouldRequestLocation = awaitingAuthorization || recoveringFromSettings
            return LocationAuthorizationTransition(
                locationState: shouldRequestLocation ? .requesting : locationState,
                locating: shouldRequestLocation ? true : locating,
                awaitingAuthorization: false,
                shouldRequestLocation: shouldRequestLocation
            )
        case .notDetermined:
            return LocationAuthorizationTransition(
                locationState: locationState,
                locating: locating,
                awaitingAuthorization: awaitingAuthorization,
                shouldRequestLocation: false
            )
        case .denied:
            return LocationAuthorizationTransition(
                locationState: .denied,
                locating: false,
                awaitingAuthorization: false,
                shouldRequestLocation: false
            )
        case .restricted:
            return LocationAuthorizationTransition(
                locationState: .restricted,
                locating: false,
                awaitingAuthorization: false,
                shouldRequestLocation: false
            )
        @unknown default:
            return LocationAuthorizationTransition(
                locationState: .failed,
                locating: false,
                awaitingAuthorization: false,
                shouldRequestLocation: false
            )
        }
    }

    /// Core Location can deliver a terminal error after a usable one-shot coordinate.
    /// Preserve the resolved location instead of resurfacing a contradictory recovery panel.
    static func locationFailureTransition(hasUsableCoordinate: Bool) -> LocationState {
        hasUsableCoordinate ? .located : .failed
    }

    func locate() {
        #if DEBUG
        if let override = Self.debugLocationOverride {
            locating = false
            awaitingLocationAuthorization = false
            locationState = override
            announce(locationRecoveryAnnouncement(for: override))
            return
        }
        #endif

        locating = true
        locationState = .requesting
        switch locationManager.authorizationStatus {
        case .authorizedAlways, .authorizedWhenInUse:
            awaitingLocationAuthorization = false
            locationManager.requestLocation()
        case .notDetermined:
            awaitingLocationAuthorization = true
            locationManager.requestWhenInUseAuthorization()
        case .denied:
            locating = false
            awaitingLocationAuthorization = false
            locationState = .denied
            announce(locationRecoveryAnnouncement(for: .denied))
        case .restricted:
            locating = false
            awaitingLocationAuthorization = false
            locationState = .restricted
            announce(locationRecoveryAnnouncement(for: .restricted))
        @unknown default:
            locating = false
            awaitingLocationAuthorization = false
            locationState = .failed
            announce(locationRecoveryAnnouncement(for: .failed))
        }
    }

    private func locationRecoveryAnnouncement(for state: LocationState) -> String {
        switch state {
        case .denied: String(localized: "Location access is off. Search a city or allow location in Settings.")
        case .restricted: String(localized: "Location access is restricted. Search a city or review Settings.")
        case .failed: String(localized: "Your location could not be read. Search a city or review Settings.")
        case .idle, .requesting, .located: ""
        }
    }
}

extension AppState: CLLocationManagerDelegate {
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        Task { @MainActor in
            #if DEBUG
            if let override = Self.debugLocationOverride {
                self.locationState = override
                self.locating = false
                self.awaitingLocationAuthorization = false
                return
            }
            #endif
            let transition = Self.authorizationTransition(
                status: status,
                locationState: self.locationState,
                locating: self.locating,
                awaitingAuthorization: self.awaitingLocationAuthorization
            )
            self.locationState = transition.locationState
            self.locating = transition.locating
            self.awaitingLocationAuthorization = transition.awaitingAuthorization

            if transition.shouldRequestLocation {
                manager.requestLocation()
            } else if transition.locationState == .denied
                        || transition.locationState == .restricted
                        || transition.locationState == .failed {
                self.announce(self.locationRecoveryAnnouncement(for: transition.locationState))
            }
        }
    }

    nonisolated func locationManager(_: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last, CLLocationCoordinate2DIsValid(loc.coordinate) else { return }
        Task { @MainActor in
            #if DEBUG
            if let override = Self.debugLocationOverride {
                self.locationState = override
                self.locating = false
                self.awaitingLocationAuthorization = false
                return
            }
            #endif
            self.locating = false
            self.awaitingLocationAuthorization = false
            self.locationState = .located
            self.userCoordinate = loc.coordinate
            let region = MKCoordinateRegion(center: loc.coordinate,
                                            latitudinalMeters: 1_200, longitudinalMeters: 1_200)
            self.visibleRegion = region
            withAnimation(self.cameraAnimation) { self.camera = .region(region) }
            self.fetchLive(reason: "locate")
            self.announce(String(localized: "Location found. Loading nearby chargers."))
        }
    }

    nonisolated func locationManager(_: CLLocationManager, didFailWithError _: Error) {
        Task { @MainActor in
            #if DEBUG
            if let override = Self.debugLocationOverride {
                self.locationState = override
                self.locating = false
                self.awaitingLocationAuthorization = false
                return
            }
            #endif
            self.locating = false
            self.awaitingLocationAuthorization = false
            let nextState = Self.locationFailureTransition(
                hasUsableCoordinate: self.userCoordinate != nil
            )
            self.locationState = nextState
            if nextState == .failed {
                self.announce(String(localized: "Your location could not be read. Search a city or review Settings."))
            }
        }
    }
}


extension MKCoordinateRegion {
    var bbox: BBox {
        BBox(
            south: center.latitude - span.latitudeDelta / 2,
            west: center.longitude - span.longitudeDelta / 2,
            north: center.latitude + span.latitudeDelta / 2,
            east: center.longitude + span.longitudeDelta / 2
        )
    }
}
