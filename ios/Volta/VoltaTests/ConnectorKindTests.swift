import XCTest
@testable import Volta

final class ConnectorKindTests: XCTestCase {
    func testCCSVariantsNeverMatchEachOtherOrAnUnknownGenericLabel() {
        XCTAssertTrue(ConnectorKind.matches("CCS Type 1", selection: "CCS1"))
        XCTAssertTrue(ConnectorKind.matches("CCS Type 2", selection: "CCS2"))
        XCTAssertFalse(ConnectorKind.matches("CCS1", selection: "CCS2"))
        XCTAssertFalse(ConnectorKind.matches("CCS2", selection: "CCS1"))
        XCTAssertFalse(ConnectorKind.matches("CCS", selection: "CCS2"))
        XCTAssertFalse(ConnectorKind.matches("CCS Type 1", selection: "Type 1"))
    }

    func testWorldwideStandardsStayDistinct() {
        XCTAssertTrue(ConnectorKind.matches("SAE J1772", selection: "Type 1"))
        XCTAssertTrue(ConnectorKind.matches("SAE J3400", selection: "NACS"))
        XCTAssertTrue(ConnectorKind.matches("GB/T (AC)", selection: "GB/T AC"))
        XCTAssertFalse(ConnectorKind.matches("GB/T DC", selection: "GB/T AC"))
        XCTAssertFalse(ConnectorKind.matches("Tesla", selection: "NACS"))
    }

    @MainActor
    func testChoosingCCS2KeepsOnlyTheMatchingStations() throws {
        let ccs1 = try TestDecode.station("""
        {"id":"us","name":"US plug","lat":37.7,"lon":-122.4,"lead":true,
         "connector_labels":["CCS1"]}
        """)
        let ccs2 = try TestDecode.station("""
        {"id":"eu","name":"European plug","lat":39.4,"lon":-0.3,"lead":true,
         "connectors":[{"type":"CCS2"}]}
        """)
        let state = AppState()
        state.leads = [ccs1, ccs2]
        state.connectorTypes = ["CCS2"]
        XCTAssertEqual(state.filteredRows.map(\.id), ["eu"])
        state.connectorTypes = ["CCS1"]
        XCTAssertEqual(state.filteredRows.map(\.id), ["us"])
    }
}
