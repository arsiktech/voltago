import Foundation

/// Canned JSON payloads for StubServer, shaped like server.py's real `/api/chargers` and
/// `/api/geocode` responses (snake_case keys -- VoltaAPI.swift's decoder applies
/// `.convertFromSnakeCase`, exactly as VoltaTests' fixtures already assume, e.g.
/// ChargersResponseDecodeTests.swift). Station names are unique, obviously-synthetic strings
/// ("UITest ...") so UI test predicates never collide with anything else that could appear in
/// the accessibility tree, and every URL is a non-resolving `example.com` placeholder -- these
/// are synthetic fixtures, not real public source data, and Volta's honesty rules never assert
/// anything about real providers here.
enum StubFixtures {
    static let goNowStationName = "UITest Live — Valencia Primary Charger"
    static let checkFirstStationName = "UITest Live — Valencia Check First Charger"
    static let leadStationName = "UITest Lead — Torrent Free Charger"

    /// Typed into SheetView's search field to trigger the "find near me" flow's search-a-place
    /// path (see CoreFlowsUITests' class doc comment for why search replaced the locate button).
    /// The stub ignores the actual query text and always returns `geocodeJSON` below.
    static let geocodeQueryText = "Valencia"
    /// A deliberately distinct name (not just "Valencia") so the result-row predicate in
    /// CoreFlowsUITests can never ambiguously match a later station row -- in practice there is
    /// no timing overlap either (the "Places" section replaces the station list while
    /// searching), but this keeps the query unambiguous by construction, not by ordering luck.
    static let geocodeResultName = "UITest Geocode Target"

    /// `GET /api/chargers?overview=true&...` -- AppState.loadOverview's national leads-only
    /// call. `stations` is empty by contract (AppState.swift's own comment: "if mode ==
    /// .overview { stations = resp.stations } // [] by contract").
    static var overviewChargersJSON: String {
        """
        {
          "stations": [],
          "leads": [\(leadJSON)],
          "meta": {"coverage": {"mode": "standard"}}
        }
        """
    }

    /// `GET /api/chargers?...bbox=...` (no `overview` param) -- AppState.fetchLive's viewport
    /// call, fired by locate/search/pan. One route-ready "Go now" live station, one "Check
    /// first" live station, plus the same directory lead (leads stay visible in live mode too,
    /// honestly marked "Not live").
    static var liveChargersJSON: String {
        """
        {
          "stations": [\(goNowStationJSON), \(checkFirstStationJSON)],
          "leads": [\(leadJSON)],
          "meta": {"coverage": {"mode": "standard"}}
        }
        """
    }

    /// Same otherwise-route-ready row, but explicitly marked stale at both the response and
    /// row level. This exercises the production contract where fresh merged providers may
    /// coexist with stale cached rows; only a row carrying `cache_stale` is demoted here.
    static var staleLiveChargersJSON: String {
        let staleGoNow = goNowStationJSON.replacingOccurrences(
            of: #""freshness_minutes": 1"#,
            with: #""cache_stale": true, "freshness_minutes": 1"#
        )
        return """
        {
          "stations": [\(staleGoNow)],
          "leads": [\(leadJSON)],
          "cache": {"hit": true, "stale": true, "live_failed": false, "filter_fallback": true, "age_seconds": 94, "ttl_seconds": 60},
          "warning": "Live source timed out; cached status is shown.",
          "meta": {"coverage": {"mode": "standard"}}
        }
        """
    }

    /// Fresh and stale rows may coexist when independent live providers answer at different
    /// ages. The response-level aggregate must announce both states while preserving each
    /// row's own route-readiness contract.
    static var mixedLiveChargersJSON: String {
        let staleCopy = goNowStationJSON
            .replacingOccurrences(of: #""id": "uitest-live-go""#, with: #""id": "uitest-live-stale""#)
            .replacingOccurrences(of: goNowStationName, with: "UITest Cached — Valencia Charger")
            .replacingOccurrences(
                of: #""freshness_minutes": 1"#,
                with: #""cache_stale": true, "freshness_minutes": 94"#
            )
        return """
        {
          "stations": [\(goNowStationJSON), \(staleCopy)],
          "leads": [\(leadJSON)],
          "cache": {"hit": true, "stale": true, "live_failed": false, "fresh_live_authority": true, "mixed_live": true, "filter_fallback": true, "age_seconds": 94, "ttl_seconds": 60},
          "warning": "Fresh live status is available; cached rows remain marked stale.",
          "meta": {"coverage": {"mode": "standard"}}
        }
        """
    }

    /// A syntactically healthy response with insufficient live-source coverage. The backend's
    /// Go label/availability are intentionally present so the client must demote them instead
    /// of merely echoing optimistic server fields.
    static var degradedLiveChargersJSON: String {
        """
        {
          "stations": [\(goNowStationJSON)],
          "leads": [\(leadJSON)],
          "warning": "Only one live source answered for this area.",
          "meta": {"coverage": {"mode": "degraded"}}
        }
        """
    }

    /// `GET /api/geocode?q=...` -- drives the "find near me" flow's search-a-place trigger (see
    /// CoreFlowsUITests' class doc comment). Its bbox is centered close enough to the fixture
    /// stations that AppState.pick()'s resulting `visibleRegion` still contains all three after
    /// the 1.2x expansion `pick()` applies.
    static var geocodeJSON: String {
        """
        {
          "results": [
            {
              "label": "Valencia, Comunitat Valenciana, España",
              "name": "\(geocodeResultName) — Valencia",
              "lat": 39.4699,
              "lon": -0.3763,
              "bbox": {"latitude_sw": 39.36, "longitude_sw": -0.46, "latitude_ne": 39.55, "longitude_ne": -0.30}
            }
          ]
        }
        """
    }

    /// Live, route-ready, free -- placed close to the geocodeJSON result's Valencia coordinate
    /// (39.4699, -0.3763) so it survives AppState.listRows' viewport bbox filter once
    /// AppState.pick() sets `visibleRegion` from the search result. Carries the full honesty
    /// surface (separate charge/parking claims, connectors, corroboration, sources) for the
    /// "inspect a charger" / "act on it" flows.
    private static var goNowStationJSON: String {
        """
        {
          "id": "uitest-live-go",
          "name": "\(goNowStationName)",
          "city": "Valencia",
          "address": "Carrer de Colón 5",
          "lat": 39.4750,
          "lon": -0.3770,
          "price_min": 0.0,
          "is_free": true,
          "lead": false,
          "status": "AVAILABLE",
          "available": 2,
          "total_evse": 3,
          "connector_summary": {"available": 2, "busy": 1, "out_of_service": 0, "total": 3, "max_power_kw": 22},
          "connectors": [
            {"connector_id": "1", "label": "Type 2", "type": "IEC_62196_T2", "status": "AVAILABLE", "power_kw": 22},
            {"connector_id": "2", "label": "Type 2", "type": "IEC_62196_T2", "status": "CHARGING", "power_kw": 22},
            {"connector_id": "3", "label": "Type 2", "type": "IEC_62196_T2", "status": "AVAILABLE", "power_kw": 22}
          ],
          "decision": {"level": "go", "label": "Go now", "reason": "Live, free, and route-ready right now."},
          "verification": {"level": "live", "label": "Live verified", "note": "Includes current tariff/status fields.", "live_verified": true, "location_verified": true, "price_verified": true, "route_checked": true},
          "corroboration": {"independent_sources": 1, "families": ["mapareve"], "tier": "live"},
          "parking": {"kind": "street", "label": "Street parking", "paid": false, "note": "Free municipal bay."},
          "charge_claim": {"type": "free", "source": "MapaREVE live OCPI feed", "url": "https://example.com/mapareve/uitest-live-go", "price_min": 0.0, "summary": "Live feed reports this connector as free."},
          "parking_claim": {"type": "street", "source": "MapaREVE live OCPI feed", "url": "https://example.com/mapareve/uitest-live-go", "summary": "Public street bay, no time limit reported."},
          "primary_source": {"name": "MapaREVE", "url": "https://example.com/mapareve/uitest-live-go", "action": "Check MapaREVE", "kind": "live_feed"},
          "source_url": "https://example.com/mapareve/uitest-live-go",
          "sources": [
            {"name": "MapaREVE", "url": "https://example.com/mapareve/uitest-live-go", "evidence": "Live OCPI status feed", "kind": "live_feed", "license": "CC BY 4.0", "license_url": "https://example.com/licenses/cc-by-4.0", "attribution": "UITest City Open Data", "transformation": "Coordinates normalized to WGS84.", "source_last_updated": "2026-07-15"},
            {"name": "UITest retrieval mirror", "url": "https://example.com/mirror/uitest-live-go", "evidence": "Fetch-only metadata fixture", "kind": "official_list", "source_fetched_at": "2026-07-15T08:00:00Z"}
          ],
          "freshness_minutes": 1
        }
        """
    }

    private static var checkFirstStationJSON: String {
        """
        {
          "id": "uitest-live-check",
          "name": "\(checkFirstStationName)",
          "city": "Valencia",
          "address": "Carrer de la Pau 9",
          "lat": 39.4550,
          "lon": -0.3850,
          "price_min": 0.19,
          "is_free": false,
          "lead": false,
          "status": "UNKNOWN",
          "available": 0,
          "total_evse": 2,
          "decision": {"level": "check", "label": "Check first", "reason": "Live feed can't confirm availability right now."},
          "verification": {"level": "live", "label": "Live OCPI feed", "live_verified": true, "location_verified": true},
          "freshness_minutes": 4
        }
        """
    }

    /// Directory lead -- never live, its own separate charge/parking claim provenance
    /// (Prime Directive #1: charge and parking provenance are tracked and never merged).
    private static var leadJSON: String {
        """
        {
          "id": "uitest-lead-01",
          "name": "\(leadStationName)",
          "city": "Torrent",
          "address": "Avinguda del Vedat 12",
          "lat": 39.4620,
          "lon": -0.4100,
          "price_min": 0.0,
          "is_free": true,
          "lead": true,
          "reliability": "official_list",
          "decision": {"level": "verify", "label": "Verify first", "reason": "Directory lead, not a live feed."},
          "verification": {"level": "directory", "label": "Directory listing", "location_verified": true},
          "corroboration": {"independent_sources": 2, "families": ["official_list", "electromaps"], "tier": "directory"},
          "charge_claim": {"type": "free", "source": "Official free-charger directory", "url": "https://example.com/directory/uitest-lead-01", "price_min": 0.0, "summary": "Published as a free customer charger."},
          "parking_claim": {"type": "customer", "source": "Official free-charger directory", "url": "https://example.com/directory/uitest-lead-01", "summary": "Free while shopping; store parking only."},
          "source_url": "https://example.com/directory/uitest-lead-01",
          "freshness_minutes": 2880
        }
        """
    }
}
