import json
import os
import plistlib
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios" / "Volta"
APP = IOS / "Volta"
PROJECT = IOS / "Volta.xcodeproj"


DEBUG_ONLY_LOCALIZATION_KEYS = {
    "Backend address",
    "Backend server address",
    "Cancel",
    "Checks whether the Volta server responds at this address",
    "Choose and test the Volta server address",
    "Enter the Volta server address and port, or choose a preset below",
    "Presets",
    "Save",
    "Sets the backend address to %@",
    "Test connection",
    "Testing connection",
    "Testing…",
    "The Volta server must be running on the Mac:\nHOST=0.0.0.0 PORT=8773 python3 server.py",
    "http://host:8773",
    "✓ Connected — server and data reachable",
    "✗ Unreachable: %@",
}

STALE_PRIVACY_KEYS = {
    "Device storage",
    "Location and charger requests",
    "No Volta account",
    "This is an honest draft privacy notice. Public hosting and owner review are still required before App Store release.",
    "Volta does not ask you to create an account. A subscription follows the Apple ID used for App Store purchases; Volta does not receive your payment details.",
    "Volta stores your selected backend address and normal app preferences on the device. The app contains no advertising or third-party analytics SDK.",
    "With permission, your location is used to request nearby charger data from the Volta server you choose. Search text and map areas are also sent to that server to return relevant results.",
}

PRIVACY_COPY = {
    "Configured Volta service": {
        "en": "Configured Volta service",
        "es": "Servicio de Volta configurado",
        "ru": "Настроенный сервис Volta",
    },
    "Volta sends searches and visible map areas to the configured Volta service when you search or move the map. After location permission is granted, precise coordinates are also sent when the app opens and when you ask to find or rank nearby chargers.": {
        "en": "Volta sends searches and visible map areas to the configured Volta service when you search or move the map. After location permission is granted, precise coordinates are also sent when the app opens and when you ask to find or rank nearby chargers.",
        "es": "Volta envía las búsquedas y las áreas visibles del mapa al servicio de Volta configurado cuando buscas o mueves el mapa. Tras conceder permiso de ubicación, también envía las coordenadas precisas al abrir la app y cuando pides buscar u ordenar cargadores cercanos.",
        "ru": "Volta отправляет поисковые запросы и видимые области карты настроенному сервису Volta, когда вы выполняете поиск или перемещаете карту. После разрешения доступа к геопозиции точные координаты также отправляются при открытии приложения и когда вы просите найти или ранжировать ближайшие зарядные станции.",
    },
    "Search relay and service logs": {
        "en": "Search relay and service logs",
        "es": "Reenvío de búsquedas y registros del servicio",
        "ru": "Передача поиска и журналы сервиса",
    },
    "City searches are relayed by the Volta service to Nominatim. The service may cache requests and results, and logs the client IP address for security and rate limiting.": {
        "en": "City searches are relayed by the Volta service to Nominatim. The service may cache requests and results, and logs the client IP address for security and rate limiting.",
        "es": "El servicio de Volta reenvía las búsquedas de ciudades a Nominatim. El servicio puede almacenar en caché las solicitudes y los resultados, y registra la dirección IP del cliente por seguridad y para limitar la frecuencia de uso.",
        "ru": "Сервис Volta перенаправляет поиск городов в Nominatim. Сервис может кэшировать запросы и результаты, а также регистрирует IP-адрес клиента для защиты и ограничения частоты запросов.",
    },
    "Maps and charger photos": {
        "en": "Maps and charger photos",
        "es": "Mapas y fotos de cargadores",
        "ru": "Карты и фотографии зарядных станций",
    },
    "MapKit loads map content from Apple. Charger details may show optional photos from the attributed third-party source named in the app.": {
        "en": "MapKit loads map content from Apple. Charger details may show optional photos from the attributed third-party source named in the app.",
        "es": "MapKit carga contenido cartográfico de Apple. Los detalles de un cargador pueden mostrar fotos opcionales de la fuente externa atribuida que se indica en la app.",
        "ru": "MapKit загружает картографические материалы Apple. В сведениях о зарядной станции могут отображаться необязательные фотографии из указанного в приложении стороннего источника.",
    },
    "Subscriptions and payments": {
        "en": "Subscriptions and payments",
        "es": "Suscripciones y pagos",
        "ru": "Подписки и платежи",
    },
    "StoreKit and the App Store handle Volta Plus subscriptions, payments, renewals, restoration, and refunds. Volta does not receive payment details.": {
        "en": "StoreKit and the App Store handle Volta Plus subscriptions, payments, renewals, restoration, and refunds. Volta does not receive payment details.",
        "es": "StoreKit y el App Store gestionan las suscripciones a Volta Plus, los pagos, las renovaciones, la restauración y los reembolsos. Volta no recibe los datos de pago.",
        "ru": "StoreKit и App Store обрабатывают подписки Volta Plus, платежи, продления, восстановление и возвраты. Volta не получает платёжные данные.",
    },
    "On-device preferences": {
        "en": "On-device preferences",
        "es": "Preferencias en el dispositivo",
        "ru": "Настройки на устройстве",
    },
    "Volta stores normal app preferences on this device. The app contains no advertising or third-party analytics SDK.": {
        "en": "Volta stores normal app preferences on this device. The app contains no advertising or third-party analytics SDK.",
        "es": "Volta guarda las preferencias normales de la app en este dispositivo. La app no contiene publicidad ni SDK de análisis de terceros.",
        "ru": "Volta хранит обычные настройки приложения на этом устройстве. В приложении нет рекламы и сторонних SDK аналитики.",
    },
    "Contact and control": {
        "en": "Contact and control",
        "es": "Contacto y control",
        "ru": "Связь и управление",
    },
    "Questions about privacy, access, or deletion can be sent through Volta Support. You can stop location sharing in iOS Settings and remove on-device preferences by deleting the app.": {
        "en": "Questions about privacy, access, or deletion can be sent through Volta Support. You can stop location sharing in iOS Settings and remove on-device preferences by deleting the app.",
        "es": "Puedes enviar preguntas sobre privacidad, acceso o eliminación al soporte de Volta. Puedes detener el uso de la ubicación en Ajustes de iOS y borrar las preferencias del dispositivo eliminando la app.",
        "ru": "Вопросы о конфиденциальности, доступе или удалении можно направить в поддержку Volta. Передачу геопозиции можно отключить в настройках iOS, а локальные настройки удалить вместе с приложением.",
    },
}


class IOSReleaseContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = (PROJECT / "project.pbxproj").read_text(encoding="utf-8")
        cls.scheme = (
            PROJECT / "xcshareddata" / "xcschemes" / "Volta.xcscheme"
        ).read_text(encoding="utf-8")

    def test_privacy_manifest_reports_current_data_and_required_reason_apis(self):
        manifest_path = APP / "PrivacyInfo.xcprivacy"
        self.assertTrue(manifest_path.exists())
        with manifest_path.open("rb") as handle:
            manifest = plistlib.load(handle)

        self.assertIs(manifest["NSPrivacyTracking"], False)
        self.assertEqual(manifest["NSPrivacyTrackingDomains"], [])

        collected = {
            item["NSPrivacyCollectedDataType"]: item
            for item in manifest["NSPrivacyCollectedDataTypes"]
        }
        expected_linking = {
            "NSPrivacyCollectedDataTypePreciseLocation": True,
            "NSPrivacyCollectedDataTypeSearchHistory": False,
            "NSPrivacyCollectedDataTypeDeviceID": True,
        }
        self.assertEqual(set(collected), set(expected_linking))
        for data_type, linked in expected_linking.items():
            item = collected[data_type]
            self.assertIs(item["NSPrivacyCollectedDataTypeLinked"], linked)
            self.assertIs(item["NSPrivacyCollectedDataTypeTracking"], False)
            self.assertEqual(
                item["NSPrivacyCollectedDataTypePurposes"],
                ["NSPrivacyCollectedDataTypePurposeAppFunctionality"],
            )

        accessed = {
            item["NSPrivacyAccessedAPIType"]: item["NSPrivacyAccessedAPITypeReasons"]
            for item in manifest["NSPrivacyAccessedAPITypes"]
        }
        self.assertEqual(
            accessed,
            {
                "NSPrivacyAccessedAPICategoryUserDefaults": ["CA92.1"],
                "NSPrivacyAccessedAPICategorySystemBootTime": ["35F9.1"],
            },
        )

    def test_location_purpose_is_exact_and_localized_in_all_supported_languages(self):
        expected = {
            "en": (
                "After location access is granted, Volta uses your location when the app "
                "opens and when you ask to find or rank nearby chargers. Your coordinates "
                "are sent to the configured Volta service."
            ),
            "es": (
                "Después de conceder acceso a la ubicación, Volta la usa al abrir la app "
                "y cuando pides buscar u ordenar cargadores cercanos. Tus coordenadas se "
                "envían al servicio de Volta configurado."
            ),
            "ru": (
                "После разрешения доступа к геопозиции Volta использует её при открытии "
                "приложения и когда вы просите найти или ранжировать ближайшие зарядные "
                "станции. Ваши координаты отправляются настроенному сервису Volta."
            ),
        }
        catalog = json.loads((APP / "InfoPlist.xcstrings").read_text(encoding="utf-8"))
        entry = catalog["strings"]["NSLocationWhenInUseUsageDescription"]
        self.assertEqual(entry.get("extractionState"), "manual")
        for locale, value in expected.items():
            self.assertEqual(
                entry["localizations"][locale]["stringUnit"]["value"], value
            )

        for plist_name in ("Info.plist", "Info-Debug.plist"):
            with (APP / plist_name).open("rb") as handle:
                info = plistlib.load(handle)
            self.assertEqual(info["NSLocationWhenInUseUsageDescription"], expected["en"])

    def test_release_info_is_strict_and_debug_info_is_local_only(self):
        with (APP / "Info.plist").open("rb") as handle:
            release = plistlib.load(handle)
        with (APP / "Info-Debug.plist").open("rb") as handle:
            debug = plistlib.load(handle)

        for info in (release, debug):
            self.assertIs(info["ITSAppUsesNonExemptEncryption"], False)
            self.assertEqual(
                info["UILaunchScreen"], {"UIColorName": "LaunchBackground"}
            )

        self.assertNotIn("NSAppTransportSecurity", release)
        self.assertNotIn("NSLocalNetworkUsageDescription", release)
        self.assertNotIn("NSAllowsArbitraryLoads", repr(release))
        self.assertEqual(release["VoltaReleaseBaseURL"], "$(VOLTA_RELEASE_BASE_URL)")
        self.assertEqual(
            release["VoltaPrivacyPolicyURL"], "$(VOLTA_PRIVACY_POLICY_URL)"
        )
        self.assertEqual(release["VoltaTermsURL"], "$(VOLTA_TERMS_URL)")
        self.assertEqual(release["VoltaSupportURL"], "$(VOLTA_SUPPORT_URL)")
        for key in ("VoltaPrivacyPolicyURL", "VoltaTermsURL", "VoltaSupportURL"):
            self.assertNotIn(key, debug)
        self.assertEqual(
            debug["NSAppTransportSecurity"], {"NSAllowsLocalNetworking": True}
        )
        self.assertIn("NSLocalNetworkUsageDescription", debug)
        self.assertNotIn("NSAllowsArbitraryLoads", repr(debug))

    def test_release_endpoint_policy_is_https_only_and_fail_closed(self):
        source = (APP / "RuntimeConfiguration.swift").read_text(encoding="utf-8")
        self.assertIn('"https://configuration.invalid"', source)
        self.assertIn('components.scheme?.lowercased() == "https"', source)
        self.assertIn('object(forInfoDictionaryKey: "VoltaReleaseBaseURL")', source)
        self.assertIn("static func resolvedURL(from rawValue: String?) -> URL", source)
        self.assertIn(
            "static func resolvedDocumentURL(from rawValue: String?) -> URL?", source
        )
        self.assertIn("static func isConfigured(_ url: URL) -> Bool", source)
        self.assertIn("candidate.unicodeScalars.allSatisfy(\\.isASCII)", source)
        self.assertIn('!candidate.contains("%")', source)
        self.assertIn("components.percentEncodedPath", source)
        for suffix in (
            "localhost",
            "local",
            "internal",
            "invalid",
            "test",
            "example",
            "home.arpa",
        ):
            self.assertIn(f'"{suffix}"', source)
        self.assertIn('label.hasPrefix("xn--")', source)
        self.assertIn('label.hasPrefix("0x")', source)
        for key in ("VoltaPrivacyPolicyURL", "VoltaTermsURL", "VoltaSupportURL"):
            self.assertIn(f'hostedDocumentURL(forInfoDictionaryKey: "{key}")', source)
        self.assertIn("Bundle.main.object(forInfoDictionaryKey: key)", source)
        self.assertNotIn('"http://127.0.0.1:8773"', source.split("#if DEBUG")[0])

    def test_native_privacy_copy_is_truthful_and_fully_localized(self):
        source = (APP / "LegalDocumentView.swift").read_text(encoding="utf-8")
        catalog = json.loads(
            (APP / "Localizable.xcstrings").read_text(encoding="utf-8")
        )["strings"]

        for key, localized in PRIVACY_COPY.items():
            self.assertIn(f'String(localized: "{key}")', source)
            self.assertIn(key, catalog)
            self.assertEqual(
                set(catalog[key]["localizations"]), {"en", "es", "ru"}, key
            )
            for locale, expected in localized.items():
                self.assertEqual(
                    catalog[key]["localizations"][locale]["stringUnit"]["value"],
                    expected,
                    f"{key} ({locale})",
                )

        for stale in STALE_PRIVACY_KEYS:
            self.assertNotIn(stale, source)
            self.assertNotIn(stale, catalog)

        combined = "\n".join(PRIVACY_COPY)
        for disclosure in (
            "configured Volta service",
            "when the app opens",
            "Nominatim",
            "client IP address",
            "MapKit",
            "third-party source",
            "StoreKit",
            "normal app preferences",
            "no advertising",
            "deletion",
        ):
            self.assertIn(disclosure, combined)

    def test_debug_operational_copy_is_not_in_release_localization_resources(self):
        catalog = json.loads(
            (APP / "Localizable.xcstrings").read_text(encoding="utf-8")
        )["strings"]
        self.assertTrue(DEBUG_ONLY_LOCALIZATION_KEYS)
        for forbidden in DEBUG_ONLY_LOCALIZATION_KEYS:
            self.assertNotIn(forbidden, catalog)

        settings = (APP / "SettingsView.swift").read_text(encoding="utf-8")
        debug_branch = settings.split("#if DEBUG", 1)[1].split("#else", 1)[0]
        for required in (
            'Text(verbatim: "Backend address")',
            'Text(verbatim: "http://host:8773")',
            'Text(verbatim: testing ? "Testing…" : "Test connection")',
        ):
            self.assertIn(required, debug_branch)
        self.assertNotIn("MagicDNS", settings)
        self.assertNotIn(".ts.net", settings)

    def test_embedded_legal_is_available_in_every_build_and_support_is_real(self):
        settings = (APP / "SettingsView.swift").read_text(encoding="utf-8")
        paywall = (APP / "PaywallView.swift").read_text(encoding="utf-8")
        self.assertIn('NavigationLink(value: LegalDocument.terms)', settings)
        self.assertIn('NavigationLink(value: LegalDocument.privacy)', settings)
        self.assertIn('NavigationLink("Terms", value: LegalDocument.terms)', paywall)
        self.assertIn('NavigationLink("Privacy", value: LegalDocument.privacy)', paywall)
        for source in (settings, paywall):
            self.assertNotIn("Pending release configuration", source)
        self.assertIn("mailto:arsik2121@icloud.com", settings)

    def test_recursive_release_bundle_scan_omits_debug_and_stale_privacy_copy(self):
        raw_path = os.environ.get("VOLTA_RELEASE_APP_PATH")
        if not raw_path:
            self.skipTest("set VOLTA_RELEASE_APP_PATH after a Release build")
        app_path = Path(raw_path)
        self.assertTrue(app_path.is_dir(), app_path)

        built_info = plistlib.loads((app_path / "Info.plist").read_bytes())
        self.assertNotIn("NSAppTransportSecurity", built_info)
        self.assertNotIn("NSLocalNetworkUsageDescription", built_info)
        expected_url_env = {
            "VoltaReleaseBaseURL": "VOLTA_EXPECTED_RELEASE_BASE_URL",
            "VoltaPrivacyPolicyURL": "VOLTA_EXPECTED_PRIVACY_POLICY_URL",
            "VoltaTermsURL": "VOLTA_EXPECTED_TERMS_URL",
            "VoltaSupportURL": "VOLTA_EXPECTED_SUPPORT_URL",
        }
        for key, env_name in expected_url_env.items():
            expected = os.environ.get(env_name)
            self.assertTrue(expected, f"set {env_name} for the built Release candidate")
            self.assertTrue(expected.startswith("https://"), f"{env_name} must be HTTPS")
            self.assertEqual(built_info[key], expected)

        source_manifest = plistlib.loads((APP / "PrivacyInfo.xcprivacy").read_bytes())
        built_manifest = plistlib.loads((app_path / "PrivacyInfo.xcprivacy").read_bytes())
        self.assertEqual(built_manifest, source_manifest)
        self.assertEqual(list(app_path.rglob("*.storekit")), [])

        info_catalog = json.loads(
            (APP / "InfoPlist.xcstrings").read_text(encoding="utf-8")
        )["strings"]["NSLocationWhenInUseUsageDescription"]["localizations"]
        for locale in ("en", "es", "ru"):
            localized_info = plistlib.loads(
                (app_path / f"{locale}.lproj" / "InfoPlist.strings").read_bytes()
            )
            self.assertEqual(
                localized_info["NSLocationWhenInUseUsageDescription"],
                info_catalog[locale]["stringUnit"]["value"],
            )
            localized_ui = plistlib.loads(
                (app_path / f"{locale}.lproj" / "Localizable.strings").read_bytes()
            )
            for key, translations in PRIVACY_COPY.items():
                self.assertEqual(localized_ui[key], translations[locale], key)
            for forbidden in DEBUG_ONLY_LOCALIZATION_KEYS | STALE_PRIVACY_KEYS:
                self.assertNotIn(forbidden, localized_ui)

        forbidden = DEBUG_ONLY_LOCALIZATION_KEYS | STALE_PRIVACY_KEYS | {
            "your-mac.local",
            "192.168.1.220",
            "127.0.0.1:8773",
        }
        exact_key_only = {"Cancel", "Save", "No Volta account"}
        hits = []
        for path in sorted(item for item in app_path.rglob("*") if item.is_file()):
            data = path.read_bytes()
            parsed = None
            try:
                parsed = plistlib.loads(data)
            except (plistlib.InvalidFileException, ValueError, TypeError):
                pass
            if isinstance(parsed, dict):
                for needle in forbidden & set(parsed):
                    hits.append(
                        f"{path.relative_to(app_path)}: exact localization key {needle}"
                    )
            for needle in forbidden - exact_key_only:
                encoded_needles = (
                    needle.encode("utf-8"),
                    needle.encode("utf-16-le"),
                    needle.encode("utf-16-be"),
                )
                if any(encoded in data for encoded in encoded_needles):
                    hits.append(f"{path.relative_to(app_path)}: {needle}")
        self.assertEqual(hits, [])

    def test_review_note_assemblies_fit_app_store_utf8_byte_limit(self):
        source = (ROOT / "docs" / "appstore" / "review-notes.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("limited to **4,000 UTF-8 bytes**", source)
        self.assertNotIn("limited to 4,000 Unicode characters", source)
        assemblies = re.findall(
            r"### Assembly ([AB]).*?```text\n(.*?)\n```", source, re.S
        )
        self.assertEqual([label for label, _ in assemblies], ["A", "B"])
        substitutions = {
            "[STABLE PUBLIC HTTPS VOLTA ENDPOINT]": (
                "https://api.volta-carga-clara.example"
            ),
            "[FINAL PUBLIC PRIVACY URL]": (
                "https://www.volta-carga-clara.example/legal/privacy"
            ),
            "[FINAL PUBLIC TERMS URL]": (
                "https://www.volta-carga-clara.example/legal/terms"
            ),
            "[FINAL PUBLIC SUPPORT URL]": (
                "https://www.volta-carga-clara.example/help/support"
            ),
        }
        for label, assembly in assemblies:
            filled = assembly + "\n"
            for placeholder, value in substitutions.items():
                filled = filled.replace(placeholder, value)
            byte_count = len(filled.encode("utf-8"))
            self.assertLessEqual(byte_count, 4_000, label)
            self.assertGreaterEqual(4_000 - byte_count, 500, label)

    def test_private_backend_controls_and_literals_are_debug_only(self):
        settings = (APP / "SettingsView.swift").read_text(encoding="utf-8")
        self.assertRegex(settings, re.compile(r"#if DEBUG.*?your-mac\.local.*?#else", re.S))
        release_branch = settings.split("#else", 1)[1].split("#endif", 1)[0]
        for forbidden in (
            "http://",
            "Tailscale",
            "MagicDNS",
            "home Wi-Fi",
            "Backend address",
            "Test connection",
            "TextField",
        ):
            self.assertNotIn(forbidden, release_branch)
        for required in ("Service", "Version"):
            self.assertIn(required, release_branch)
        for required in ("Terms of Use", "Privacy", "Support"):
            self.assertIn(required, settings)

    def test_app_state_release_branch_does_not_persist_or_fallback_to_http(self):
        source = (APP / "AppState.swift").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            re.compile(r"#if DEBUG\s+@AppStorage\(\"voltaBaseURL\"\).*?#else", re.S),
        )
        release_property = source.split("#else", 1)[1].split("#endif", 1)[0]
        self.assertNotIn("@AppStorage", release_property)
        self.assertNotIn("http://", release_property)
        self.assertIn("VoltaRuntimeConfiguration.baseURL", source)
        self.assertNotIn('URL(string: "http://127.0.0.1:8773")', source)

    def test_product_build_settings_are_release_deliberate(self):
        debug = self._target_configuration("A100000E000000000000001")
        release = self._target_configuration("A100000F000000000000001")
        state = json.loads(
            (ROOT / "docs/appstore/testflight-state.json").read_text(encoding="utf-8")
        )
        candidate = state["candidate"]

        self.assertIn(f"CURRENT_PROJECT_VERSION = {candidate['build']};", debug)
        self.assertIn(f"CURRENT_PROJECT_VERSION = {candidate['build']};", release)
        self.assertIn(f"MARKETING_VERSION = {candidate['version']};", debug)
        self.assertIn(f"MARKETING_VERSION = {candidate['version']};", release)
        self.assertIn("TARGETED_DEVICE_FAMILY = 1;", debug)
        self.assertIn("TARGETED_DEVICE_FAMILY = 1;", release)
        for block in (debug, release):
            self.assertIn(
                "INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone = "
                "UIInterfaceOrientationPortrait;",
                block,
            )
            self.assertNotIn("UISupportedInterfaceOrientations_iPad", block)
            self.assertNotIn("UILaunchScreen_Generation", block)

        self.assertRegex(
            debug, re.compile(r'INFOPLIST_FILE = "?Volta/Info-Debug\.plist"?;')
        )
        self.assertIn("INFOPLIST_FILE = Volta/Info.plist;", release)
        self.assertIn(
            'VOLTA_RELEASE_BASE_URL = "https://configuration.invalid";', release
        )
        for setting in (
            "VOLTA_PRIVACY_POLICY_URL",
            "VOLTA_TERMS_URL",
            "VOLTA_SUPPORT_URL",
        ):
            self.assertIn(
                f'{setting} = "https://configuration.invalid";', release
            )
        for key in (
            "VoltaReleaseBaseURL",
            "VoltaPrivacyPolicyURL",
            "VoltaTermsURL",
            "VoltaSupportURL",
        ):
            self.assertNotIn(f"INFOPLIST_KEY_{key}", release)

    def test_storekit_configuration_is_test_only(self):
        self.assertFalse((APP / "Volta.storekit").exists())
        fixture = IOS / "VoltaTests" / "Volta.storekit"
        self.assertTrue(fixture.is_file())
        # With `xcodebuild -project`, scheme paths resolve from the project's
        # embedded workspace, not from xcshareddata/xcschemes.
        workspace = PROJECT / "project.xcworkspace"
        scheme = ET.fromstring(self.scheme)
        for action in ("TestAction", "LaunchAction"):
            with self.subTest(action=action):
                references = scheme.findall(
                    f"{action}/StoreKitConfigurationFileReference"
                )
                self.assertEqual(len(references), 1)
                resolved = workspace / references[0].attrib["identifier"]
                self.assertEqual(resolved.resolve(), fixture.resolve())

    def test_launch_background_is_adaptive_and_icon_composer_source_is_complete(self):
        colors = json.loads(
            (
                APP
                / "Assets.xcassets"
                / "LaunchBackground.colorset"
                / "Contents.json"
            ).read_text(encoding="utf-8")
        )["colors"]
        self.assertEqual(len(colors), 2)
        dark = [
            color
            for color in colors
            if color.get("appearances") == [
                {"appearance": "luminosity", "value": "dark"}
            ]
        ]
        self.assertEqual(len(dark), 1)
        icon = json.loads(
            (
                APP / "Assets.xcassets" / "AppIcon.appiconset" / "Contents.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(
            any(image.get("idiom") == "ios-marketing" for image in icon["images"])
        )
        composer = APP / "AppIcon.icon"
        self.assertTrue(composer.is_dir())
        icon_document = json.loads((composer / "icon.json").read_text(encoding="utf-8"))
        layer_names = [
            layer["image-name"]
            for group in icon_document["groups"]
            for layer in group["layers"]
        ]
        self.assertEqual(
            layer_names,
            [
                "03-route-accent.svg",
                "02-bolt.svg",
                "01-pin.svg",
                "00-background.svg",
            ],
        )
        for layer_name in layer_names:
            composer_layer = composer / "Assets" / layer_name
            canonical_layer = (
                ROOT
                / "docs"
                / "visual-design"
                / "voltago-logo-2026-07-15"
                / "layers"
                / layer_name
            )
            self.assertTrue(composer_layer.is_file())
            self.assertEqual(composer_layer.read_bytes(), canonical_layer.read_bytes())

        design_dir = ROOT / "docs" / "visual-design" / "voltago-logo-2026-07-15"
        canonical_mark = (design_dir / "voltago-mark.svg").read_text(encoding="utf-8")
        geometry_consumers = [
            (design_dir / "voltago-app-icon-flat.svg").read_text(encoding="utf-8"),
            # The web mark was replaced on 2026-09-16 (owner rejected the old mark); the
            # web icon no longer derives from the iOS composer layers. The native icon
            # package keeps its own canonical layers unchanged below.
            "\n".join(
                (composer / "Assets" / layer_name).read_text(encoding="utf-8")
                for layer_name in layer_names
            ),
        ]
        canonical_paths = re.findall(r'\sd="([^"]+)"', canonical_mark)
        self.assertEqual(len(canonical_paths), 4)
        for path_data in canonical_paths:
            for consumer in geometry_consumers:
                self.assertIn(f'd="{path_data}"', consumer)

        icon_dir = APP / "Assets.xcassets" / "AppIcon.appiconset"
        for image in icon["images"]:
            filename = image.get("filename")
            if not filename:
                continue
            size = float(image["size"].split("x", 1)[0])
            scale = float(image["scale"].removesuffix("x"))
            expected_pixels = round(size * scale)
            png = (icon_dir / filename).read_bytes()
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(int.from_bytes(png[16:20], "big"), expected_pixels)
            self.assertEqual(int.from_bytes(png[20:24], "big"), expected_pixels)
            self.assertNotIn(png[25], {4, 6}, f"{filename} must not contain alpha")

        marketing_icon = APP / "Assets.xcassets" / "AppIcon.appiconset" / "AppIcon1024.png"
        self.assertGreater(marketing_icon.stat().st_size, 20_000)

    def _target_configuration(self, identifier: str) -> str:
        match = re.search(
            rf"{identifier} /\* (?:Debug|Release) \*/ = \{{(.*?)\n\t\t\}};",
            self.project,
            re.S,
        )
        self.assertIsNotNone(match, f"build configuration {identifier} not found")
        return match.group(1)


if __name__ == "__main__":
    unittest.main()
