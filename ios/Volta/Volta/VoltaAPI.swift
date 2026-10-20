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

    /// Stored evidence for the visible area; never downloads an unbounded global catalog.
    func overview(maxPrice: Double, bbox: BBox, coords: CLLocationCoordinate2D? = nil) async throws -> ChargersResponse {
        var query: [URLQueryItem] = [
            .init(name: "overview", value: "true"),
            .init(name: "include_leads", value: "true"),
            .init(name: "compact", value: "true"),
            .init(name: "max_price", value: String(maxPrice)),
            .init(name: "bbox", value: bbox.queryValue),
        ]
        if let coords {
            query.append(.init(name: "lat", value: String(coords.latitude)))
            query.append(.init(name: "lon", value: String(coords.longitude)))
        }
        return try await get("api/chargers", query)
    }

    /// Live viewport fetch; regional public status feeds merge through the server's shared decision path.
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

    init(south: Double, west: Double, north: Double, east: Double) {
        self.south = max(-90, min(90, south))
        self.north = max(-90, min(90, north))
        let fullWorld = abs(east - west) >= 360
        self.west = fullWorld ? -180 : Self.wrapLongitude(west)
        self.east = fullWorld ? 180 : Self.wrapLongitude(east)
    }

    static func wrapLongitude(_ lon: Double) -> Double {
        if (-180 ... 180).contains(lon) { return lon }
        let remainder = (lon + 180).truncatingRemainder(dividingBy: 360)
        return (remainder < 0 ? remainder + 360 : remainder) - 180
    }

    var longitudeSpan: Double { east >= west ? east - west : east - west + 360 }
    var centerLongitude: Double { Self.wrapLongitude(west + longitudeSpan / 2) }

    /// Matches the backend's default live-source viewport budget.
    var supportsLiveLookup: Bool {
        let latitude = (south + north) / 2
        let maxLongitudeSpan = 1.2 / max(0.35, cos(latitude * .pi / 180))
        return north - south <= 1.2 && longitudeSpan <= maxLongitudeSpan
    }

    var queryValue: String { "\(south),\(west),\(north),\(east)" }

    func contains(_ st: Station) -> Bool {
        contains(latitude: st.lat, longitude: st.lon)
    }

    func contains(latitude: Double, longitude: Double) -> Bool {
        guard latitude.isFinite, longitude.isFinite,
              (-90 ... 90).contains(latitude), (-180 ... 180).contains(longitude),
              latitude >= south, latitude <= north else { return false }
        let longitudes = abs(longitude) == 180 ? [-180.0, 180.0] : [longitude]
        return longitudes.contains { lon in
            west > east ? lon >= west || lon <= east : lon >= west && lon <= east
        }
    }
}
