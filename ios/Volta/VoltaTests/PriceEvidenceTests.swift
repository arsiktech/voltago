import Foundation
import XCTest
@testable import Volta

@MainActor
final class PriceEvidenceTests: XCTestCase {
    private func allEvidence() throws -> [PriceEvidence] {
        let station = try TestDecode.station("""
        {"id":"evidence","name":"N","lat":28,"lon":-16,"lead":true,
         "price_evidence":[
          {"kind":"repsol_network_tariff","source":"Repsol","label":"Verified free",
           "text":"from €0.39/kWh; idle €0.10/min after 30 min grace",
           "source_url":"https://www.repsol.es/particulares/vehiculos/movilidad-electrica/",
           "source_updated_at":"2020-01-01T00:00:00Z","fetched_at":"2026-10-20T10:20:00Z",
           "price_per_kwh":0,"currency":"EUR","parking_price":"€2/hour",
           "tariff_details":{"future_component":{"fee":3}},
           "rates":[{"kind":"energy","label":"normal","min":0.36,"max":null,"from":false,"unit":"kWh","after_minutes":null},
                    {"kind":"energy","label":"fast","min":0.48,"max":null,"from":true,"unit":"kWh","after_minutes":null},
                    {"kind":"energy","label":"ultrafast","min":0.56,"max":null,"from":false,"unit":"kWh","after_minutes":null}]},
          {"kind":"electromaps_community","source":"Electromaps","label":"Live free",
           "text":"€0.25/kWh; €1 session fee","source_url":"https://www.electromaps.com/en/charging-stations/example",
           "source_updated_at":"2024-03-02T23:30:00-05:00","fetched_at":"2026-10-20T10:20:00Z",
           "price_per_kwh":0,"currency":"EUR",
           "rates":[{"kind":"energy","label":null,"min":0.35,"max":0.54,"from":false,"unit":"kWh","after_minutes":null}]},
          {"kind":"chargemap_pass","source":"Chargemap","label":"Operator direct tariff",
           "text":"€0.59 per started kWh; idle €0.15/min after 10 min grace",
           "source_url":"https://chargemap.com/en-gb/example","source_updated_at":"2021-02-03",
           "fetched_at":"2026-10-19T23:45:00-05:00","price_per_kwh":0,"currency":"EUR",
           "rates":[{"kind":"energy","label":"all","min":0.599,"max":null,"from":false,"unit":"started_kWh","after_minutes":null},
                    {"kind":"idle","label":null,"min":0.111,"max":null,"from":false,"unit":"minute","after_minutes":15}]}
         ]}
        """)
        return try XCTUnwrap(station.priceEvidence)
    }

    func testEachKnownKindUsesItsOwnLabelAndCorrectDate() throws {
        let evidence = try allEvidence()
        let items = evidence.compactMap(PriceEvidencePresentation.init)
        XCTAssertEqual(items.map(\.label), [
            "Repsol published tariff · station may differ",
            "Electromaps community price · 2024-03-02",
            "Price with Chargemap Pass"
        ])
        XCTAssertEqual(items.map(\.checkedText), ["Checked: 2026-10-20", nil, "Checked: 2026-10-19"])
        XCTAssertEqual(evidence[1].sourceUpdatedAt, "2024-03-02T23:30:00-05:00")
        XCTAssertEqual(evidence[1].fetchedAt, "2026-10-20T10:20:00Z")
        XCTAssertEqual(items.map(\.text), [
            "Normal €0.36 · Fast from €0.48 · Ultra-fast €0.56 per kWh",
            "€0.35–0.54 per kWh",
            "€0.599 per started kWh · idle €0.111/min after 15 min"
        ])
        for index in items.indices { XCTAssertNotEqual(items[index].text, evidence[index].text) }
        XCTAssertEqual(items[0].parkingText, "Parking: €2/hour")
        XCTAssertFalse(items[0].text.contains("€2/hour"), "Parking is a separate fact")
        XCTAssertEqual(Set(items.map(\.id)).count, 3)
    }

    func testMissingEmptyOrInvalidRatesUseShortFallbackNeverRawTextOrMetadata() throws {
        var evidence = try allEvidence()[0]
        for rates in [nil, [], [PriceEvidenceRate(kind: "energy", min: -0.1, from: false, unit: "kWh")]] as [[PriceEvidenceRate]?] {
            evidence.rates = rates
            XCTAssertEqual(PriceEvidencePresentation(evidence)?.text, "See source for prices")
        }
        evidence.rates = [PriceEvidenceRate(kind: "energy", min: 0.54, from: true, unit: "kWh")]
        XCTAssertEqual(PriceEvidencePresentation(evidence)?.text, "From €0.54 per kWh")
    }

    func testMissingNullAndEmptyEvidenceKeepOlderStationResponsesCompatible() throws {
        for field in ["", ",\"price_evidence\":null", ",\"price_evidence\":[]"] {
            let station = try TestDecode.station("""
            {"id":"older","name":"N","lat":28,"lon":-16,"price_min":0.35\(field)}
            """)
            XCTAssertTrue(station.priceEvidencePresentations.isEmpty)
            XCTAssertEqual(station.priceText, "€0.35")
        }
    }

    func testUnknownKindsAndFutureFieldsDoNotDiscardTheStationOrInventLabels() throws {
        let station = try TestDecode.station("""
        {"id":"future","name":"N","lat":28,"lon":-16,"future_station_field":{},
         "price_evidence":[{"kind":"future_price_kind","source":"Future","label":"Verified free",
           "text":"Unrecognized terms","source_url":"https://example.com/terms",
           "source_updated_at":null,"fetched_at":"2026-10-20T00:00:00Z",
           "price_per_kwh":0,"currency":"EUR","future_field":{"nested":[true,3]}}]}
        """)
        XCTAssertEqual(station.id, "future")
        XCTAssertEqual(station.priceEvidence?.first?.kind, "future_price_kind")
        XCTAssertTrue(station.priceEvidencePresentations.isEmpty)
        XCTAssertNil(station.priceMin)
        XCTAssertFalse(station.hasVerifiedPrice)
    }

    func testUnknownSourceDatesStayExplicitAndNeverUseTheFreshDownload() throws {
        var evidence = try allEvidence()[1]
        for date in [nil, "", "unknown", "2026-02-30", "2026-13-01", "2026-10-2", "0000-01-01"] as [String?] {
            evidence.sourceUpdatedAt = date
            let item = try XCTUnwrap(PriceEvidencePresentation(evidence))
            XCTAssertEqual(item.label, "Electromaps community price · Date unknown")
            XCTAssertNil(item.checkedText)
        }
        for index in [0, 2] {
            var published = try allEvidence()[index]
            published.fetchedAt = nil
            XCTAssertEqual(PriceEvidencePresentation(published)?.checkedText, "Checked: Date unknown")
        }
        XCTAssertEqual(PriceEvidencePresentation.sourceDate("2024-02-29T23:30:00Z"), "2024-02-29")
        XCTAssertNil(PriceEvidencePresentation.sourceDate("2025-02-29"))
    }

    func testIdentityFollowsSourceKindAndDatesInsteadOfArrayPosition() throws {
        let evidence = try allEvidence()
        XCTAssertEqual(evidence.reversed().map(\.id), Array(evidence.map(\.id).reversed()))
        var changed = evidence[0]
        changed.label = "Changed upstream wording"
        XCTAssertEqual(changed.id, evidence[0].id)
        changed.sourceUrl += "different"
        XCTAssertNotEqual(changed.id, evidence[0].id)
        changed = evidence[0]
        changed.kind = "chargemap_pass"
        XCTAssertNotEqual(changed.id, evidence[0].id)
        changed = evidence[0]
        changed.sourceUpdatedAt = "2026-10-01"
        XCTAssertNotEqual(changed.id, evidence[0].id)
        changed = evidence[0]
        changed.fetchedAt = "2026-10-21T00:00:00Z"
        XCTAssertNotEqual(changed.id, evidence[0].id)
    }

    func testSourceLinksUseTheExistingPublicURLSanitizer() throws {
        var evidence = try allEvidence()[0]
        XCTAssertEqual(evidence.safeSourceURL?.host, "www.repsol.es")
        for unsafe in ["javascript:alert(1)", "file:///source", "https://user:password@example.com/price"] {
            evidence.sourceUrl = unsafe
            XCTAssertNil(evidence.safeSourceURL)
            XCTAssertNil(PriceEvidencePresentation(evidence)?.sourceURL)
        }
    }

    func testNumericEvidenceNeverSuppliesPrimaryPricesFiltersVerificationOrEstimates() throws {
        var station = try TestDecode.station("""
        {"id":"unpriced","name":"N","lat":28,"lon":-16,"lead":true,
         "verification":{"price_verified":true},
         "connectors":[{"type":"Type 2","power_kw":22}]}
        """)
        station.priceEvidence = try allEvidence()
        XCTAssertEqual(station.priceEvidencePresentations.count, 3)
        XCTAssertNil(station.priceMin)
        XCTAssertNil(station.isFree)
        XCTAssertNil(station.isCheap)
        XCTAssertNil(station.chargeClaim)
        XCTAssertEqual(station.priceText, "—")
        XCTAssertNil(station.priceUnit)
        XCTAssertTrue(station.tariffNotes.isEmpty)
        XCTAssertFalse(station.hasVerifiedPrice)
        XCTAssertFalse(station.isRouteReady)
        XCTAssertEqual(station.decisionKind, .verify)

        let state = AppState()
        state.leads = [station]
        state.freeOnly = true
        XCTAssertTrue(state.filteredRows.isEmpty)
        state.freeOnly = false
        state.maxPrice = 0.25
        XCTAssertTrue(state.filteredRows.isEmpty)

        var vehicle = OwnedVehicle.manual()
        vehicle.batteryPercent = 20
        vehicle.batteryCapacityKwh = 75
        vehicle.connectors = ["Type 2"]
        let estimate = VehicleChargingEstimate(vehicle: vehicle, target: 80, station: station)
        XCTAssertNil(estimate.energyCostEUR, "A price-verification flag cannot turn numeric evidence into an estimate")
    }

    func testUnverifiedPriceAndStrayVerificationFlagDoNotHideTheRepsolGuide() throws {
        var station = try TestDecode.station("""
        {"id":"unverified","name":"N","lat":28,"lon":-16,"price_min":0.37}
        """)
        station.priceEvidence = try allEvidence()
        XCTAssertEqual(station.cardPriceEvidencePresentations.count, 3)
        station.priceMin = nil
        station.verification = try TestDecode.station("""
        {"id":"flag","name":"N","lat":28,"lon":-16,"verification":{"price_verified":true}}
        """).verification
        XCTAssertEqual(station.cardPriceEvidencePresentations.count, 3)
    }

    func testAllClaimsRemainAlongsideAnUnchangedVerifiedLivePrice() throws {
        var station = try TestDecode.station("""
        {"id":"official","name":"N","lat":28,"lon":-16,"lead":false,
         "price_min":0.59,"price_currency":"EUR","available":1,
         "verification":{"level":"live","live_verified":true,"price_verified":true},
         "decision":{"level":"go"},
         "connectors":[{"type":"Type 2","power_kw":22}]}
        """)
        let primaryPrice = station.priceText
        let decision = station.decisionKind
        let routeReady = station.isRouteReady
        station.priceEvidence = try allEvidence()
        XCTAssertEqual(station.priceEvidencePresentations.count, 3, "Details retain the Repsol guide")
        XCTAssertEqual(station.cardPriceEvidencePresentations.count, 2)
        XCTAssertFalse(station.cardPriceEvidencePresentations.contains { $0.label.hasPrefix("Repsol") })
        XCTAssertEqual(station.priceText, primaryPrice)
        XCTAssertEqual(station.priceText, "€0.59")
        XCTAssertEqual(station.priceMin, 0.59)
        XCTAssertTrue(station.hasVerifiedPrice)
        XCTAssertEqual(station.decisionKind, decision)
        XCTAssertEqual(station.isRouteReady, routeReady)

        var vehicle = OwnedVehicle.manual()
        vehicle.batteryPercent = 20
        vehicle.batteryCapacityKwh = 75
        vehicle.connectors = ["Type 2"]
        let estimate = VehicleChargingEstimate(vehicle: vehicle, target: 80, station: station)
        XCTAssertEqual(try XCTUnwrap(estimate.energyCostEUR), 29.5, accuracy: 0.001)
    }
}
