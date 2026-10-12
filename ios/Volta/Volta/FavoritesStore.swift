import Foundation

/// On-device saved chargers. IDs only — never invent a station that the service no longer returns.
struct FavoriteRecord: Codable, Equatable, Hashable, Identifiable {
    var id: String
    var name: String
    var savedAt: Date
}

enum FavoritesStore {
    static let defaultsKey = "voltaFavoriteStations"
    /// Tests replace this with an ephemeral suite so they never touch the owner's defaults.
    static var defaults: UserDefaults = .standard

    static func load() -> [FavoriteRecord] {
        guard let data = defaults.data(forKey: defaultsKey) else { return [] }
        return (try? JSONDecoder().decode([FavoriteRecord].self, from: data)) ?? []
    }

    static func save(_ records: [FavoriteRecord]) {
        defaults.set(try? JSONEncoder().encode(records), forKey: defaultsKey)
    }
}
