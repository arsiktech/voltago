import SwiftUI

enum LegalDocument: String, Identifiable, CaseIterable {
    case terms, privacy

    var id: String { rawValue }

    var title: String {
        switch self {
        case .terms: String(localized: "Terms of Use")
        case .privacy: String(localized: "Privacy")
        }
    }

    var sections: [(title: String, body: String)] {
        switch self {
        case .terms:
            [
                (String(localized: "Using Volta"), String(localized: "These terms apply when you use Volta. Availability, price, parking, and access can change; verify the public source and onsite conditions before routing or charging.")),
                (String(localized: "What Volta provides"), String(localized: "Volta helps you compare public charger evidence. Availability, price, parking, and access can change; verify the station before routing or charging.")),
                (String(localized: "Subscriptions"), String(localized: "Volta Plus is account-less and tied to the Apple ID used for App Store purchases. Apple handles payment, renewal, cancellation, restoration, and refunds.")),
                (String(localized: "Responsible use"), String(localized: "Do not interact with Volta while driving. Follow road rules, posted parking conditions, and charger operator instructions.")),
            ]
        case .privacy:
            [
                (String(localized: "Configured Volta service"), String(localized: "Volta sends searches and visible map areas to the configured Volta service when you search or move the map. After location permission is granted, precise coordinates are also sent when the app opens and when you ask to find or rank nearby chargers.")),
                (String(localized: "Search relay and service logs"), String(localized: "City searches are relayed by the Volta service to Nominatim. The service may cache requests and results, and logs the client IP address for security and rate limiting.")),
                (String(localized: "Maps and charger photos"), String(localized: "MapKit loads map content from Apple. Charger details may show optional photos from the attributed third-party source named in the app.")),
                (String(localized: "Subscriptions and payments"), String(localized: "StoreKit and the App Store handle Volta Plus subscriptions, payments, renewals, restoration, and refunds. Volta does not receive payment details.")),
                (String(localized: "On-device preferences"), String(localized: "Volta stores normal app preferences on this device. The app contains no advertising or third-party analytics SDK.")),
                (String(localized: "Connected vehicle data"), String(localized: "Connecting a car is optional. With your permission, Smartcar sends the vehicle identity, battery, range and charging readings to the configured Volta service. OEM access tokens stay on that service; the iPhone stores its Volta session securely in Keychain. Disconnect in Your EV to remove that service session. Selected car photos, manual entries and charging targets stay on this iPhone.")),
                (String(localized: "Contact and control"), String(localized: "Questions about privacy, access, or deletion can be sent through Volta Support. You can stop location sharing in iOS Settings and remove on-device preferences by deleting the app.")),
            ]
        }
    }
}

struct LegalDocumentView: View {
    let document: LegalDocument

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                ForEach(Array(document.sections.enumerated()), id: \.offset) { _, section in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(section.title).font(.headline)
                        Text(section.body)
                            .font(.body)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            .frame(maxWidth: 640, alignment: .leading)
            .padding(20)
        }
        .navigationTitle(document.title)
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("legalDocument-\(document.rawValue)")
    }
}
