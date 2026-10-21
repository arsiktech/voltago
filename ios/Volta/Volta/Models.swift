import CoreLocation
import Foundation

// Models mirroring the Volta Python API (server.py enriched rows).
// Decoded with .convertFromSnakeCase; everything but identity/coords is optional because
// confidence/source coverage varies per row — exactly like the web client. `id`/`lat`/`lon`
// stay non-optional on Station itself: a coord-less row (server.py keeps some leads keyed by
// name only) is instead dropped at the array boundary — see LossyArray below (CORR-07).

/// Decodes a JSON array leniently: each element decodes independently, and an element that
/// fails (e.g. a lead server.py deliberately keeps with no lat/lon, keyed by name only --
/// CORR-07) is silently dropped instead of aborting the whole array. A coord-less row can't be
/// mapped or routed to anyway, so dropping it is the honest fail-closed behavior: fewer
/// trustworthy rows, never a blanked list and never a crash.
///
/// `Element_`'s own `init(from:)` never throws (a per-element failure resolves to `nil` via
/// `try?`), so the unkeyed container's cursor always advances -- no risk of stalling on a
/// malformed element.
@propertyWrapper
struct LossyArray<Element: Decodable>: Decodable {
    var wrappedValue: [Element]

    init(wrappedValue: [Element]) {
        self.wrappedValue = wrappedValue
    }

    private struct Element_: Decodable {
        let value: Element?
        init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            value = try? container.decode(Element.self)
        }
    }

    init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        var elements: [Element] = []
        while !container.isAtEnd {
            if let value = try container.decode(Element_.self).value {
                elements.append(value)
            }
        }
        wrappedValue = elements
    }
}

struct ChargersResponse: Decodable {
    @LossyArray var stations: [Station]
    @LossyArray var leads: [Station]
    var warning: String?
    var cache: Cache?
    var meta: Meta?

    struct Cache: Decodable, Equatable {
        var liveSupported: Bool?
        var hit: Bool?
        var stale: Bool?
        var liveFailed: Bool?
        var freshLiveAuthority: Bool?
        var mixedLive: Bool?
        var filterFallback: Bool?
        var ageSeconds: Double?
        var ttlSeconds: Double?
    }

    struct Meta: Decodable {
        var coverage: Coverage?
        var truncated: Bool?
        struct Coverage: Decodable {
            var mode: String?
            var liveRouteReady: Int?
            var warning: String?
        }
    }
}

struct Station: Decodable, Identifiable, Hashable {
    static func == (a: Station, b: Station) -> Bool { a.id == b.id }
    func hash(into h: inout Hasher) { h.combine(id) }

    var id: String
    var name: String
    /// Source-reported operator text, not verified CPO identity. Optional so older JSON still decodes.
    var operatorName: String? = nil
    /// Source-reported OCPI OWNER party; not proof of the station's CPO role.
    var owner: StationOwner? = nil
    var city: String?
    var address: String?
    var lat: Double
    var lon: Double

    var priceMin: Double?
    var priceCurrency: String?
    var priceNativeUnit: String?
    var priceLabel: String?
    var isFree: Bool?
    var isCheap: Bool?
    /// Additional published/community/pass claims only. Never a verified station tariff.
    var priceEvidence: [PriceEvidence]? = nil

    var teslaOk: Bool?
    var fast: Bool?
    var maxPowerKw: Double?
    var connectorLabels: [String]?
    var connectors: [Connector]?
    var connectorSummary: ConnectorSummary?

    var status: String?
    var available: Int?
    var totalEvse: Int?

    var source: String?
    var sourceUrl: String?
    var sources: [SourceRef]?
    var primarySource: SourceRef?
    /// Exact charger evidence action, selected independently of broader provenance.
    var stationSource: SourceRef? = nil
    var reliability: String?

    var parking: ParkingProfile?
    var access: AccessProfile?
    var accessClaim: AccessClaim?
    var verification: Verification?
    var decision: Decision?
    var corroboration: Corroboration?
    var priceWindowStatus: PriceWindowStatus?
    var pricing: [PricingWindow]?
    var chargeClaim: Claim?
    var parkingClaim: Claim?
    var claimConflict: ClaimConflict?
    /// The source itself reports this charger out of service (server field
    /// `source_reported_unavailable`). Such a row stays on the map so drivers know it exists,
    /// but it is never a destination: the card leads with the outage, mutes the price and
    /// offers nearby chargers before directions.
    var sourceReportedUnavailable: SourceOutage?

    var freshnessMinutes: Int?
    var lastSeen: String?
    var distanceKm: Double?
    var driveEtaMinEst: Int?
    var navigation: [String: String]?
    var media: MediaProfile?
    var sourceDetails: SourceDetails? = nil
    var tariffDetails: TariffDetails? = nil
    var lead: Bool?
    /// Community evidence (plan 013) the server attaches to rows with driver check-ins/photos.
    /// It never affects live/Go-now trust — display + a fail-closed downgrade only.
    var community: CommunitySummary?
    /// Per-row stale marker emitted after a stale live-cache fallback.
    var cacheStale: Bool?
    /// Client-only context stamped from the enclosing response. Optional keeps synthesis
    /// forward-compatible with servers that know nothing about this presentation field.
    var responseTrust: StationTrustKind?

    var coordinate: CLLocationCoordinate2D { .init(latitude: lat, longitude: lon) }
    var isLead: Bool { lead ?? false }

    /// A directory's hardware inventory remains separate when the selected source differs.
    var sourcePageConnectors: [Connector] {
        let sourceConnectors = sourceDetails?.connectors ?? []
        return sourceConnectors.map(\.inventoryText).sorted() == (connectors ?? []).map(\.inventoryText).sorted()
            ? [] : sourceConnectors
    }

    enum CodingKeys: String, CodingKey {
        case id, name
        case operatorName = "operator"
        case owner, city, address, lat, lon
        case priceMin, priceCurrency, priceNativeUnit, priceLabel, isFree, isCheap, priceEvidence
        case teslaOk, fast, maxPowerKw, connectorLabels, connectors, connectorSummary
        case status, available, totalEvse
        case source, sourceUrl, sources, primarySource, stationSource, reliability
        case parking, access, accessClaim, verification, decision, corroboration
        case priceWindowStatus, pricing, chargeClaim, parkingClaim, claimConflict
        case sourceReportedUnavailable
        case freshnessMinutes, lastSeen, distanceKm, driveEtaMinEst, navigation, media, lead
        case sourceDetails, tariffDetails
        case community, cacheStale, responseTrust
    }
}

/// Display-only rates from explicit source units. Never a primary tariff or estimate input.
struct PriceEvidenceRate: Codable, Hashable {
    var kind: String
    var label: String?
    var min: Double
    var max: Double?
    var from: Bool
    var unit: String
    var afterMinutes: Double?

    var isValid: Bool {
        guard min.isFinite, min >= 0,
              max == nil || (max!.isFinite && max! >= min),
              label == nil || ["normal", "fast", "ultrafast", "motorcycle", "all"].contains(label!),
              afterMinutes == nil || (afterMinutes!.isFinite && afterMinutes! >= 0) else { return false }
        return (kind == "energy" && ["kWh", "started_kWh"].contains(unit) && afterMinutes == nil)
            || (kind == "idle" && unit == "minute" && label == nil && max == nil && !from)
    }
}

struct StationOwner: Decodable, Hashable {
    var name: String?
    var website: String?
    var phone: String?
    var logo: String?
}

/// Trust state for status-sensitive presentation. A lead is separate from the four possible
/// outcomes of a live request because directory/community evidence was never live.
enum StationTrustKind: String, Codable, Hashable {
    case lead, fresh, degraded, stale, unavailable
}

/// Community evidence for a station (plan 013): how many drivers reported a successful/failed
/// charge, and whether there's a fresh success/failure. Presentation only — the honesty engine
/// never lets this create live status (a failure streak can downgrade Go-now, never upgrade).
struct CommunitySummary: Decodable, Hashable {
    struct Checkins: Decodable, Hashable {
        var charged: Int?
        var failed: Int?
        var occupied: Int?
        var offline: Int?
        var blocked: Int?
        var total: Int?
        var lastSuccessAt: String?
        var lastFailureAt: String?
    }
    var checkins: Checkins?
    var recentSuccess: Bool?
    var recentFailure: Bool?
    var recentFailureCount: Int?

    var total: Int { checkins?.total ?? 0 }
    var chargedCount: Int { checkins?.charged ?? 0 }
    /// Failed + offline + blocked are "couldn't charge"; occupied is neutral (charger works).
    var failedCount: Int { (checkins?.failed ?? 0) + (checkins?.offline ?? 0) + (checkins?.blocked ?? 0) }
    var occupiedCount: Int { checkins?.occupied ?? 0 }
}

/// The single validation boundary for every public web handoff decoded from the service.
/// Foundation accepts many URL schemes that are useful elsewhere on iOS; Volta's evidence and
/// navigation actions intentionally accept only ordinary HTTP(S) pages with a real host.
enum PublicWebURL {
    private static func isPublicHostname(_ rawHost: String) -> Bool {
        let host = rawHost.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        guard !host.isEmpty,
              host.count <= 253,
              host != "localhost",
              !host.hasSuffix(".localhost"),
              !host.hasSuffix(".local"),
              !host.hasSuffix(".internal"),
              !host.hasSuffix(".home.arpa"),
              !host.contains(":")
        else { return false }

        let labels = host.split(separator: ".", omittingEmptySubsequences: false).map(String.init)
        // Legacy inet parsers treat dotted hexadecimal/octal labels as numeric addresses
        // (for example 0x7f.0.0.1 or 0177.0.0.1 => loopback). URLComponents preserves
        // those spellings, so reject them before treating a dotted value as DNS.
        if labels.contains(where: {
            $0.lowercased().hasPrefix("0x")
                || ($0.count > 1 && $0.first == "0" && $0.allSatisfy(\.isNumber))
        }) {
            return false
        }
        let numeric = labels.allSatisfy { !$0.isEmpty && $0.allSatisfy(\.isNumber) }
        if numeric {
            guard labels.count == 4 else { return false }
            let octets = labels.compactMap(Int.init)
            guard octets.count == 4, octets.allSatisfy({ (0...255).contains($0) }) else { return false }
            let a = octets[0]
            let b = octets[1]
            return !(a == 0
                     || a == 10
                     || a == 127
                     || (a == 100 && (64...127).contains(b))
                     || (a == 169 && b == 254)
                     || (a == 172 && (16...31).contains(b))
                     || (a == 192 && b == 0)
                     || (a == 192 && b == 88 && octets[2] == 99)
                     || (a == 192 && b == 168)
                     || (a == 198 && (b == 18 || b == 19))
                     || (a == 198 && b == 51 && octets[2] == 100)
                     || (a == 203 && b == 0 && octets[2] == 113)
                     || a >= 224)
        }

        // Public handoffs use DNS names. Single-label or malformed labels are local/
        // resolver-dependent identities rather than a trustworthy public destination.
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-"))
        return labels.count >= 2 && labels.allSatisfy { label in
            !label.isEmpty
                && label.count <= 63
                && label.first != "-"
                && label.last != "-"
                && label.unicodeScalars.allSatisfy(allowed.contains)
        }
    }

    static func validated(_ value: String?) -> URL? {
        guard let value,
              let components = URLComponents(string: value),
              let scheme = components.scheme?.lowercased(),
              scheme == "https" || scheme == "http",
              let host = components.host,
              isPublicHostname(host),
              components.user == nil,
              components.password == nil,
              let url = components.url
        else { return nil }
        return url
    }
}

/// Google Maps handoffs have a narrower boundary than general evidence links. A listing
/// needs a Google place identifier (or a Google share code); a name/coordinate search alone
/// must remain a search, even when a malformed service response labels it as a listing.
enum GoogleMapsURL {
    private static func validatedComponents(_ value: String?) -> URLComponents? {
        guard let url = PublicWebURL.validated(value),
              url.scheme?.lowercased() == "https",
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.port == nil || components.port == 443,
              let host = components.host?.lowercased(),
              components.percentEncodedHost?.lowercased() == host
        else { return nil }

        let path = components.path
        switch host {
        case "google.com", "www.google.com", "google.es", "www.google.es":
            guard path == "/maps" || path.hasPrefix("/maps/") else { return nil }
        case "maps.google.com", "maps.google.es":
            guard path.isEmpty || path == "/" || path == "/maps" || path.hasPrefix("/maps/")
            else { return nil }
        case "maps.app.goo.gl":
            guard path.range(of: #"^/[A-Za-z0-9_-]+/?$"#, options: .regularExpression) != nil
            else { return nil }
        case "goo.gl":
            guard path.range(of: #"^/maps/[A-Za-z0-9_-]+/?$"#, options: .regularExpression) != nil
            else { return nil }
        default:
            return nil
        }
        return components
    }

    static func listing(_ value: String?) -> URL? {
        guard let components = validatedComponents(value) else { return nil }
        let path = components.path
        guard path != "/maps/dir", !path.hasPrefix("/maps/dir/") else { return nil }
        if components.host?.lowercased() == "maps.app.goo.gl" || components.host?.lowercased() == "goo.gl" {
            return components.url
        }

        let items = components.queryItems ?? []
        let hasPlaceID = items.contains { item in
            let value = item.value ?? ""
            let placeID = item.name == "query_place_id" ? value
                : item.name == "q" && value.hasPrefix("place_id:") ? String(value.dropFirst(9)) : ""
            return placeID.range(of: #"^[A-Za-z0-9_-]{3,255}$"#, options: .regularExpression) != nil
        }
        let hasCID = items.contains {
            $0.name == "cid" && ($0.value ?? "").range(of: #"^[1-9][0-9]{0,19}$"#, options: .regularExpression) != nil
        }
        let hasFeatureID = items.contains {
            $0.name == "ftid" && ($0.value ?? "").range(
                of: #"^0x[0-9a-fA-F]+:0x[0-9a-fA-F]+$"#, options: .regularExpression
            ) != nil
        }
        let hasPlaceData = path.hasPrefix("/maps/place/") && components.percentEncodedPath.range(
            of: #"!(?:1s|16s)[^!/?#]+"#, options: .regularExpression
        ) != nil
        return hasPlaceID || hasCID || hasFeatureID || hasPlaceData ? components.url : nil
    }

    static func search(_ value: String?) -> URL? {
        guard let components = validatedComponents(value) else { return nil }
        let path = components.path
        guard path == "/maps" || path == "/maps/" || path == "/maps/search"
                || path == "/maps/search/" || path.isEmpty || path == "/",
              components.queryItems?.contains(where: {
                  ($0.name == "query" || $0.name == "q")
                      && !($0.value ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
              }) == true
        else { return nil }
        return components.url
    }
}

struct Connector: Decodable, Hashable {
    var connectorId: String?
    var label: String?
    var type: String?
    var status: String?
    var powerKw: Double?
    var isFree: Bool?
    var fast: Bool?
    var count: Int? = nil
    var sourceUrl: String? = nil

    var inventoryLabel: String {
        let name = [label, type].compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .first { !$0.isEmpty } ?? String(localized: "Unknown connector")
        guard let count, count > 0 else { return name }
        return "\(count) × \(name)"
    }

    var powerText: String? {
        guard let powerKw, powerKw.isFinite, powerKw > 0 else { return nil }
        return String(format: "%g kW", powerKw)
    }

    var safeSourceURL: URL? { PublicWebURL.validated(sourceUrl) }

    var inventoryText: String {
        guard let powerText else { return inventoryLabel }
        return "\(inventoryLabel) · \(powerText)"
    }
}

struct SourceDetails: Decodable, Hashable {
    var sourceUrl: String?
    var sourceName: String?
    var description: String?
    var openingHours: String?
    var access: String?
    var contact: SourceContact?
    var notes: [String]?
    var connectors: [Connector]?
    var connectorCount: Int?
    var maxPowerKw: Double?

    var safeSourceURL: URL? { PublicWebURL.validated(sourceUrl) }
}

struct SourceContact: Decodable, Hashable {
    var name: String?
    var phone: String?
    var email: String?
    var website: String?

    var safeWebsiteURL: URL? { PublicWebURL.validated(website) }
}

struct TariffDetails: Decodable, Hashable {
    var sourceUrl: String?
    var text: String?
    var conditions: [String]?
    var components: [TariffComponent]?
    var energyEurPerKwh: Double?
    var hourEur: Double?
    var minimumEur: Double?
    var unconditionalFree: Bool?

    var safeSourceURL: URL? { PublicWebURL.validated(sourceUrl) }
}

struct TariffComponent: Decodable, Hashable {
    var type: String?
    var price: Double?
    var unit: String?
}

private func validTariffAmount(_ value: Double?) -> Double? {
    guard let value, value.isFinite, value >= 0 else { return nil }
    return value
}

struct ConnectorSummary: Decodable, Hashable {
    var available: Int?
    var busy: Int?
    var charging: Int?
    var outOfService: Int?
    var unknown: Int?
    var total: Int?
    var maxPowerKw: Double?
}

struct SourceRef: Decodable, Hashable {
    var name: String?
    var url: String?
    var evidence: String?
    var kind: String?
    var label: String?
    var action: String?
    var quality: String?
    var exactness: String?
    var license: String? = nil
    var licenseUrl: String? = nil
    var attribution: String? = nil
    var transformation: String? = nil
    var sourceFetchedAt: String? = nil
    var sourcePublishedAt: String? = nil
    var sourceLastUpdated: String? = nil
}

extension SourceRef {
    /// Public source handoffs allow only ordinary HTTP(S) pages, never arbitrary decoded URL
    /// schemes that UIApplication could otherwise forward to another process.
    var safeURL: URL? {
        PublicWebURL.validated(url)
    }

    var safeLicenseURL: URL? {
        PublicWebURL.validated(licenseUrl)
    }

    static func displayName(for url: URL, preferred: String?) -> String {
        let host = (url.host ?? "").lowercased()
        let providers: [(String, String)] = [
            ("canariasterecarga.com", "Canarias te recarga"),
            ("openchargemap.org", "OpenChargeMap"), ("openchargemap.io", "OpenChargeMap"),
            ("openstreetmap.org", "OpenStreetMap"), ("mapareve.es", "MapaREVE"),
            ("gaiacharge.com", "Gaia"), ("fenieenergia.es", "Feníe Energía"),
            ("electromaps.com", "Electromaps"), ("chargemap.com", "Chargemap"),
            ("repsol.es", "Repsol"), ("tesla.com", "Tesla"), ("telpark.com", "Telpark"),
            ("interparking.es", "Interparking"), ("endollabarcelona.cat", "Endolla"),
            ("wenea.com", "Wenea"), ("zunder.com", "Zunder"), ("lidl.es", "Lidl")
        ]
        if let provider = providers.first(where: { host == $0.0 || host.hasSuffix("." + $0.0) }) {
            return provider.1
        }
        if let value = preferred?.trimmingCharacters(in: .whitespacesAndNewlines),
           !value.isEmpty, value.count <= 80,
           !["source", "directory", "directory page", "public source", "official data", "live data", "live source", "live map", "provider", "provider page", "community", "map", "website"].contains(value.lowercased()),
           value.unicodeScalars.allSatisfy({ !CharacterSet.controlCharacters.contains($0) }) {
            return value
        }
        return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
    }

    var displayName: String {
        guard let url = safeURL else { return String(localized: "Source") }
        return Self.displayName(for: url, preferred: name)
    }

    private static let genericPaths: Set<String> = [
        "map", "mapa", "maps", "locations", "stations", "charging-stations", "chargers", "points",
        "index", "index.html", "home", "contact", "contacto", "login", "about", "tariffs", "pricing",
        "locator", "search", "puntos-de-recarga", "findus"
    ]

    var isOverviewURL: Bool {
        guard let url = safeURL else { return false }
        let segments = url.path.lowercased().split(separator: "/").map(String.init)
        return segments.isEmpty || Self.genericPaths.contains(segments.last ?? "")
            || segments.joined(separator: "/").range(of: #"^[a-z]{2}$"#, options: .regularExpression) != nil
    }

    /// Mirrors the backend's exact station routes. Known providers never fall through
    /// to a metadata-only guess; feeds, regulations and arbitrary root queries stay references.
    var exactStationURL: URL? {
        guard let raw = self.url?.trimmingCharacters(in: .whitespacesAndNewlines), raw.count <= 4096,
              raw.range(of: #"[\s\\\x00-\x1f\x7f]"#, options: .regularExpression) == nil,
              let url = safeURL, let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.port == nil || components.port == 80 || components.port == 443,
              let rawHost = url.host,
              let decodedPath = components.percentEncodedPath.removingPercentEncoding,
              decodedPath.range(of: #"[\\\x00-\x1f\x7f]"#, options: .regularExpression) == nil,
              !decodedPath.split(separator: "/").contains(where: { $0 == "." || $0 == ".." })
        else { return nil }
        let host = rawHost.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        guard !host.split(separator: ".").allSatisfy({ $0.allSatisfy(\.isNumber) }) else { return nil }
        let domain = host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
        let path = components.percentEncodedPath
        func matches(_ expression: String, ignoringCase: Bool = false) -> Bool {
            path.range(of: expression, options: ignoringCase ? [.regularExpression, .caseInsensitive] : [.regularExpression]) != nil
        }
        let query = components.queryItems ?? []
        func values(_ name: String) -> [String] { query.filter { $0.name == name }.compactMap(\.value) }
        let exact: Bool
        switch domain {
        case "canariasterecarga.com":
            exact = components.query?.isEmpty != false && matches(#"^/(?:es|en)/(?:(?:mapa/)?p\d{1,10}|punto-de-recarga-[a-z0-9-]+)/?$"#)
        case "gaiacharge.com", "partner-next.gaiacharge.com":
            exact = matches(#"^/cpo/jump/[^/]+/(?:[^/]+/)?[^/]+/?$"#)
        case "openstreetmap.org":
            exact = matches(#"^/(?:node|way|relation)/[1-9]\d*/?$"#)
        case "openchargemap.org":
            exact = matches(#"^/site/poi/details/[1-9]\d*/?$"#, ignoringCase: true)
        case "map.openchargemap.io":
            let ids = values("id"), pois = values("poi")
            let identifiers = ids.isEmpty ? pois : ids
            exact = (path.isEmpty || path == "/") && values("mode") == ["details"] && identifiers.count == 1
                && (ids.isEmpty || pois.isEmpty || ids == pois)
                && identifiers[0].range(of: #"^[1-9]\d*$"#, options: .regularExpression) != nil
        case "electromaps.com", "map.electromaps.com":
            exact = matches(#"^/(?:en|es|fr|pt)/p/[1-9]\d*(?:/[^/]+)*/?$"#)
        case "chargemap.com":
            exact = matches(#"^/(?!(?:index|map|contact|about|terms|privacy|pricing|tariffs)\.html$)[a-z0-9][a-z0-9-]*\.html$"#, ignoringCase: true)
                || matches(#"^/[a-z]{2}(?:-[a-z]{2})?/charging-stations/[^/]+/?$"#, ignoringCase: true)
        case "recarga.fenieenergia.es":
            exact = matches(#"^/punto-recarga/(?!todos/?$)[a-zA-Z0-9_-]+/?$"#)
        case "tesla.com":
            exact = matches(#"^/(?:[a-z]{2}_[a-z]{2}/)?findus/location/(?:supercharger|destinationcharger)/[^/]+/?$"#, ignoringCase: true)
        case "telpark.com":
            exact = matches(#"^/[a-z]{2}/ciudades/[a-z0-9-]+/[a-z0-9-]+/?$"#)
        case "interparking.es":
            exact = matches(#"^/es/aparcamientos/[a-z0-9/_-]+/?$"#)
        default:
            let segments = decodedPath.lowercased().split(separator: "/").map(String.init)
            exact = ["station_page", "start_page", "provider_station_page", "map_object"].contains(kind?.lowercased() ?? "")
                && !segments.isEmpty && !Self.genericPaths.contains(segments.last ?? "")
                && segments.joined(separator: "/").range(of: #"^[a-z]{2}(?:-[a-z]{2})?$"#, options: .regularExpression) == nil
                && !matches(#"\.(?:json|geojson|csv|xml|pdf|zip|gz)$"#, ignoringCase: true)
        }
        return exact ? url : nil
    }

    /// Provider-authored guidance is useful, but remains remote text: require one short,
    /// printable line and fall back to stable localized copy when malformed.
    var safeActionLabel: String {
        let fallback = String(localized: "Verify at source")
        guard safeURL != nil, let action else { return fallback }
        let value = action.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, value.count <= 60,
              value.unicodeScalars.allSatisfy({ !CharacterSet.controlCharacters.contains($0) })
        else { return fallback }
        return value
    }
}

struct ParkingProfile: Decodable, Hashable {
    var kind: String?
    var label: String?
    var paid: Bool?
    var note: String?
}

struct StationSourceReference {
    let url: URL
    let label: String
}

struct AccessProfile: Decodable, Hashable {
    var level: String?
    var label: String?
    var note: String?
}

struct AccessClaim: Decodable, Hashable {
    var source: String?
    var url: String?
    var level: String?
    var label: String?
    var parkingKind: String?
    var parkingPaid: Bool?
    var ownership: String?
    var privateOrRestricted: Bool?
    var summary: String?
}

struct MediaProfile: Decodable, Hashable {
    var photos: [StationPhoto]?
    var photoStatus: String?
    var note: String?
}

struct StationPhoto: Decodable, Hashable {
    var url: String?
    var thumbnailUrl: String?
    var source: String?
    var sourceUrl: String?
    var caption: String?
    var license: String?
    /// Automatic display is fail-closed until this exact media item has a documented right.
    var displayRightsApproved: Bool?
}

extension StationPhoto {
    /// An explicit handoff can show the source without automatically loading its image.
    var safeSourceURL: URL? {
        PublicWebURL.validated(sourceUrl) ?? PublicWebURL.validated(url) ?? PublicWebURL.validated(thumbnailUrl)
    }

    /// Automatic media loading shares the same public-web boundary as explicit source and
    /// navigation handoffs. Prefer the full image only when it is safe, then try its thumbnail.
    var safeURL: URL? {
        guard displayRightsApproved == true else { return nil }
        return PublicWebURL.validated(url) ?? PublicWebURL.validated(thumbnailUrl)
    }
}

struct Verification: Decodable, Hashable {
    var level: String?
    var label: String?
    var note: String?
    var liveVerified: Bool?
    var locationVerified: Bool?
    var priceVerified: Bool?
    var routeChecked: Bool?
}

struct VerificationPresentation: Equatable {
    var title: String
    var detail: String?
}

struct Decision: Decodable, Hashable {
    var level: String?   // forward-compatible: unknown levels fall back in the UI (web D-084)
    var label: String?
    var reason: String?
}

struct Corroboration: Decodable, Hashable {
    var independentSources: Int?
    var families: [String]?
    var tier: String?
}

struct PriceWindowStatus: Decodable, Hashable {
    var label: String?
    var isCurrentlyWithinStandard: Bool?
    var currentWindows: [PricingWindow]?
}

struct PricingWindow: Decodable, Hashable {
    var price: Double?
    var currency: String?
    var unit: String?
    var isCurrentlyValid: Bool?
    var validFrom: String?
    var validUntil: String?
    var label: String?
}

/// Charging-PRICE provenance (Station.chargeClaim) and parking-COST provenance
/// (Station.parkingClaim) are tracked SEPARATELY per station/lead (server.py's
/// ensure_claim_contract, AGENTS.md Prime Directive #1) and may disagree with each other (see
/// ClaimConflict below) -- the two claims must never be merged into one. Mirrors the web's
/// `Claim` interface (frontend/src/types.ts).
struct Claim: Decodable, Hashable {
    var type: String?
    var kind: String?
    var source: String?
    var url: String?
    var priceMin: Double?
    var costHint: String?
    var summary: String?
}

struct ClaimConflict: Decodable, Hashable {
    var summary: String?
}

/// A source's own out-of-service report, for example Feníe's `estado` 4.
struct SourceOutage: Decodable, Hashable {
    var source: String?
    var summary: String?
}

extension Station {
    /// True when the source itself reports the charger out of service.
    var isReportedOutOfService: Bool { sourceReportedUnavailable != nil }
}

struct GeocodeResponse: Decodable {
    var results: [GeocodeResult]
}

struct GeocodeResult: Decodable, Identifiable, Hashable {
    var label: String
    var name: String
    var lat: Double
    var lon: Double
    var bbox: GeoBbox?
    var id: String { "\(lat),\(lon),\(label)" }
}

struct GeoBbox: Decodable, Hashable {
    var latitudeSw: Double
    var longitudeSw: Double
    var latitudeNe: Double
    var longitudeNe: Double
}

// MARK: - Presentation helpers (ported from frontend/src/lib/format.ts)

enum DecisionKind: String {
    case go, check, verify, lead, avoid

    var label: String {
        switch self {
        case .go: String(localized: "Go now")
        case .check: String(localized: "Check first")
        case .verify: String(localized: "Verify first")
        case .lead: String(localized: "Price lead")
        case .avoid: String(localized: "Avoid")
        }
    }

    var color: SwiftUI.Color {
        switch self {
        case .go: Theme.go
        case .check: Theme.check
        case .verify: Theme.verify
        case .lead: .secondary
        case .avoid: Theme.danger
        }
    }

    var symbol: String {
        switch self {
        case .go: "checkmark"
        case .check: "clock"
        case .verify: "shield.lefthalf.filled"
        case .lead: "mappin"
        case .avoid: "nosign"
        }
    }
}

import SwiftUI

extension Claim {
    /// "via {source}" fallback text, mirroring the web's `charge.source || 'source'`
    /// (frontend/src/components/SourceProvenance.tsx).
    var sourceLabel: String { (source?.isEmpty == false) ? source! : "source" }

    var sourceURL: URL? {
        PublicWebURL.validated(url)
    }
}

extension Station {
    /// Conservative source-backed title. Same rules as frontend/src/lib/provider.ts.
    /// Never infers Tesla Supercharger from serial, power, or tesla_ok. Identifier-like
    /// source names keep exact casing. Source name/id are not mutated.
    var stationTitle: String {
        let provider = Self.providerIdentity(operatorText: operatorName, ownerName: owner?.name, stationName: name)
        let place = Self.placeName(name)
        if let provider, !place.lowercased().hasPrefix(provider.lowercased()) {
            return "\(provider) · \(place)"
        }
        return place
    }

    private static func placeName(_ raw: String) -> String {
        let compact = raw.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if compact.rangeOfCharacter(from: .decimalDigits) != nil, compact.rangeOfCharacter(from: .whitespaces) == nil {
            return compact
        }
        return displayPlaceName(compact)
    }

    private static func displayPlaceName(_ name: String) -> String {
        let clean = name.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if clean.isEmpty { return "Charging point" }
        if clean == clean.uppercased(), clean.count > 4 {
            return clean.lowercased().split(separator: " ").map { word in
                word.split(whereSeparator: { $0 == "-" || $0 == "/" }).map { part in
                    let s = String(part)
                    guard let first = s.first else { return s }
                    return String(first).uppercased() + s.dropFirst()
                }.joined(separator: word.contains("-") ? "-" : (word.contains("/") ? "/" : ""))
            }.joined(separator: " ")
        }
        return clean
    }

    private static func providerIdentity(operatorText: String?, ownerName: String?, stationName: String) -> String? {
        let explicit = [operatorText].compactMap { value -> String? in
            guard let value else { return nil }
            let trimmed = value.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
        // iOS compact payloads currently carry at most one operator string; conflict fail-closed
        // remains the web rule when both operator and raw.operator exist.
        let name = explicit.first ?? {
            let owner = (ownerName ?? "").replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            return owner.isEmpty ? nil : owner
        }()
        guard var resolved = name else { return nil }
        if resolved.range(of: #"\btesla\b"#, options: [.regularExpression, .caseInsensitive]) != nil {
            let superOperator = resolved.range(of: "supercharg", options: .caseInsensitive) != nil
            let destinationOperator = resolved.range(of: "destination", options: .caseInsensitive) != nil
            let combined = stationName + " " + resolved
            if (superOperator && combined.range(of: #"destination|compatible"#, options: [.regularExpression, .caseInsensitive]) != nil)
                || (destinationOperator && stationName.range(of: "supercharg", options: .caseInsensitive) != nil) {
                return nil
            }
        }
        if resolved.range(of: #"^bp pulse$"#, options: [.regularExpression, .caseInsensitive]) != nil {
            resolved = "bp pulse"
        }
        if resolved.range(of: #"^iberdrola$"#, options: [.regularExpression, .caseInsensitive]) != nil {
            resolved = "Iberdrola"
        }
        if resolved.range(of: #"^tesla$"#, options: [.regularExpression, .caseInsensitive]) != nil {
            let superchargerPlace = stationName.range(of: #"\bsupercharger\b"#, options: [.regularExpression, .caseInsensitive]) != nil
            let destinationOrCompatible = stationName.range(of: #"destination|compatible"#, options: [.regularExpression, .caseInsensitive]) != nil
            resolved = superchargerPlace && !destinationOrCompatible ? "Tesla Supercharger" : "Tesla"
        }
        return resolved
    }

    /// Safe decision classification — unknown backend levels fall back honestly (web D-084).
    var decisionKind: DecisionKind {
        switch trustKind {
        case .stale, .degraded: return .check
        case .unavailable: return .verify
        case .lead, .fresh: break
        }
        if let level = decision?.level, let kind = DecisionKind(rawValue: level) { return kind }
        return isLead ? .verify : .check
    }

    var decisionLabel: String {
        switch trustKind {
        case .stale, .unavailable: return String(localized: "Refresh first")
        case .degraded: return String(localized: "Check first")
        case .lead, .fresh: return decision?.label ?? decisionKind.label
        }
    }

    var trustKind: StationTrustKind {
        if isLead { return .lead }
        // A selected live station can outlive the response that supplied it. When a later
        // request fails or removes that row, AppState stamps the retained detail unavailable.
        // That current response context must outrank an older per-row stale marker; otherwise
        // stale -> unavailable would incorrectly remain merely stale and imply refreshable
        // status still exists.
        if responseTrust == .unavailable { return .unavailable }
        if cacheStale == true { return .stale }
        return responseTrust ?? .fresh
    }

    var canShowLiveAvailability: Bool { trustKind == .fresh }
    var canShowDecisionReason: Bool { trustKind == .fresh || trustKind == .lead }

    /// A server verification label describes the response at the time it was assembled. Once
    /// client trust is stale, degraded, or unavailable, repeating optimistic remote wording
    /// such as “Live verified” or “current tariff/status” would contradict the current card.
    /// Keep the evidence block, but explicitly present it as provenance rather than current
    /// operational verification.
    var verificationPresentation: VerificationPresentation? {
        guard let verification else { return nil }
        switch trustKind {
        case .stale, .degraded, .unavailable:
            return VerificationPresentation(
                title: String(localized: "Source evidence only"),
                detail: String(localized: "The source record is retained below, but it does not confirm the current tariff or status.")
            )
        case .lead, .fresh:
            return VerificationPresentation(
                title: verification.label ?? String(localized: "Verification"),
                detail: verification.note
            )
        }
    }

    func withTrust(_ trust: StationTrustKind) -> Station {
        var copy = self
        copy.responseTrust = trust
        return copy
    }

    var priceText: String {
        if isFree == true || priceMin == 0 { return String(localized: "Free") }
        if let p = validTariffAmount(priceMin) { return String(format: "€%.2f", p) }
        if let hourly = validTariffAmount(tariffDetails?.hourEur) { return String(format: "€%.2f", hourly) }
        return "—"
    }

    private var publishedTariffText: String? {
        guard let text = tariffDetails?.text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        let freeStatement = #"^(?:(?:tariff|tarifa|charging|charge|energy|rate)\s*[:\-]?\s*)?(?:free|gratis|gratuit[oa]s?)\b|^first\s+(?:hour|\d+\s*(?:minutes?|hours?))\s+free\b"#
        let explicitRate = #"(?:€\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*(?:€|eur(?:os?)?))\s*(?:/|per\s+)\s*(?:kwh|hours?|horas?|h|minutes?|minutos?|min|sessions?|sesión)\b"#
        let options: String.CompareOptions = [.regularExpression, .caseInsensitive]
        return text.range(of: freeStatement, options: options) != nil
            || text.range(of: explicitRate, options: options) != nil ? text : nil
    }

    /// Contact and booking guidance stays in source details, never in the price position.
    var displayPriceText: String {
        if priceText != "—" { return priceText }
        return publishedTariffText ?? String(localized: "Unknown")
    }

    var chargingPriceLabel: String {
        displayPriceText != String(localized: "Unknown") && !hasVerifiedPrice
            ? String(localized: "Source price") : String(localized: "Charging price")
    }

    /// List/provenance views can retain a source-reported price with their honesty labels,
    /// but map price pills and their VoiceOver values must not turn a
    /// directory/community/OSM lead into a verified tariff. Only the server's explicit
    /// price-verification bit authorizes those compact map price surfaces.
    var hasVerifiedPrice: Bool {
        guard verification?.priceVerified == true else { return false }
        return isFree == true || priceMin != nil
    }

    var priceUnit: String? {
        if isFree == true || priceMin == 0 { return nil }
        if validTariffAmount(priceMin) != nil { return "/kWh" }
        return validTariffAmount(tariffDetails?.hourEur) != nil ? String(localized: "/hour") : nil
    }

    var tariffNotes: [String] {
        guard let tariff = tariffDetails else { return [] }
        var notes: [String] = []
        let minimum = tariff.minimumEur ?? tariff.components?.first { $0.type == "MINIMUM" }?.price
        if let minimum = validTariffAmount(minimum) {
            notes.append(String(localized: "Minimum charge €\(String(format: "%.2f", minimum))"))
        }
        if (validTariffAmount(priceMin) != nil || isFree == true), let hourly = validTariffAmount(tariff.hourEur) {
            notes.append(String(localized: "Charging time €\(String(format: "%.2f", hourly))/hour"))
        }
        for component in tariff.components ?? [] {
            if component.type == "FLAT", let price = validTariffAmount(component.price) {
                notes.append(String(localized: "Session fee €\(String(format: "%.2f", price))"))
            }
        }
        notes += (tariff.conditions ?? []).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        var seen = Set<String>()
        return notes.filter { seen.insert($0).inserted }
    }

    /// Locale-INVARIANT tier classification (live / official / directory / community). Internal
    /// comparisons (tierIsStrong, hasExactDirectoryLocation, isMapLocationVerified -- all
    /// map-pin/honesty-verification logic) must key off this, never off `tierLabel`: `tierLabel`
    /// is now localized display text (String(localized:)), and comparing verification logic
    /// against a display string would silently break under a non-English device language.
    private var tierKind: String {
        if !isLead { return "live" }
        let r = (reliability ?? "").lowercased()
        if r.hasPrefix("official") { return "official" }
        if r.contains("directory") || r.contains("map") || r.contains("locator") { return "directory" }
        return "community"
    }

    /// live / official / directory / community — mirrors sourceTier() on the web. Localized
    /// DISPLAY text derived from `tierKind`; never compare against this for logic.
    var tierLabel: String {
        switch trustKind {
        case .stale: return String(localized: "Stale")
        case .degraded: return String(localized: "Limited live")
        case .unavailable: return String(localized: "Unavailable")
        case .lead, .fresh: break
        }
        switch tierKind {
        case "live": return String(localized: "Live")
        case "official": return String(localized: "Official")
        case "directory": return String(localized: "Directory")
        default: return String(localized: "Community")
        }
    }

    var tierIsStrong: Bool {
        (trustKind == .fresh || trustKind == .lead)
            && (tierKind == "live" || tierKind == "official")
    }

    var hasExactDirectoryLocation: Bool {
        guard tierKind == "directory", verification?.locationVerified == true else { return false }
        let kind = (primarySource?.kind ?? "").lowercased()
        if ["station_page", "start_page", "provider_station_page", "map_object", "live_map", "official_data"].contains(kind) {
            return true
        }
        let quality = (primarySource?.quality ?? primarySource?.exactness ?? "").lowercased()
        if ["exact", "map", "official"].contains(quality) {
            return true
        }
        let url = (primarySource?.url ?? sourceUrl ?? "").lowercased()
        return url.range(of: #"^https://(www\.)?openchargemap\.org/site/poi/details/\d+([/?#]|$)"#, options: .regularExpression) != nil
            || url.contains("map.electromaps.com")
            || url.range(of: #"electromaps\.com/(en|es|fr|pt)/p/"#, options: .regularExpression) != nil
            || url.contains("chargemap.com/")
    }

    /// OSM node/way/relation URLs already carry an exact public coordinate. Pin them
    /// as Not live; never treat them as Go-now.
    var hasExactOsmLocation: Bool {
        guard lat.isFinite, lon.isFinite else { return false }
        let url = (primarySource?.url ?? sourceUrl ?? "").lowercased()
        return url.range(of: #"openstreetmap\.org/(node|way|relation)/\d+"#, options: .regularExpression) != nil
    }

    /// Map pins need a stricter bar than list rows: a list can show weak leads
    /// with honest copy, but a pin visually claims location. Exact public
    /// directory pages and OSM objects are shown as non-live pins.
    var isMapLocationVerified: Bool {
        guard lat.isFinite, lon.isFinite else { return false }
        if !isLead { return verification?.locationVerified != false }
        if verification?.routeChecked == true { return true }
        if hasExactDirectoryLocation { return true }
        if hasExactOsmLocation { return true }
        // Official registry coordinates prove the place even without a published tariff.
        return tierKind == "official" && verification?.locationVerified == true
    }

    /// A true route-ready row is live, actionable now, and has free connectors.
    /// Lead/directory rows never qualify for celebration moments.
    var isRouteReady: Bool {
        guard trustKind == .fresh, !isLead, decisionKind == .go else { return false }
        guard verification?.liveVerified == true else { return false }

        let verificationLevel = verification?.level
        guard verificationLevel == nil || verificationLevel?.isEmpty == true || verificationLevel == "live" else {
            return false
        }

        if let connectorAvailability = connectorSummary?.available { return connectorAvailability > 0 }
        if let available { return available > 0 }
        return status?.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() == "AVAILABLE"
    }

    /// Exact station records remain useful even when current connector availability is not
    /// strong enough for Go-now. They may appear in the map/carousel, but never qualify for
    /// route-ready celebrations or availability claims unless `isRouteReady` also passes.
    var isVerifiedStationRecord: Bool {
        guard !isLead, cacheStale != true else { return false }
        guard trustKind == .fresh || trustKind == .degraded else { return false }
        guard verification?.liveVerified == true, verification?.locationVerified != false else {
            return false
        }
        let level = verification?.level
        return level == nil || level?.isEmpty == true || level == "live"
    }

    var corroborationCount: Int {
        max(corroboration?.independentSources ?? sources?.count ?? 1, 1)
    }

    /// Time-of-use tariff chip — only for genuinely time-bound windows (web tariffWindow()).
    var tariffWindowLabel: (text: String, currentlyCheap: Bool)? {
        let windows = pricing ?? []
        guard windows.contains(where: { $0.validFrom != nil || $0.validUntil != nil }) else { return nil }
        guard let label = priceWindowStatus?.label ?? priceLabel else { return nil }
        return (label, priceWindowStatus?.isCurrentlyWithinStandard != false)
    }

    var distanceText: String? {
        guard let km = distanceKm else { return nil }
        if km < 1 { return "\(Int((km * 1000).rounded())) m" }
        if km < 10 { return String(format: "%.1f km", km) }
        return "\(Int(km.rounded())) km"
    }

    var freshnessText: String? {
        guard let mins = freshnessMinutes else { return nil }
        if mins < 1 { return String(localized: "just now") }
        if mins < 60 { return String(localized: "\(mins) min ago") }
        let h = Double(mins) / 60
        if h < 48 { return String(localized: "\(Int(h.rounded())) h ago") }
        let days = h / 24
        if days < 60 { return String(localized: "\(Int(days.rounded())) d ago") }
        if days < 730 { return String(localized: "\(Int((days / 30).rounded())) mo ago") }
        return String(localized: "\(Int((days / 365).rounded())) y ago")
    }

    /// Row-card freshness fact (DIR-03): same freshnessText, gated only on its presence -- NOT
    /// on isLead (matches the web's `st.last_seen || st.freshness_minutes != null` gate in
    /// StationCard.tsx) -- but worded to distinguish evidence-age-on-leads ("As of …") from
    /// live-freshness-on-live-rows ("Updated …") so a driver never mistakes a lead's
    /// scrape-time age for a live status refresh (GOAL_CONTRACT.md's Trust UX bullet: "evidence
    /// age on leads, live freshness on live rows -- visually distinct").
    var freshnessRowText: String? {
        guard let fresh = freshnessText else { return nil }
        if trustKind == .stale { return String(localized: "Cached \(fresh)") }
        return isLead ? String(localized: "As of \(fresh)") : String(localized: "Updated \(fresh)")
    }

    var mapsURL: URL? {
        if let u = PublicWebURL.validated(navigation?["google"]) { return u }
        if let u = PublicWebURL.validated(navigation?["apple"]) { return u }
        return PublicWebURL.validated("https://maps.apple.com/?ll=\(lat),\(lon)")
    }

    var googleMapsListingURL: URL? {
        GoogleMapsURL.listing(navigation?["google_listing"])
    }

    /// Older services may not send Google links yet. Keep their cards useful with an
    /// explicitly labeled name/address search, with coordinates only as nearby context.
    var googleMapsActionURL: URL? {
        if let url = googleMapsListingURL { return url }
        if let url = GoogleMapsURL.search(navigation?["google_search"]) { return url }
        let place = [name, address, city].compactMap { value -> String? in
            let text = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return text.isEmpty ? nil : text
        }.joined(separator: ", ")
        var query = "EV charging station"
        if !place.isEmpty { query += " \(place)" }
        if lat.isFinite, lon.isFinite, (-90...90).contains(lat), (-180...180).contains(lon) {
            query += " near \(lat),\(lon)"
        }
        var components = URLComponents()
        components.scheme = "https"
        components.host = "www.google.com"
        components.path = "/maps/search/"
        components.queryItems = [URLQueryItem(name: "api", value: "1"), URLQueryItem(name: "query", value: query)]
        return components.url
    }

    var googleMapsActionLabel: String {
        googleMapsListingURL != nil ? String(localized: "View Google Maps") : String(localized: "Search Google Maps")
    }

    var googleMapsActionHint: String {
        googleMapsListingURL != nil
            ? String(localized: "Opens this charger's Google Maps listing for reviews and place details")
            : String(localized: "Searches Google Maps; confirm the result matches this charger")
    }

    var sourceURL: URL? {
        exactStationSource?.exactStationURL
    }

    var sourceActionLabel: String {
        guard let selected = exactStationSource else { return String(localized: "Source unavailable") }
        if ["start_page", "provider_station_page"].contains(selected.kind?.lowercased() ?? "") {
            return String(localized: "Start/check \(selected.displayName)")
        }
        return String(localized: "Open \(selected.displayName)")
    }

    private var sourceCandidates: [SourceRef] {
        var candidates = [stationSource, primarySource].compactMap { $0 }
        if let details = sourceDetails {
            candidates.append(SourceRef(name: details.sourceName, url: details.sourceUrl))
        }
        if let tariff = tariffDetails {
            candidates.append(SourceRef(name: chargeClaim?.source, url: tariff.sourceUrl))
        }
        candidates.append(SourceRef(name: chargeClaim?.source, url: chargeClaim?.url))
        candidates.append(SourceRef(name: source, url: sourceUrl))
        candidates += sources ?? []
        candidates += (connectors ?? []).map { SourceRef(name: source, url: $0.sourceUrl) }
        return candidates
    }

    /// Backward-compatible with saved responses; new responses supply station_source.
    private var exactStationSource: SourceRef? {
        for var candidate in sourceCandidates {
            guard let url = candidate.exactStationURL else { continue }
            candidate.name = SourceRef.displayName(for: url, preferred: candidate.name)
            let host = (url.host ?? "").lowercased().replacingOccurrences(of: "www.", with: "", options: .anchored)
            switch host {
            case "gaiacharge.com", "partner-next.gaiacharge.com": candidate.kind = "start_page"
            case "recarga.fenieenergia.es": candidate.kind = "provider_station_page"
            case "openstreetmap.org": candidate.kind = "map_object"
            default: break
            }
            return candidate
        }
        return nil
    }

    /// A generic home/map reference may use the same source's existing charger page.
    /// Specific tariff regulations and feed URLs retain their original provenance.
    func sourceReference(_ value: String?, preferredName: String? = nil, tariff: Bool = false) -> StationSourceReference? {
        guard let url = PublicWebURL.validated(value) else { return nil }
        let original = SourceRef(name: preferredName, url: url.absoluteString)
        var exact = original.exactStationURL != nil ? original : nil
        if exact == nil {
            let host = url.host?.lowercased().replacingOccurrences(of: "www.", with: "", options: .anchored)
            exact = sourceCandidates.first { candidate in
                guard let candidateURL = candidate.exactStationURL else { return false }
                let sameHost = candidateURL.host?.lowercased().replacingOccurrences(of: "www.", with: "", options: .anchored) == host
                return candidateURL == url || (original.isOverviewURL && sameHost)
            }
        }
        let destination = exact?.exactStationURL ?? url
        let name = SourceRef.displayName(for: destination, preferred: exact?.name ?? preferredName)
        let label: String
        if exact != nil { label = String(localized: "Open \(name)") }
        else if tariff { label = String(localized: "Tariff reference: \(name)") }
        else { label = String(localized: "General reference: \(name)") }
        return StationSourceReference(url: destination, label: label)
    }

    /// Best-known eligible access path from verified fields only. Never invents an app, RFID,
    /// or payment method the sources did not supply.
    var accessGuidance: (title: String, detail: String) {
        if let access, access.level == "restricted" || access.level == "customer" {
            let title = access.label ?? String(localized: "Restricted access")
            let detail = access.note ?? String(localized: "This charger may need a customer account, membership, or onsite permission. Volta cannot start the session.")
            return (title, detail)
        }
        if let claim = accessClaim, claim.privateOrRestricted == true {
            let title = claim.label ?? String(localized: "Private or restricted")
            let detail = claim.summary ?? String(localized: "Sources mark this as private or restricted. Confirm access before driving.")
            return (title, detail)
        }
        if sourceURL != nil {
            return (
                String(localized: "Start charging outside Volta"),
                String(localized: "Open the public source to see the operator app, RFID, or payment option that source actually lists. Volta does not start or pay for sessions.")
            )
        }
        return (
            String(localized: "How to start is unknown"),
            String(localized: "No verified start method is published here. Check onsite signs; do not assume an operator app or card will work.")
        )
    }

    var firstPublicPhoto: StationPhoto? {
        media?.photos?.first { $0.safeURL != nil }
    }

}
