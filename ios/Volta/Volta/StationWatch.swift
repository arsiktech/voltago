import Foundation
import SwiftUI
import UIKit
import UserNotifications

// Availability watches (docs/spec-availability-alerts.md, VG-19/VG-20). A watch asks the
// server to send one notification if a compatible connector at a live-status charger turns
// free. It never reserves a connector, and the app re-reads current facts when opened.

struct WatchConfig: Decodable, Equatable {
    var enabled: Bool
    var pushConfigured: Bool
    var eligibleFeeds: [String]
    var defaultMinutes: Int
    var maxMinutes: Int
}

struct WatchCreateRequest: Equatable {
    var stationID: String
    var lat: Double
    var lon: Double
    var deviceToken: String
    var connectorTypes: [String]
    var durationMinutes: Int

    var jsonObject: [String: Any] {
        var body: [String: Any] = ["station_id": stationID, "lat": lat, "lon": lon,
                                   "device_token": deviceToken, "duration_minutes": durationMinutes]
        if !connectorTypes.isEmpty { body["connector_types"] = connectorTypes }
        return body
    }
}

struct WatchRecord: Decodable, Equatable {
    var id: String
    var stationId: String
    var stationName: String?
    var active: Bool
    var expiresAt: String?
    var endReason: String?
    var delivery: String?
    var pushConfigured: Bool?
    var freeNow: [String]?
}

struct WatchEnvelope: Decodable { var watch: WatchRecord }
struct WatchErrorBody: Decodable { var warning: String? }

struct WatchRequestError: Error, Equatable {
    var status: Int
    var reason: String?
}

/// What the app remembers about a watch it started, so a tapped notification can find the
/// charger again without trusting the notification text.
struct LocalWatch: Codable, Equatable {
    var id: String
    var stationID: String
    var name: String
    var lat: Double
    var lon: Double
    var expiresAt: Date?
}

extension Station {
    /// Only fresh rows from a feed the server can watch, with stable connector IDs.
    func isWatchable(feeds: [String]) -> Bool {
        guard canShowLiveAvailability, cacheStale != true, !isLead,
              liveStatus?.valid == true, let feed = liveStatus?.feed, feeds.contains(feed) else { return false }
        return (connectors ?? []).contains { !($0.connectorId ?? "").isEmpty }
    }
}

/// Hands the APNs token from the app delegate to whoever is waiting for it.
@MainActor
final class PushTokenBroker {
    static let shared = PushTokenBroker()
    private var waiters: [CheckedContinuation<String?, Never>] = []
    private(set) var token: String?

    func requestToken() async -> String? {
        #if DEBUG
        if let override = Self.uiTestToken { return override }
        #endif
        if let token { return token }
        return await withCheckedContinuation { continuation in
            waiters.append(continuation)
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    func deliver(_ data: Data?) {
        token = data.map { $0.map { String(format: "%02x", $0) }.joined() }
        let pending = waiters
        waiters.removeAll()
        pending.forEach { $0.resume(returning: token) }
    }

    #if DEBUG
    static var uiTestToken: String? {
        let args = ProcessInfo.processInfo.arguments
        guard let index = args.firstIndex(of: "-VoltaUITestPushToken"), args.indices.contains(index + 1) else { return nil }
        return args[index + 1]
    }
    #endif
}

final class VoltaAppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Task { @MainActor in PushTokenBroker.shared.deliver(deviceToken) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        // Without the Push Notifications capability this always fails; the watch UI explains it.
        Task { @MainActor in PushTokenBroker.shared.deliver(nil) }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        guard let volta = info["volta"] as? [String: Any], let watchID = volta["watch_id"] as? String else { return }
        await MainActor.run { NotificationCenter.default.post(name: .voltaWatchAlertOpened, object: watchID) }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }
}

extension Notification.Name {
    static let voltaWatchAlertOpened = Notification.Name("voltaWatchAlertOpened")
}

@MainActor
final class WatchService: ObservableObject {
    enum Phase: Equatable {
        case idle
        case working
        case watching(LocalWatch)
        case message(String)
    }

    @Published private(set) var config: WatchConfig?
    @Published private(set) var phases: [String: Phase] = [:]
    private var watches: [String: LocalWatch]
    private let defaults: UserDefaults
    private static let storageKey = "voltaActiveWatches"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let stored = (defaults.data(forKey: Self.storageKey)).flatMap { try? JSONDecoder().decode([String: LocalWatch].self, from: $0) }
        watches = stored ?? [:]
        for (station, watch) in watches { phases[station] = .watching(watch) }
    }

    var feeds: [String] { config?.eligibleFeeds ?? [] }

    func canOffer(_ station: Station) -> Bool {
        config?.enabled == true && station.isWatchable(feeds: feeds)
    }

    func phase(for stationID: String) -> Phase { phases[stationID] ?? .idle }

    func watch(id: String) -> LocalWatch? { watches.values.first { $0.id == id } }

    /// Watch calls use their own connection pool so they can never queue behind (or delay)
    /// the map's charger requests to the same host.
    private static let session = URLSession(configuration: .ephemeral)

    private func client(_ api: VoltaAPI) -> VoltaAPI { VoltaAPI(baseURL: api.baseURL, session: Self.session) }

    func loadConfig(api: VoltaAPI) async {
        config = try? await client(api).watchConfig()
    }

    #if DEBUG
    func setConfigForTesting(_ config: WatchConfig) { self.config = config }
    #endif

    func start(_ station: Station, connectorTypes: [String], api: VoltaAPI,
               authorize: () async -> Bool = WatchService.requestAuthorization) async {
        guard canOffer(station), let config else { return }
        phases[station.id] = .working
        guard await authorize() else {
            phases[station.id] = .message(String(localized: "Notifications are off for Volta Go. Turn them on in Settings to get charger alerts."))
            return
        }
        guard let token = await PushTokenBroker.shared.requestToken() else {
            phases[station.id] = .message(String(localized: "This build can't receive alerts yet. Push notifications are not set up."))
            return
        }
        do {
            let record = try await client(api).createWatch(WatchCreateRequest(
                stationID: station.id, lat: station.lat, lon: station.lon, deviceToken: token,
                connectorTypes: connectorTypes, durationMinutes: config.defaultMinutes))
            let local = LocalWatch(id: record.id, stationID: station.id, name: station.stationTitle,
                                   lat: station.lat, lon: station.lon,
                                   expiresAt: record.expiresAt.flatMap(Self.parseDate))
            watches[station.id] = local
            persist()
            phases[station.id] = .watching(local)
        } catch let error as WatchRequestError {
            phases[station.id] = .message(error.reason ?? String(localized: "Couldn't start the watch. Try again."))
        } catch {
            phases[station.id] = .message(String(localized: "Couldn't reach Volta Go. Try again."))
        }
    }

    func stop(stationID: String, api: VoltaAPI) async {
        guard let local = watches[stationID] else { return }
        phases[stationID] = .working
        _ = try? await client(api).cancelWatch(id: local.id)
        watches[stationID] = nil
        persist()
        phases[stationID] = .idle
    }

    /// Drops watches the server has ended (alerted, expired, outage, cancelled elsewhere).
    func refresh(api: VoltaAPI) async {
        guard !watches.isEmpty else { return }
        for (station, local) in watches {
            guard let record = try? await client(api).watch(id: local.id) else { continue }
            if !record.active {
                watches[station] = nil
                phases[station] = .message(Self.endText(record))
            }
        }
        persist()
    }

    static func endText(_ record: WatchRecord) -> String {
        switch record.endReason {
        case "alerted": String(localized: "A connector showed free. Check the current status before driving.")
        case "expired": String(localized: "The watch ended without a free connector.")
        case "outage": String(localized: "Watch stopped: live status unavailable.")
        default: String(localized: "The watch has ended.")
        }
    }

    private func persist() {
        defaults.set(try? JSONEncoder().encode(watches), forKey: Self.storageKey)
    }

    static func requestAuthorization() async -> Bool {
        #if DEBUG
        if PushTokenBroker.uiTestToken != nil { return true }
        #endif
        return (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])) ?? false
    }

    static func parseDate(_ value: String) -> Date? {
        ISO8601DateFormatter().date(from: value)
    }
}

/// The detail-screen block: offered only on watchable chargers when the server enables watches.
struct StationWatchSection: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var watches: WatchService
    let station: Station

    var body: some View {
        if watches.canOffer(station) || watches.phase(for: station.id) != .idle {
            VStack(alignment: .leading, spacing: 6) {
                switch watches.phase(for: station.id) {
                case .idle:
                    Button {
                        Task { await watches.start(station, connectorTypes: state.watchConnectorTypes, api: state.api) }
                    } label: {
                        Label(String(localized: "Alert me when a connector is free"), systemImage: "bell")
                    }
                    .accessibilityIdentifier("watchChargerButton")
                case .working:
                    ProgressView().accessibilityIdentifier("watchWorking")
                case .watching(let local):
                    Label(watchingText(local), systemImage: "bell.badge")
                        .foregroundStyle(Theme.ink)
                        .accessibilityIdentifier("watchActiveNote")
                    Button(String(localized: "Stop watching")) {
                        Task { await watches.stop(stationID: station.id, api: state.api) }
                    }
                    .accessibilityIdentifier("stopWatchButton")
                case .message(let text):
                    Text(text).foregroundStyle(.secondary).accessibilityIdentifier("watchMessage")
                }
                Text(String(localized: "An alert never reserves a connector. It may be taken before you arrive."))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.borderless)
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func watchingText(_ local: LocalWatch) -> String {
        guard let expires = local.expiresAt else { return String(localized: "Watching this charger.") }
        return String(localized: "Watching until \(expires.formatted(date: .omitted, time: .shortened)).")
    }
}
