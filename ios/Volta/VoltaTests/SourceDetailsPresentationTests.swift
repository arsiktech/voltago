import XCTest
@testable import Volta

final class SourceDetailsPresentationTests: XCTestCase {
    func testHourlyChargeKeepsItsUnitAndFreeHourCondition() throws {
        let station = try TestDecode.station("""
        {"id":"hourly","name":"N","lat":28,"lon":-16,"lead":true,
         "tariff_details":{"hour_eur":2,"unconditional_free":false,"conditions":["First hour free"]}}
        """)
        XCTAssertEqual(station.priceText, "€2.00")
        XCTAssertEqual(station.priceUnit, "/hour")
        XCTAssertEqual(station.tariffNotes, ["First hour free"])
        XCTAssertNil(station.priceMin, "An hourly charge is not an energy price")
        XCTAssertFalse(station.isRouteReady)
        XCTAssertFalse(station.hasVerifiedPrice)
        XCTAssertEqual(station.displayPriceText, "€2.00")
        XCTAssertEqual(station.chargingPriceLabel, "Source price")
    }

    func testEnergyChargeKeepsMinimumAndExtraTimeConditions() throws {
        let station = try TestDecode.station("""
        {"id":"composite","name":"N","lat":28,"lon":-16,"price_min":0.35,
         "tariff_details":{"energy_eur_per_kwh":0.35,"minimum_eur":2,"hour_eur":1,
                           "conditions":["Customers only","Customers only"]}}
        """)
        XCTAssertEqual(station.priceText, "€0.35")
        XCTAssertEqual(station.priceUnit, "/kWh")
        XCTAssertEqual(station.tariffDetails?.energyEurPerKwh, 0.35)
        XCTAssertEqual(station.tariffNotes, ["Minimum charge €2.00", "Charging time €1.00/hour", "Customers only"])
    }

    func testConditionalEnergyAndSessionMinimumDoNotBecomeFreeEnergy() throws {
        let station = try TestDecode.station("""
        {"id":"conditional","name":"N","lat":28,"lon":-16,
         "tariff_details":{"energy_eur_per_kwh":0,"unconditional_free":false,
                           "components":[{"type":"MINIMUM","price":2,"unit":"session"},
                                         {"type":"FLAT","price":0.5,"unit":"session"}],
                           "conditions":["With a purchase"]}}
        """)
        XCTAssertEqual(station.priceText, "—")
        XCTAssertNil(station.priceUnit)
        XCTAssertEqual(station.tariffNotes, ["Minimum charge €2.00", "Session fee €0.50", "With a purchase"])
    }

    func testConnectorQuantityAndPowerStaySourceFacts() throws {
        let station = try TestDecode.station("""
        {"id":"hardware","name":"N","lat":28,"lon":-16,"lead":true,
         "connectors":[{"label":"Type 2","count":3,"power_kw":7.4,"source_url":"https://canariasterecarga.com/es/p123/"},
                       {"label":"Unknown connector","count":null},
                       {"label":"CCS2","source_url":"file:///private/source"}]}
        """)
        let connectors = try XCTUnwrap(station.connectors)
        XCTAssertEqual(connectors[0].inventoryLabel, "3 × Type 2")
        XCTAssertEqual(connectors[0].powerText, "7.4 kW")
        XCTAssertEqual(connectors[0].safeSourceURL?.absoluteString, "https://canariasterecarga.com/es/p123/")
        XCTAssertEqual(connectors[1].inventoryLabel, "Unknown connector")
        XCTAssertNil(connectors[1].count)
        XCTAssertEqual(connectors[2].inventoryLabel, "CCS2")
        XCTAssertNil(connectors[2].safeSourceURL)
    }

    func testSourceDetailsAndPhotoLinksDoNotGrantImageDisplayRights() throws {
        let station = try TestDecode.station("""
        {"id":"details","name":"N","lat":28,"lon":-16,
         "source_details":{"source_url":"https://canariasterecarga.com/es/p123/","description":"Parking level -1",
                           "opening_hours":"08:00–22:00","access":"Customers only",
                           "contact":{"phone":"123456789","website":"javascript:alert(1)"}},
         "media":{"photos":[{"url":"https://canariasterecarga.com/photo.jpg",
                                "source_url":"https://canariasterecarga.com/es/p123/","caption":"Entrance"}]}}
        """)
        XCTAssertEqual(station.sourceDetails?.openingHours, "08:00–22:00")
        XCTAssertEqual(station.sourceDetails?.access, "Customers only")
        XCTAssertEqual(station.sourceDetails?.contact?.phone, "123456789")
        XCTAssertNotNil(station.sourceDetails?.safeSourceURL)
        XCTAssertNil(station.sourceDetails?.contact?.safeWebsiteURL)
        XCTAssertEqual(station.media?.photos?.first?.caption, "Entrance")
        XCTAssertNotNil(station.media?.photos?.first?.safeSourceURL)
        XCTAssertNil(station.media?.photos?.first?.safeURL)
        XCTAssertNil(station.firstPublicPhoto)
    }

    func testDifferentSourcePageInventoryDoesNotReplaceSelectedHardware() throws {
        let station = try TestDecode.station("""
        {"id":"two-sources","name":"N","lat":28,"lon":-16,"lead":true,
         "connectors":[{"label":"Type 2","count":2,"power_kw":22}],
         "source_details":{"source_name":"Canarias te recarga","connector_count":9,"max_power_kw":50,
                           "connectors":[{"label":"CCS2","count":1,"power_kw":50},
                                         {"label":"CHAdeMO","count":1,"power_kw":50},
                                         {"label":"Type 2","count":1,"power_kw":43},
                                         {"label":"Type 2","count":6,"power_kw":22}]}}
        """)
        XCTAssertEqual(station.connectors?.first?.count, 2)
        XCTAssertEqual(station.sourceDetails?.sourceName, "Canarias te recarga")
        XCTAssertEqual(station.sourceDetails?.connectorCount, 9)
        XCTAssertEqual(station.sourceDetails?.maxPowerKw, 50)
        XCTAssertEqual(station.sourcePageConnectors.map(\.inventoryText), [
            "1 × CCS2 · 50 kW", "1 × CHAdeMO · 50 kW", "1 × Type 2 · 43 kW", "6 × Type 2 · 22 kW"
        ])
        XCTAssertFalse(station.canShowLiveAvailability)
    }

    func testMatchingSourcePageInventoryIsNotRepeatedForDifferentOrderOrMetadata() throws {
        let station = try TestDecode.station("""
        {"id":"same-hardware","name":"N","lat":28,"lon":-16,
         "connectors":[{"label":"Type 2","count":2,"power_kw":22.1},{"label":"CCS2","power_kw":50}],
         "source_details":{"connectors":[{"label":"CCS2","power_kw":50,"source_url":"https://canariasterecarga.com/es/p123/"},
                                         {"label":"Type 2","count":2,"power_kw":22.1,"connector_id":"directory-row"}]}}
        """)
        XCTAssertEqual(station.connectors?.first?.inventoryText, "2 × Type 2 · 22.1 kW")
        XCTAssertTrue(station.sourcePageConnectors.isEmpty)
    }

    func testContactAndBookingTextKeepsUnknownPrice() throws {
        for text in [
            "Para más información sobre las tarifas llame al 630 034 636",
            "Call reception for tariff information",
            "Contact us for free charging information",
            "0 € Reservar"
        ] {
            let payload: [String: Any] = ["id": "contact", "name": "N", "lat": 28, "lon": -16,
                                          "lead": true, "tariff_details": ["text": text]]
            let data = try JSONSerialization.data(withJSONObject: payload)
            let station = try TestDecode.station(try XCTUnwrap(String(data: data, encoding: .utf8)))
            XCTAssertEqual(station.displayPriceText, "Unknown", text)
            XCTAssertEqual(station.chargingPriceLabel, "Charging price", text)
            XCTAssertFalse(station.hasVerifiedPrice)
            XCTAssertFalse(station.isRouteReady)
        }
    }

    func testExplicitSourceRateTextIsRetained() throws {
        for text in ["€0.35/kWh", "2 EUR/hour after the first hour"] {
            let payload: [String: Any] = ["id": "rate", "name": "N", "lat": 28, "lon": -16,
                                          "lead": true, "tariff_details": ["text": text]]
            let data = try JSONSerialization.data(withJSONObject: payload)
            let station = try TestDecode.station(try XCTUnwrap(String(data: data, encoding: .utf8)))
            XCTAssertEqual(station.displayPriceText, text)
            XCTAssertEqual(station.chargingPriceLabel, "Source price")
        }
    }

    func testConditionalTariffTextAndUnknownPriceStayDistinctFromVerifiedFreeEnergy() throws {
        let conditional = try TestDecode.station("""
        {"id":"condition","name":"N","lat":28,"lon":-16,"lead":true,
         "tariff_details":{"text":"Free with a purchase; otherwise ask at reception","conditions":["Customers only"]}}
        """)
        XCTAssertEqual(conditional.displayPriceText, "Free with a purchase; otherwise ask at reception")
        XCTAssertEqual(conditional.chargingPriceLabel, "Source price")
        XCTAssertEqual(conditional.tariffNotes, ["Customers only"])
        XCTAssertFalse(conditional.hasVerifiedPrice)
        XCTAssertFalse(conditional.isRouteReady)

        let unknown = try TestDecode.station("""
        {"id":"unknown","name":"N","lat":28,"lon":-16}
        """)
        XCTAssertEqual(unknown.displayPriceText, "Unknown")
        XCTAssertEqual(unknown.chargingPriceLabel, "Charging price")
        XCTAssertNil(unknown.priceUnit)

        let verified = try TestDecode.station("""
        {"id":"free","name":"N","lat":28,"lon":-16,"price_min":0,
         "verification":{"price_verified":true}}
        """)
        XCTAssertEqual(verified.displayPriceText, "Free")
        XCTAssertEqual(verified.chargingPriceLabel, "Charging price")
    }

    func testNamedStationSourceOpensExactChargerInsteadOfPrimaryHomepage() throws {
        let station = try TestDecode.station("""
        {"id":"exact","name":"N","lat":28,"lon":-16,"lead":true,
         "primary_source":{"name":"Directory page","kind":"provider_homepage","url":"https://canariasterecarga.com/"},
         "station_source":{"name":"Directory page","kind":"station_page","url":"https://canariasterecarga.com/es/mapa/p53018/"}}
        """)
        XCTAssertEqual(station.sourceURL?.absoluteString, "https://canariasterecarga.com/es/mapa/p53018/")
        XCTAssertEqual(station.sourceActionLabel, "Open Canarias te recarga")
        XCTAssertEqual(station.primarySource?.url, "https://canariasterecarga.com/", "Broader evidence is retained independently")
        XCTAssertFalse(station.isRouteReady)
    }

    func testSavedDetailPageFallbackNamesTheSourceAndAvoidsGenericHomepages() throws {
        let station = try TestDecode.station("""
        {"id":"saved","name":"N","lat":28,"lon":-16,
         "primary_source":{"name":"Directory page","kind":"directory","url":"https://canariasterecarga.com/es/mapa/"},
         "source_details":{"source_name":"Canarias te recarga public map","source_url":"https://canariasterecarga.com/es/p00246/"}}
        """)
        XCTAssertEqual(station.sourceURL?.absoluteString, "https://canariasterecarga.com/es/p00246/")
        XCTAssertEqual(station.sourceActionLabel, "Open Canarias te recarga")

        let generic = try TestDecode.station("""
        {"id":"generic","name":"N","lat":28,"lon":-16,
         "source_url":"https://canariasterecarga.com/es/mapa/",
         "primary_source":{"name":"Directory page","kind":"station_page","url":"https://canariasterecarga.com/"}}
        """)
        XCTAssertNil(generic.sourceURL, "A homepage does not become a station link from its metadata label")
        XCTAssertEqual(generic.sourceActionLabel, "Source unavailable")
    }

    func testExactSourceSelectionRejectsUnsafeURLsAndKeepsSpecificStartLinks() throws {
        let station = try TestDecode.station("""
        {"id":"start","name":"N","lat":28,"lon":-16,
         "station_source":{"name":"Gaia","kind":"start_page","url":"https://partner-next.gaiacharge.com/cpo/jump/site-1/connector-2"}}
        """)
        XCTAssertEqual(station.sourceURL?.absoluteString, "https://partner-next.gaiacharge.com/cpo/jump/site-1/connector-2")
        XCTAssertEqual(station.sourceActionLabel, "Start/check Gaia")

        let unsafe = try TestDecode.station("""
        {"id":"unsafe","name":"N","lat":28,"lon":-16,
         "station_source":{"name":"Unsafe","kind":"station_page","url":"javascript:alert(1)"},
         "sources":[{"name":"OpenStreetMap","kind":"map_object","url":"https://www.openstreetmap.org/node/1234"}]}
        """)
        XCTAssertEqual(unsafe.sourceURL?.absoluteString, "https://www.openstreetmap.org/node/1234")
        XCTAssertEqual(unsafe.sourceActionLabel, "Open OpenStreetMap")
    }

    func testEverySupportedProviderRetainsItsExactChargerLink() throws {
        let cases = [
            ("https://map.openchargemap.io/?mode=details&id=123", "Open OpenChargeMap"),
            ("https://map.openchargemap.io/?mode=details&poi=123", "Open OpenChargeMap"),
            ("https://recarga.fenieenergia.es/punto-recarga/ABC123", "Start/check Feníe Energía"),
            ("https://www.tesla.com/es_es/findus/location/supercharger/valencia", "Open Tesla"),
            ("https://www.telpark.com/es/ciudades/madrid/plaza-mayor/", "Open Telpark"),
            ("https://www.interparking.es/es/aparcamientos/madrid/example/", "Open Interparking"),
            ("https://chargemap.com/en-gb/charging-stations/123", "Open Chargemap")
        ]
        for (url, label) in cases {
            let station = try TestDecode.station("""
            {"id":"exact","name":"N","lat":28,"lon":-16,
             "station_source":{"name":"Directory page","kind":"station_page","url":"\(url)"}}
            """)
            XCTAssertEqual(station.sourceURL?.absoluteString, url)
            XCTAssertEqual(station.sourceActionLabel, label)
        }
    }

    func testExactMetadataCannotTurnGenericRootsFeedsOrKnownWrongRoutesIntoStationActions() {
        for url in [
            "https://example.org/?id=123", "https://example.org/map?id=123",
            "https://example.org/feed/data.csv", "https://example.org/regulations/prices.pdf",
            "https://chargemap.com/index.html", "https://chargemap.com/contact.html",
            "https://canariasterecarga.com/es/mapa/?id=123", "https://www.openstreetmap.org/search?id=123",
            "https://map.openchargemap.io/?mode=details", "https://map.openchargemap.io/?mode=details&id=123&poi=456",
            "https://recarga.fenieenergia.es/punto-recarga/todos", "https://www.tesla.com/es_es/findus"
        ] {
            let source = SourceRef(name: "Provider", url: url, kind: "station_page")
            XCTAssertNil(source.exactStationURL, url)
        }
    }

    func testGenericReferencesUseExistingSameSourceChargerButRegulationsKeepTheirURLs() throws {
        let station = try TestDecode.station("""
        {"id":"references","name":"N","lat":28,"lon":-16,
         "source_url":"https://canariasterecarga.com/es/mapa/",
         "station_source":{"name":"Canarias te recarga","kind":"station_page","url":"https://canariasterecarga.com/es/p123/"}}
        """)
        let charger = try XCTUnwrap(station.sourceReference("https://canariasterecarga.com/es/mapa/", preferredName: "Directory page"))
        XCTAssertEqual(charger.url.absoluteString, "https://canariasterecarga.com/es/p123/")
        XCTAssertEqual(charger.label, "Open Canarias te recarga")

        let regulationURL = "https://canariasterecarga.com/regulations/prices.pdf"
        let regulation = try XCTUnwrap(station.sourceReference(regulationURL, preferredName: "Directory page", tariff: true))
        XCTAssertEqual(regulation.url.absoluteString, regulationURL)
        XCTAssertEqual(regulation.label, "Tariff reference: Canarias te recarga")

        let mapURL = "https://www.mapareve.es/"
        let general = try XCTUnwrap(station.sourceReference(mapURL, preferredName: "Live data"))
        XCTAssertEqual(general.url.absoluteString, mapURL)
        XCTAssertEqual(general.label, "General reference: MapaREVE")
        XCTAssertNil(station.sourceReference("javascript:alert(1)"))
    }
}
