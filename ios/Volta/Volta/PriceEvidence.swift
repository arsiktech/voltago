import Foundation
import SwiftUI

/// Additive source text. Numeric metadata is retained for decoding, never used by
/// station pricing, filters, routing, verification or vehicle cost estimates.
struct PriceEvidence: Codable, Hashable, Identifiable {
    var kind: String
    var source: String
    var label: String
    var text: String
    var sourceUrl: String
    var sourceUpdatedAt: String?
    var fetchedAt: String?
    var pricePerKwh: Double?
    var currency: String?
    var parkingPrice: String?

    struct Identity: Hashable {
        let sourceUrl: String
        let kind: String
        let sourceUpdatedAt: String?
        let fetchedAt: String?
    }

    var id: Identity {
        Identity(sourceUrl: sourceUrl, kind: kind,
                 sourceUpdatedAt: sourceUpdatedAt, fetchedAt: fetchedAt)
    }

    var safeSourceURL: URL? { PublicWebURL.validated(sourceUrl) }
}

/// Labels come from known kinds, not upstream English. Unknown future kinds decode
/// without losing the station, but cannot invent a new trust label in this client.
struct PriceEvidencePresentation: Identifiable {
    let id: PriceEvidence.Identity
    let label: String
    let text: String
    let checkedText: String?
    let parkingText: String?
    let sourceURL: URL?

    init?(_ evidence: PriceEvidence) {
        let unknownDate = String(localized: "Date unknown")
        switch evidence.kind {
        case "repsol_network_tariff":
            label = String(localized: "Repsol published tariff · station may differ")
            let date = Self.sourceDate(evidence.fetchedAt) ?? unknownDate
            checkedText = String(localized: "Checked: \(date)")
        case "electromaps_community":
            let date = Self.sourceDate(evidence.sourceUpdatedAt) ?? unknownDate
            label = String(localized: "Electromaps community price · \(date)")
            checkedText = nil
        case "chargemap_pass":
            label = String(localized: "Price with Chargemap Pass")
            let date = Self.sourceDate(evidence.fetchedAt) ?? unknownDate
            checkedText = String(localized: "Checked: \(date)")
        default:
            return nil
        }
        id = evidence.id
        // Keep qualifiers, started-kWh billing and idle-fee grace periods verbatim.
        text = evidence.text
        sourceURL = evidence.safeSourceURL
        if let parking = evidence.parkingPrice, !parking.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            parkingText = String(localized: "Parking: \(parking)")
        } else {
            parkingText = nil
        }
    }

    /// Preserve the source's calendar date, without turning a download into a
    /// source update or moving a timestamp across midnight in the device timezone.
    static func sourceDate(_ raw: String?) -> String? {
        guard let raw,
              raw.range(of: #"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:$|T| )"#,
                        options: .regularExpression) != nil else { return nil }
        let date = String(raw.prefix(10))
        let parts = date.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3, (1...9999).contains(parts[0]) else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let components = DateComponents(year: parts[0], month: parts[1], day: parts[2])
        guard let parsed = calendar.date(from: components),
              calendar.dateComponents([.year, .month, .day], from: parsed) == components else { return nil }
        return date
    }
}

extension Station {
    var priceEvidencePresentations: [PriceEvidencePresentation] {
        (priceEvidence ?? []).compactMap(PriceEvidencePresentation.init)
    }
}

/// Quiet wrapping source claims below the station's primary price on cards and
/// details. Cards already sit inside a detail button, so source links live in details.
struct StationPriceEvidence: View {
    let station: Station
    var showsSourceLinks = false

    var body: some View {
        let items = station.priceEvidencePresentations
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(items) { item in
                    VStack(alignment: .leading, spacing: 3) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(verbatim: item.label)
                                .font(.caption.weight(.medium))
                            Text(verbatim: item.text)
                                .font(.caption)
                            if let checked = item.checkedText {
                                Text(verbatim: checked)
                                    .font(.caption2)
                            }
                            if let parking = item.parkingText {
                                Text(verbatim: parking)
                                    .font(.caption)
                            }
                        }
                        .accessibilityElement(children: .combine)
                        if showsSourceLinks, let url = item.sourceURL {
                            Link(destination: url) {
                                Text("Source")
                                    .underline()
                                    .frame(minHeight: 44)
                            }
                            .font(.caption)
                            .accessibilityLabel(Text(verbatim: "\(String(localized: "Source")): \(item.label)"))
                        }
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .foregroundStyle(.secondary)
            .tint(.secondary)
        }
    }
}
