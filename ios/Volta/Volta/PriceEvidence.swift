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
    var rates: [PriceEvidenceRate]? = nil

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
        text = Self.rateText(evidence.rates)
        sourceURL = evidence.safeSourceURL
        if let parking = evidence.parkingPrice, !parking.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            parkingText = String(localized: "Parking: \(parking)")
        } else {
            parkingText = nil
        }
    }

    /// Match the app's euro prefix and decimal point while retaining sub-cent rates.
    private static func euro(_ value: Double) -> String {
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "en_IE")
        formatter.numberStyle = .currency
        formatter.currencyCode = "EUR"
        formatter.minimumFractionDigits = 2
        formatter.maximumFractionDigits = 6
        formatter.usesGroupingSeparator = false
        return formatter.string(from: NSNumber(value: value))!
    }

    static func rateText(_ rates: [PriceEvidenceRate]?) -> String {
        guard let rates, !rates.isEmpty, rates.allSatisfy(\.isValid) else {
            return String(localized: "See source for prices")
        }
        let allKwh = rates.allSatisfy { $0.kind == "energy" && $0.unit == "kWh" }
        let parts = rates.map { rate -> String in
            let low = euro(rate.min)
            let amount: String
            if let maximum = rate.max {
                let high = String(euro(maximum).dropFirst())
                amount = String(localized: "\(low)–\(high)")
            } else {
                amount = low
            }
            if rate.kind == "idle" {
                if let minutes = rate.afterMinutes {
                    let grace = minutes.formatted(.number.locale(Locale.current).grouping(.never))
                    return String(localized: "idle \(amount)/min after \(grace) min")
                }
                return String(localized: "idle \(amount)/min")
            }
            let labelled: String
            let price = rate.from ? String(localized: "from \(amount)") : amount
            switch rate.label {
            case "normal": labelled = String(localized: "Normal \(price)")
            case "fast": labelled = String(localized: "Fast \(price)")
            case "ultrafast": labelled = String(localized: "Ultra-fast \(price)")
            case "motorcycle": labelled = String(localized: "Motorcycle \(price)")
            default: labelled = rate.from ? String(localized: "From \(amount)") : amount
            }
            if allKwh { return labelled }
            return rate.unit == "started_kWh"
                ? String(localized: "\(labelled) per started kWh")
                : String(localized: "\(labelled) per kWh")
        }
        let line = parts.joined(separator: " · ")
        return allKwh ? String(localized: "\(line) per kWh") : line
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

    var cardPriceEvidencePresentations: [PriceEvidencePresentation] {
        (priceEvidence ?? []).filter {
            !($0.kind == "repsol_network_tariff" && priceMin != nil && hasVerifiedPrice)
        }.compactMap(PriceEvidencePresentation.init)
    }
}

/// Quiet wrapping source claims below the station's primary price on cards and
/// details. Cards already sit inside a detail button, so source links live in details.
struct StationPriceEvidence: View {
    let station: Station
    var showsSourceLinks = false

    var body: some View {
        let items = showsSourceLinks ? station.priceEvidencePresentations : station.cardPriceEvidencePresentations
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
                        if showsSourceLinks, let reference = station.sourceReference(item.sourceURL?.absoluteString, tariff: true) {
                            Link(destination: reference.url) {
                                Text(reference.label)
                                    .underline()
                                    .frame(minHeight: 44)
                            }
                            .font(.caption)
                            .accessibilityHint(Text(verbatim: item.label))
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
