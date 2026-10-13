import Foundation
import XCTest
@testable import Volta

@MainActor
final class ChargerReferenceTests: XCTestCase {
    let revision = String(repeating: "a", count: 64)
    func catalogue(_ state: String = "alias") -> ChargerReferences {
        ChargerReferences(version: 1, revision: revision, entries: [
            .init(key: ChargerReferences.key("old"), state: state, canonicalId: state == "alias" ? "owner" : nil)
        ])
    }
    func station() throws -> Station {
        try TestDecode.station("""
        {"id":"owner","name":"Current charger","lat":39,"lon":-0.5,"reference_revision":"\(revision)","lead":true}
        """)
    }
    func testSharedHashVectors() {
        XCTAssertEqual(ChargerReferences.key("old"), "cc44436bce2af9dce4f4e0eaaabfa325587c667ef23dbef59d9ac70c378700b4")
        XCTAssertEqual(ChargerReferences.key("café/Зарядка"), "4b8eabe483059336d14d3db10d39d71a223e7c6847f24936f8c69c638e8cc6a8")
    }
    func testAliasResolvesOnlyWithinSameRevision() throws {
        var row = try station()
        XCTAssertEqual(catalogue().resolve("old", rows: [row])?.id, "owner")
        row.referenceRevision = String(repeating: "b", count: 64)
        XCTAssertNil(catalogue().resolve("old", rows: [row]))
    }
    func testUnavailableStatesHaveNoStationOrRoute() throws {
        for state in ["withheld", "unresolved"] {
            XCTAssertNil(catalogue(state).resolve("old", rows: [try station()]))
            XCTAssertEqual(catalogue(state).state(for: "old"), state)
        }
    }
    func testDuplicateOwnersAndCyclesFailClosed() throws {
        var c = catalogue()
        c.entries.append(c.entries[0])
        XCTAssertNil(c.resolve("old", rows: [try station()]))
        c = catalogue()
        c.entries.append(.init(key: ChargerReferences.key("owner"), state: "alias", canonicalId: "old"))
        XCTAssertNil(c.resolve("old", rows: [try station()]))
    }
    func testFavoriteFilteringPreservesIDsUntilExplicitRemove() throws {
        let suite = "VoltaTests.ReferenceMigration.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        let prior = FavoritesStore.defaults
        FavoritesStore.defaults = defaults
        defer { FavoritesStore.defaults = prior; defaults.removePersistentDomain(forName: suite) }
        let original = FavoriteRecord(id: "old", name: "Saved locally", savedAt: Date(timeIntervalSince1970: 1))
        FavoritesStore.save([original])
        let state = AppState()
        let row = try station()
        state.references = catalogue()
        state.leads = [row]
        state.showingSavedOnly = true
        XCTAssertEqual(state.filteredRows.map(\.id), ["owner"])
        XCTAssertTrue(state.isFavorite(row))
        XCTAssertEqual(FavoritesStore.load(), [original])
        state.references = catalogue("withheld")
        XCTAssertTrue(state.filteredRows.isEmpty)
        XCTAssertEqual(state.unavailableFavorites, [original])
        XCTAssertEqual(FavoritesStore.load(), [original])
        state.references = catalogue()
        state.toggleFavorite(row)
        XCTAssertTrue(FavoritesStore.load().isEmpty)
    }
}
