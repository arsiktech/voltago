import Foundation

/// Last-valid charger evidence kept on this iPhone so saved chargers and the last explored
/// area stay readable offline (VG-17). It stores the server's own response bytes and decodes
/// them with the same decoder, so cached rows cannot drift from the live contract.
///
/// Honesty rules: every cached row is marked `cacheStale`, so it is never live, never
/// route-ready and never "available now"; the app says when it was saved. Storage is finite
/// (one area of at most `maxAreaBytes`, at most `maxSavedStations` saved chargers), written
/// atomically, excluded from backup, and discarded on any schema or decoding mismatch.
/// No map tiles or photos are stored.
enum OfflineStationCache {
    static let schemaVersion = 1
    static let maxAreaBytes = 4_000_000
    static let maxSavedStations = 200
    /// Tests point this at a temporary folder so they never touch the owner's cache.
    static var directoryOverride: URL?

    private struct Envelope: Codable {
        var schema: Int
        var savedAt: Date
        var payload: Data
    }

    struct Snapshot {
        var savedAt: Date
        var stations: [Station]
    }

    // MARK: Writing

    /// Keeps the latest successful area response. An oversized response is not stored.
    static func storeArea(_ responseData: Data, at date: Date = Date()) {
        guard responseData.count <= maxAreaBytes else { return }
        write(Envelope(schema: schemaVersion, savedAt: date, payload: responseData), to: "area.json")
    }

    /// Adds or refreshes saved chargers present in a response and drops ones no longer saved.
    static func storeSaved(from responseData: Data, favoriteIDs: Set<String>, at date: Date = Date()) {
        var rows = savedRows()
        rows = rows.filter { favoriteIDs.contains($0.key) }
        if let object = try? JSONSerialization.jsonObject(with: responseData) as? [String: Any] {
            for key in ["leads", "stations"] {
                for case let row as [String: Any] in object[key] as? [Any] ?? [] {
                    if let id = row["id"] as? String, favoriteIDs.contains(id) { rows[id] = row }
                }
            }
        }
        let kept = rows.sorted { $0.key < $1.key }.prefix(maxSavedStations).map(\.value)
        guard let payload = try? JSONSerialization.data(withJSONObject: kept) else { return }
        write(Envelope(schema: schemaVersion, savedAt: date, payload: payload), to: "saved.json")
    }

    static func clear() {
        guard let directory else { return }
        try? FileManager.default.removeItem(at: directory)
    }

    // MARK: Reading

    /// The last explored area, every row marked stale. Nil when absent or unreadable.
    static func loadArea(decoder: JSONDecoder) -> Snapshot? {
        guard let envelope = read("area.json"),
              let response = try? decoder.decode(ChargersResponse.self, from: envelope.payload) else {
            discard("area.json")
            return nil
        }
        return Snapshot(savedAt: envelope.savedAt, stations: (response.leads + response.stations).map(markStale))
    }

    /// Saved chargers, every row marked stale. Empty when absent or unreadable.
    static func loadSaved(decoder: JSONDecoder) -> Snapshot? {
        guard let envelope = read("saved.json"),
              let stations = try? decoder.decode([Station].self, from: envelope.payload) else {
            discard("saved.json")
            return nil
        }
        return Snapshot(savedAt: envelope.savedAt, stations: stations.map(markStale))
    }

    // MARK: Private

    private static func markStale(_ station: Station) -> Station {
        var cached = station
        cached.cacheStale = true
        return cached
    }

    private static var directory: URL? {
        if let directoryOverride { return directoryOverride }
        return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("OfflineStations", isDirectory: true)
    }

    private static func savedRows() -> [String: [String: Any]] {
        guard let envelope = read("saved.json"),
              let rows = try? JSONSerialization.jsonObject(with: envelope.payload) as? [[String: Any]] else { return [:] }
        var byID: [String: [String: Any]] = [:]
        for row in rows { if let id = row["id"] as? String { byID[id] = row } }
        return byID
    }

    private static func read(_ name: String) -> Envelope? {
        guard let url = directory?.appendingPathComponent(name),
              let data = try? Data(contentsOf: url) else { return nil }
        guard let envelope = try? PropertyListDecoder().decode(Envelope.self, from: data),
              envelope.schema == schemaVersion else {
            discard(name)
            return nil
        }
        return envelope
    }

    private static func write(_ envelope: Envelope, to name: String) {
        guard let directory, let data = try? PropertyListEncoder().encode(envelope) else { return }
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            var resources = URLResourceValues()
            resources.isExcludedFromBackup = true
            var folder = directory
            try folder.setResourceValues(resources)
            var url = directory.appendingPathComponent(name)
            try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            try url.setResourceValues(resources)
        } catch {
            // A failed write keeps the previous last-valid copy; offline use is best effort.
        }
    }

    private static func discard(_ name: String) {
        guard let url = directory?.appendingPathComponent(name) else { return }
        try? FileManager.default.removeItem(at: url)
    }
}
