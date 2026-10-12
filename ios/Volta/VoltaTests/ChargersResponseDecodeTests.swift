import XCTest
@testable import Volta

/// CORR-07 runtime proof: Models.swift's `@LossyArray` property wrapper must decode
/// `ChargersResponse.stations`/`.leads` leniently at the ARRAY boundary, dropping a coord-less
/// element instead of throwing and blanking the whole array. `tests/test_ios_design_contract.py`
/// pins this as a Python text-contract (the wrapper's source exists, is wired up correctly);
/// these are the runtime assertions that it actually BEHAVES that way when fed a real JSON
/// payload shaped like server.py's own `_normalize_lead` output (a lead kept by name only, no
/// lat/lon keys at all -- not `null`, simply absent).
final class ChargersResponseDecodeTests: XCTestCase {
    func testStationsArrayDropsOneCoordlessElementAmongValidOnes() throws {
        let json = """
        {
          "stations": [
            {"id": "s1", "name": "Station One", "lat": 39.47, "lon": -0.38, "price_min": 0.21},
            {"id": "s1-bad", "name": "No Coords Station"},
            {"id": "s2", "name": "Station Two", "lat": 40.4168, "lon": -3.7038, "is_free": true}
          ],
          "leads": []
        }
        """
        let resp = try TestDecode.chargersResponse(json)
        // N=3 elements submitted, 1 malformed (missing required lat/lon) -> N-1=2 survive.
        XCTAssertEqual(resp.stations.count, 2)
        XCTAssertEqual(Set(resp.stations.map(\.id)), Set(["s1", "s2"]))

        let s1 = try XCTUnwrap(resp.stations.first { $0.id == "s1" })
        XCTAssertEqual(s1.lat, 39.47, accuracy: 0.0001)
        XCTAssertEqual(s1.lon, -0.38, accuracy: 0.0001)

        let s2 = try XCTUnwrap(resp.stations.first { $0.id == "s2" })
        XCTAssertEqual(s2.lat, 40.4168, accuracy: 0.0001)
        XCTAssertEqual(s2.lon, -3.7038, accuracy: 0.0001)
    }

    func testLeadsArrayDropsOneCoordlessElementAmongValidOnes() throws {
        let json = """
        {
          "stations": [],
          "leads": [
            {"id": "l1", "name": "Lead One", "lat": 41.3874, "lon": 2.1686, "lead": true},
            {"id": "l1-bad", "name": "No Coords Lead", "lead": true},
            {"id": "l2", "name": "Lead Two", "lat": 37.3891, "lon": -5.9845, "lead": true}
          ]
        }
        """
        let resp = try TestDecode.chargersResponse(json)
        XCTAssertEqual(resp.leads.count, 2)
        XCTAssertEqual(Set(resp.leads.map(\.id)), Set(["l1", "l2"]))

        let l1 = try XCTUnwrap(resp.leads.first { $0.id == "l1" })
        XCTAssertEqual(l1.lat, 41.3874, accuracy: 0.0001)
        XCTAssertEqual(l1.lon, 2.1686, accuracy: 0.0001)
        XCTAssertTrue(l1.isLead)
    }

    func testAllCoordlessArrayDecodesToEmptyWithoutThrowing() throws {
        // Stronger than the single-bad-element case: proves the unkeyed container's cursor
        // never stalls even when EVERY element fails, i.e. LossyArray truly never throws.
        let json = """
        {
          "stations": [
            {"id": "only-bad-1", "name": "No Coords"},
            {"id": "only-bad-2", "name": "Also No Coords"}
          ],
          "leads": []
        }
        """
        let resp = try TestDecode.chargersResponse(json)
        XCTAssertEqual(resp.stations.count, 0)
    }
}
