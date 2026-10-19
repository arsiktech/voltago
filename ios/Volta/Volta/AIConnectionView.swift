import SwiftUI
import UIKit

/// A persistent discovery action, with no extra row or notification permission.
struct AIConnectionPrompt: View {
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text("Connect AI")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.actionInk)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .frame(minWidth: 44, minHeight: 44)
                .background(Theme.surface, in: .capsule)
                .overlay { Capsule().strokeBorder(Theme.hairlineStrong, lineWidth: 1) }
                .contentShape(.capsule)
        }
        .buttonStyle(VoltaPressButtonStyle())
        .accessibilityLabel("Connect AI agent")
        .accessibilityHint("Opens instructions to add Volta Go to your AI agent")
        .accessibilityIdentifier("connectAIAgentButton")
    }
}

/// Setup is completed in the agent. Copying the address never implies a connection.
struct AIConnectionView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var copySucceeded: Bool?

    private static let endpoint = "https://voltago.app/mcp"
    private static let claudeCodeCommand = "claude mcp add --transport http voltago https://voltago.app/mcp"

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Ask your agent about nearby chargers, prices and charging speeds.")
                        .foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Section {
                    Text("Add a remote MCP server in your agent’s integrations, then paste this address.")
                        .fixedSize(horizontal: false, vertical: true)

                    Text(verbatim: Self.endpoint)
                        .font(.system(.subheadline, design: .monospaced))
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityLabel("MCP server address")
                        .accessibilityValue(Self.endpoint)

                    Button(action: copyAddress) {
                        Label(copySucceeded == true ? String(localized: "Copied") : String(localized: "Copy address"),
                              systemImage: copySucceeded == true ? "checkmark" : "doc.on.doc")
                            .frame(maxWidth: .infinity, minHeight: 44)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .buttonStyle(VoltaActionButtonStyle(prominent: true))
                    .accessibilityIdentifier("copyMCPAddressButton")

                    if copySucceeded == false {
                        Text("Couldn’t copy. Select the address above and copy it.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    Text("Use an agent that supports remote MCP (Streamable HTTP), such as Claude Code.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } header: {
                    Text("MCP server")
                } footer: {
                    Text("No login or API key required.")
                }

                Section("Claude Code") {
                    Text("For Claude Code, run this in your terminal:")
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(verbatim: Self.claudeCodeCommand)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityLabel("Claude Code command")
                        .accessibilityValue(Self.claudeCodeCommand)
                }

                Section("Read-only access") {
                    Text("Your agent can search chargers and read their details. It cannot start charging, take payments or submit reports.")
                        .fixedSize(horizontal: false, vertical: true)
                    Text("Live availability depends on source coverage. Directory listings do not show whether a charger is available or occupied.")
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.canvas)
            .navigationTitle("Connect your AI agent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .frame(minWidth: 44, minHeight: 44)
                }
            }
        }
        .presentationBackground(Theme.canvas)
        .presentationDragIndicator(.visible)
        .accessibilityIdentifier("aiConnectionSheet")
    }

    private func copyAddress() {
        let pasteboard = UIPasteboard.general
        pasteboard.string = Self.endpoint
        let didCopy = pasteboard.string == Self.endpoint
        copySucceeded = didCopy
        guard UIAccessibility.isVoiceOverRunning else { return }
        let message = didCopy
            ? String(localized: "Address copied")
            : String(localized: "Couldn’t copy. Select the address above and copy it.")
        UIAccessibility.post(notification: .announcement, argument: message)
    }
}

#Preview {
    AIConnectionView()
}
