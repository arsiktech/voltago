import Foundation
@testable import Volta

/// Shared JSON-decode plumbing for VoltaTests. Every test still supplies its OWN inline JSON
/// literal (mirroring exactly what server.py's /api/chargers sends: snake_case keys) -- this
/// file only centralizes the JSONDecoder configuration so it's guaranteed identical to
/// VoltaAPI.swift's private `decoder` (.convertFromSnakeCase), not a second copy that could
/// silently drift from the real network decode path.
enum TestDecode {
    static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    static func station(_ json: String) throws -> Station {
        try decoder().decode(Station.self, from: Data(json.utf8))
    }

    static func chargersResponse(_ json: String) throws -> ChargersResponse {
        try decoder().decode(ChargersResponse.self, from: Data(json.utf8))
    }
}
