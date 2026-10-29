import Foundation
import SwiftUI
import UIKit

/// The web and native clients copy the same bundled setup prompt, trimming only outer whitespace.
enum AgentConnectionPrompt {
    static func load(from bundle: Bundle = .main) -> String? {
        guard let url = bundle.url(forResource: "agent_connection_prompt", withExtension: "txt"),
              let prompt = try? String(contentsOf: url, encoding: .utf8),
              !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return nil }
        return prompt.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    @MainActor
    static func copy(_ prompt: String, to pasteboard: UIPasteboard) -> Bool {
        pasteboard.string = prompt
        return pasteboard.string == prompt
    }
}

/// One tap copies setup instructions. Only a failed copy reveals a manual-copy fallback.
struct AIConnectionPrompt: View {
    @State private var copySucceeded = false
    @State private var copyError: String?
    @State private var manualPrompt: String?

    private var title: String {
        copySucceeded ? String(localized: "Prompt copied") : String(localized: "Copy AI prompt")
    }

    private var subtitle: String { String(localized: "Paste into your AI chat") }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(action: copyPrompt) {
                HStack(spacing: 7) {
                    Image(systemName: copySucceeded ? "checkmark" : "doc.on.doc")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 16, height: 16)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 1) {
                        ZStack(alignment: .leading) {
                            // Both states reserve the same width, including translated labels.
                            Text("Copy AI prompt").hidden().accessibilityHidden(true)
                            Text("Prompt copied").hidden().accessibilityHidden(true)
                            Text(title)
                        }
                        .font(.caption.weight(.semibold))
                        Text(subtitle).font(.caption2)
                    }
                }
                .foregroundStyle(Theme.actionInk)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .frame(minWidth: 44, minHeight: 44)
                .background(Theme.surface, in: .capsule)
                .overlay { Capsule().strokeBorder(Theme.hairlineStrong, lineWidth: 1) }
                .padding(.vertical, 2)
                .contentShape(.capsule)
            }
            .buttonStyle(VoltaPressButtonStyle())
            .accessibilityLabel(Text(verbatim: title + ". " + subtitle))
            .accessibilityHint("Copies a setup prompt for ChatGPT, Claude, Grok or another AI chat")
            .accessibilityIdentifier("copyAIPromptButton")

            if let copyError {
                VStack(alignment: .leading, spacing: 6) {
                    Text(copyError)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if let manualPrompt {
                        DisclosureGroup("Copy manually") {
                            ScrollView {
                                Text(verbatim: manualPrompt)
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .accessibilityLabel("AI setup prompt")
                            }
                            .frame(height: 160)
                        }
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("manualAIPromptCopy")
                    }
                }
                .frame(maxWidth: 220)
            }
        }
    }

    private func copyPrompt() {
        copyError = nil
        manualPrompt = nil
        guard let prompt = AgentConnectionPrompt.load() else {
            copySucceeded = false
            let error = String(localized: "The AI prompt is unavailable. Tap again to retry.")
            copyError = error
            announce(error)
            return
        }
        copySucceeded = AgentConnectionPrompt.copy(prompt, to: .general)
        if copySucceeded {
            announce(String(localized: "Prompt copied. Paste it into your AI chat."))
        } else {
            let error = String(localized: "Couldn't copy. Tap again or copy the prompt manually.")
            copyError = error
            manualPrompt = prompt
            announce(error)
        }
    }

    private func announce(_ message: String) {
        guard UIAccessibility.isVoiceOverRunning else { return }
        UIAccessibility.post(notification: .announcement, argument: message)
    }
}

#Preview {
    AIConnectionPrompt()
}
