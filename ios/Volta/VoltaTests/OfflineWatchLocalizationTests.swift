import Foundation
import XCTest

/// VG-23: check the app's compiled resources, not just the catalog's JSON shape.
/// An explicit language bundle keeps these checks independent of the test device's language.
final class OfflineWatchLocalizationTests: XCTestCase {
    private let keys = [
        "Offline. Showing chargers saved on this iPhone.",
        "Offline. Showing chargers saved on this iPhone on %@. Availability and prices may have changed.",
        "Notifications are off for Volta Go. Turn them on in Settings to get charger alerts.",
        "This build can't receive alerts yet. Push notifications are not set up.",
        "Couldn't start the watch. Try again.",
        "Couldn't reach Volta Go. Try again.",
        "A connector showed free. Check the current status before driving.",
        "The watch ended without a free connector.",
        "Watch stopped: live status unavailable.",
        "The watch has ended.",
        "Alert me when a connector is free",
        "Stop watching",
        "An alert never reserves a connector. It may be taken before you arrive.",
        "Watching this charger.",
        "Watching until %@.",
    ]

    private func languageBundle(_ language: String) throws -> Bundle {
        let url = try XCTUnwrap(Bundle.main.url(forResource: language, withExtension: "lproj"), language)
        return try XCTUnwrap(Bundle(url: url), language)
    }

    func testEveryOfflineAndWatchMessageExistsInEachCompiledLanguage() throws {
        for language in ["en", "es", "ru"] {
            let bundle = try languageBundle(language)
            for key in keys {
                let value = bundle.localizedString(forKey: key, value: "__missing_translation__", table: "Localizable")
                XCTAssertNotEqual(value, "__missing_translation__", "\(language): \(key)")
                XCTAssertFalse(value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, key)
                if language != "en" { XCTAssertNotEqual(value, key, "\(language): English fallback for \(key)") }
            }
        }
    }

    func testOfflineInterpolationPreservesTheDateAndStaleEvidenceWarning() throws {
        // The app passes an already formatted date string. Check substitution without
        // coupling this contract to OS-specific date punctuation or the device locale.
        let dateText = "28/10/2026, 23:45"
        let expected = [
            "en": "Offline. Showing chargers saved on this iPhone on 28/10/2026, 23:45. Availability and prices may have changed.",
            "es": "Sin conexión. Se muestran los cargadores guardados en este iPhone el 28/10/2026, 23:45. La disponibilidad y los precios pueden haber cambiado.",
            "ru": "Нет подключения к сети. Показаны зарядные станции, сохранённые на этом iPhone 28/10/2026, 23:45. Доступность и цены могли измениться.",
        ]
        for language in ["en", "es", "ru"] {
            let bundle = try languageBundle(language)
            let text = String(localized: "Offline. Showing chargers saved on this iPhone on \(dateText). Availability and prices may have changed.", bundle: bundle)
            XCTAssertEqual(text, expected[language], language)
        }
    }

    func testWatchInterpolationPreservesTheExpiryTime() throws {
        let timeText = "23:45"
        let expected = [
            "en": "Watching until 23:45.",
            "es": "Aviso activo hasta las 23:45.",
            "ru": "Отслеживание до 23:45.",
        ]
        for language in ["en", "es", "ru"] {
            let bundle = try languageBundle(language)
            let text = String(localized: "Watching until \(timeText).", bundle: bundle)
            XCTAssertEqual(text, expected[language], language)
        }
    }
}
