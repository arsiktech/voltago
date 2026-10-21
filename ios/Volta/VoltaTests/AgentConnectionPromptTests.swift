import Foundation
import UIKit
import XCTest
@testable import Volta

final class AgentConnectionPromptTests: XCTestCase {
    func testSharedPromptIsIncludedInTheAppBundleWithSetupAndChargerTools() throws {
        let prompt = try XCTUnwrap(AgentConnectionPrompt.load())
        XCTAssertEqual(prompt, prompt.trimmingCharacters(in: .whitespacesAndNewlines))
        XCTAssertNotNil(Bundle.main.url(forResource: "agent_connection_prompt", withExtension: "txt"))
        for value in ["https://voltago.app/mcp", "Streamable HTTP", "claude mcp add --transport http",
                      "get_data_status", "find_chargers", "get_charger"] {
            XCTAssertTrue(prompt.contains(value), value)
        }
        XCTAssertTrue(prompt.contains("Do not claim the connection is ready until a tool call succeeds."))
        XCTAssertTrue(prompt.contains("hourly rates, minimum charges"))
    }

    @MainActor
    func testCopyPreservesTheCompleteBundledPrompt() throws {
        let prompt = try XCTUnwrap(AgentConnectionPrompt.load())
        let pasteboard = UIPasteboard.withUniqueName()
        defer { UIPasteboard.remove(withName: pasteboard.name) }
        XCTAssertTrue(AgentConnectionPrompt.copy(prompt, to: pasteboard))
        XCTAssertEqual(pasteboard.string, prompt)
    }

    func testMissingResourceDoesNotProduceAnIncompletePrompt() {
        XCTAssertNil(AgentConnectionPrompt.load(from: Bundle(for: Self.self)))
    }
}
