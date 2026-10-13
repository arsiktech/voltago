import CryptoKit
import Foundation

/// Nonpersonal, revision-bound catalogue delivered with charger rows. Favorites stay local.
struct ChargerReferences: Decodable {
    struct Entry: Decodable {
        var key: String
        var state: String
        var canonicalId: String?
    }
    var version: Int
    var revision: String
    var entries: [Entry]

    static func key(_ id: String) -> String {
        SHA256.hash(data: Data(("volta-reference-v1\0" + id).utf8))
            .map { String(format: "%02x", $0) }.joined()
    }

    private var validatedEntries: [String: Entry]? {
        guard version == 1, revision.count == 64,
              revision.allSatisfy({ "0123456789abcdef".contains($0) }), entries.count <= 100000 else { return nil }
        var result: [String: Entry] = [:]
        for entry in entries {
            guard entry.key.count == 64, entry.key.allSatisfy({ "0123456789abcdef".contains($0) }),
                  result[entry.key] == nil, ["alias", "withheld", "unresolved"].contains(entry.state),
                  entry.state == "alias" ? !(entry.canonicalId ?? "").isEmpty : entry.canonicalId == nil else { return nil }
            result[entry.key] = entry
        }
        return result
    }

    func state(for id: String) -> String {
        guard let index = validatedEntries else { return "unavailable" }
        return index[Self.key(id)]?.state ?? "unavailable"
    }

    func resolve(_ id: String, rows: [Station]) -> Station? {
        guard let index = validatedEntries else { return nil }
        let direct = rows.first { $0.id == id }
        if let revision = direct?.referenceRevision, revision != self.revision { return nil }
        guard let entry = index[Self.key(id)] else { return direct }
        guard direct == nil, entry.state == "alias", let target = entry.canonicalId,
              index[Self.key(target)] == nil else { return nil }
        return rows.first { $0.id == target && $0.referenceRevision == revision }
    }
}
