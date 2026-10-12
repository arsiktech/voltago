import CoreLocation
import Foundation

/// Thin async client for the Volta Python backend.
/// Base URL is user-configurable: 127.0.0.1 works in the Simulator (shares the Mac's
/// loopback); on a real device point it at the Mac's LAN/Tailscale address.
struct VoltaAPI {
    var baseURL: URL
    private var session: URLSession

    init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private func get<T: Decodable>(_ path: String, _ query: [URLQueryItem]) async throws -> T {
        var comps = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        comps.queryItems = query
        var req = URLRequest(url: comps.url!)
        req.timeoutInterval = 45
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200 ..< 300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try Self.decoder.decode(T.self, from: data)
    }

    /// National leads-only overview (no live fetch server-side — instant).
    func overview(maxPrice: Double) async throws -> ChargersResponse {
        try await get("api/chargers", [
            .init(name: "overview", value: "true"),
            .init(name: "include_leads", value: "true"),
            .init(name: "max_price", value: String(maxPrice)),
        ])
    }

    /// Live viewport fetch (MapaREVE + permitted Gaia public pages merged server-side).
    func live(bbox: BBox, coords: CLLocationCoordinate2D?, maxPrice: Double, availableOnly: Bool) async throws -> ChargersResponse {
        var q: [URLQueryItem] = [
            .init(name: "include_leads", value: "true"),
            .init(name: "max_price", value: String(maxPrice)),
            .init(name: "bbox", value: bbox.queryValue),
        ]
        if let c = coords {
            q.append(.init(name: "lat", value: String(c.latitude)))
            q.append(.init(name: "lon", value: String(c.longitude)))
        }
        if availableOnly { q.append(.init(name: "available", value: "true")) }
        return try await get("api/chargers", q)
    }

    func geocode(_ query: String) async throws -> [GeocodeResult] {
        let resp: GeocodeResponse = try await get("api/geocode", [.init(name: "q", value: query)])
        return resp.results
    }

    /// Community check-in (plan 013): a driver reports whether charging actually worked.
    /// Verdicts: charged / failed / occupied / offline / blocked. The server validates the
    /// station id, rate-limits, and (on the owner's public deploy) may require a write token —
    /// this client sends the optional token so a Release build can be permitted to write.
    func checkin(stationID: String, verdict: String, note: String? = nil, token: String? = nil) async throws {
        let url = baseURL.appendingPathComponent("api/community/checkin")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 20
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token, !token.isEmpty { req.setValue(token, forHTTPHeaderField: "X-Volta-Community") }
        var body: [String: String] = ["station_id": stationID, "verdict": verdict]
        if let note, !note.isEmpty { body["note"] = note }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (_, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200 ..< 300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }
}

/// south,west,north,east — the server's bbox query format.
struct BBox: Equatable {
    var south: Double, west: Double, north: Double, east: Double

    var queryValue: String { "\(south),\(west),\(north),\(east)" }

    func contains(_ st: Station) -> Bool {
        st.lat >= south && st.lat <= north && st.lon >= west && st.lon <= east
    }
}
