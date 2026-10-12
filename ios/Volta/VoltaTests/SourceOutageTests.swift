import XCTest
@testable import Volta

/// `source_reported_unavailable` (server, since 7b2733b): the source itself reports a charger
/// out of service. The app must decode it, and a row without it must stay unaffected.
final class SourceOutageTests: XCTestCase {
    func testOutageDecodesAndMarksTheStation() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "Feníe — X", "lat": 38.9, "lon": -0.2, "is_free": true, "price_min": 0,
         "lead": true, "decision": {"level": "avoid", "label": "Unavailable"},
         "source_reported_unavailable": {"source": "Feníe Energía public map (Gratuitos)", "field": "estado",
                                         "value": 4, "summary": "Feníe reports this charger unavailable."}}
        """)
        XCTAssertTrue(st.isReportedOutOfService)
        XCTAssertEqual(st.sourceReportedUnavailable?.source, "Feníe Energía public map (Gratuitos)")
        XCTAssertEqual(st.decisionKind, .avoid)
        XCTAssertFalse(st.isRouteReady, "An out-of-service charger is never route-ready")
    }

    func testRowWithoutOutageIsUnaffected() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "is_free": true}
        """)
        XCTAssertFalse(st.isReportedOutOfService)
        XCTAssertNil(st.sourceReportedUnavailable)
    }

    func testMalformedOutageDoesNotDropTheRow() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "source_reported_unavailable": {}}
        """)
        XCTAssertTrue(st.isReportedOutOfService, "An empty outage object still fails closed")
    }
}
