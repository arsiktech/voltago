"""Offline contracts for public Canarias te recarga station detail extraction."""
import unittest
from pathlib import Path

from scripts.canarias_details import (MAX_HTML_CHARS, page_identity_matches, parse_canarias_detail,
                                     popup_station_identity, safe_detail_url, safe_photo_url)


FIXTURES = Path(__file__).parent / "fixtures" / "canarias_details"
URL = "https://canariasterecarga.com/es/p00246/"


def fixture(code):
    return (FIXTURES / (code + ".html")).read_text(encoding="utf-8")


def page(connector="<p>Type 2 x2 22 kW</p>", tariff="<p>0,28 €/kWh</p>", extra=""):
    return ('<div id="page-header"><div class="page-title"><h1>Punto de Recarga – Test</h1></div></div>'
            '<div id="page-content"><h1>Localización</h1><p>Test Street 1</p>'
            '<h1>Conectores <span>2 plazas</span></h1>' + connector +
            '<h1>Tarifa</h1>' + tariff + extra + '</div>')


class CanariasDetailTests(unittest.TestCase):
    def test_actual_split_price_keeps_minimum_and_full_socket_inventory(self):
        data = parse_canarias_detail(fixture("p00246"), URL, fetched_at="2026-10-19T10:00:00Z")
        self.assertTrue(data["recognized"])
        self.assertEqual(data["station_name"], "C.C. Pasarela")
        self.assertIn("San Francisco, 6", data["address"])
        self.assertEqual([(c["type"], c["count"], c["power_kw"]) for c in data["connectors"]],
                         [("CCS2", 1, 50.0), ("CHAdeMO", 1, 50.0), ("Type 2", 1, 43.0), ("Type 2", 6, 22.0)])
        self.assertEqual(data["port_count"], 9)
        self.assertEqual(data["bay_count"], 7)
        self.assertEqual(data["max_power_kw"], 50.0)
        tariff = data["tariff"]
        self.assertEqual(tariff["energy_eur_per_kwh"], 0.38)
        self.assertEqual(tariff["minimum_eur"], 2.0)
        self.assertEqual([(c["type"], c["price"]) for c in tariff["components"]], [("ENERGY", 0.38), ("MINIMUM", 2.0)])
        self.assertEqual(tariff["conditions"], ["Mínimo 2€"])
        self.assertFalse(tariff["usable_as_simple_energy_price"])
        self.assertFalse(tariff["unconditional_free"])
        self.assertTrue(all(c["status"] == "UNKNOWN" for c in data["connectors"]))
        self.assertEqual(data["field_provenance"]["address"]["fetched_at"], "2026-10-19T10:00:00Z")

    def test_kwh_rating_normalization_is_explicit_directory_interpretation(self):
        data = parse_canarias_detail(fixture("p61008"), "https://canariasterecarga.com/es/mapa/p61008/")
        connector = data["connectors"][0]
        self.assertEqual(connector["power_kw"], 7.4)
        self.assertEqual(connector["raw"]["power_source_unit"], "kWh")
        self.assertEqual(connector["raw"]["power_source_text"], "7,4 kWh")
        self.assertTrue(connector["raw"]["power_unit_inferred"])
        self.assertIn("interpreted", connector["raw"]["power_normalization_note"])
        self.assertFalse(connector["provenance"]["live_authority"])
        self.assertTrue(data["tariff"]["unconditional_free"])

    def test_actual_free_first_hour_and_paid_later_never_become_free_kwh(self):
        data = parse_canarias_detail(fixture("p53018"), "https://canariasterecarga.com/es/p53018/")
        tariff = data["tariff"]
        self.assertIsNone(tariff["energy_eur_per_kwh"])
        self.assertEqual(tariff["hour_eur"], 2.0)
        self.assertTrue(tariff["conditional_free"])
        self.assertFalse(tariff["unconditional_free"])
        self.assertIn("Gratis 1ªhora", tariff["conditions"])
        self.assertEqual([c["type"] for c in tariff["components"]], ["FREE_PERIOD", "TIME"])

    def test_actual_unpriced_page_keeps_hardware_contact_and_source_image(self):
        data = parse_canarias_detail(fixture("p00275"), "https://canariasterecarga.com/es/p00275/")
        self.assertEqual(data["port_count"], 10)
        self.assertEqual(data["bay_count"], 8)
        self.assertIsNone(data["tariff"]["energy_eur_per_kwh"])
        self.assertEqual(data["tariff"]["components"], [])
        self.assertIn("llame al", data["tariff"]["text"])
        self.assertEqual(data["contact"]["phones"], ["630 034 636"])
        self.assertEqual(data["contact"]["scope"], "directory_contact")
        self.assertEqual(data["photos"][0]["url"], "https://canariasterecarga.com/wp-content/uploads/2024/03/martianez.jpg")
        self.assertEqual(data["photos"][0]["kind"], "source_published_image")
        self.assertEqual(data["photos"][0]["section"], "tariff")

    def test_actual_legacy_paragraph_separates_two_sockets_and_literal_kw(self):
        url = "https://canariasterecarga.com/es/punto-de-recarga-hotel-villa-del-conde/"
        data = parse_canarias_detail(fixture("punto-de-recarga-hotel-villa-del-conde"), url)
        self.assertTrue(data["recognized"])
        self.assertEqual(data["station_name"], "Hotel Villa del Conde")
        self.assertEqual([(c["count"], c["power_kw"]) for c in data["connectors"]], [(1, 7.4), (1, 2.3)])
        self.assertTrue(all(c["type"] == "Unknown" for c in data["connectors"]))
        self.assertTrue(all(c["raw"]["power_basis"] == "source_literal_kw" for c in data["connectors"]))
        self.assertEqual(data["port_count"], 2)
        self.assertTrue(data["tariff"]["unconditional_free"])
        self.assertEqual(data["photos"], [])  # An external hotel marketing image is not a source-hosted charger photo.

    def test_footer_menu_and_script_cannot_supply_station_facts(self):
        html = ('<header><h1>Tarifa</h1>Gratis</header>' + page() +
                '<nav><h1>Conectores</h1>CCS2 x99 300 kW</nav>'
                '<footer><h1>Contacto</h1>private@example.invalid 911111111'
                '<img src="https://canariasterecarga.com/wp-content/uploads/2026/01/footer.jpg"></footer>'
                '<script>Tarifa 0 €/kWh. Horario 24/7.</script>')
        data = parse_canarias_detail(html, URL)
        self.assertEqual(data["tariff"]["energy_eur_per_kwh"], 0.28)
        self.assertEqual(data["port_count"], 2)
        self.assertEqual(data["contact"], {})
        self.assertEqual(data["hours"], "")
        self.assertEqual(data["photos"], [])

    def test_unrelated_homepage_404_and_missing_root_are_not_station_details(self):
        for html in ["<h1>404 Page not found</h1>",
                     '<div id="page-content"><h1>Inicio</h1>Conectores Tarifa Gratis</div><footer>Contacto</footer>',
                     '<div id="page-content"><h1>Localización</h1><p>Our office</p></div>']:
            with self.subTest(html=html):
                data = parse_canarias_detail(html, URL)
                self.assertFalse(data["recognized"])
                self.assertEqual(data["connectors"], [])
        self.assertFalse(parse_canarias_detail(page().replace("Punto de Recarga – Test", "404 Not found"), URL)["recognized"])

    def test_missing_tariff_does_not_discard_literal_charger_hardware(self):
        data = parse_canarias_detail(page().replace('<h1>Tarifa</h1><p>0,28 €/kWh</p>', ''), URL)
        self.assertTrue(data["recognized"])
        self.assertEqual(data["max_power_kw"], 22.0)
        self.assertEqual(data["tariff"]["components"], [])

    def test_description_access_hours_and_contact_have_separate_provenance(self):
        extra = ('<h2>Descripción</h2><p>In underground parking, level -2.</p>'
                 '<h2>Acceso</h2><p>RFID card required; ask reception.</p>'
                 '<h2>Horario</h2><p>09:00–22:00</p><h2>Contacto</h2><p>+34 900 123 456 help@example.com</p>')
        data = parse_canarias_detail(page(extra=extra), URL)
        self.assertEqual(data["description"], "In underground parking, level -2.")
        self.assertEqual(data["access"], "RFID card required; ask reception.")
        self.assertEqual(data["hours"], "09:00–22:00")
        self.assertEqual(data["contact"]["emails"], ["help@example.com"])
        self.assertEqual(data["contact"]["scope"], "source_published_contact")
        self.assertEqual(data["field_provenance"]["access"]["source_url"], URL)

    def test_unknown_icon_is_not_guessed_and_generic_plug_keeps_contextual_rating(self):
        connector = ('<div class="elementor-icon-box-wrapper"><i class="fas fa-plug"></i>'
                     '<h3>x1</h3><p>3,4 kWh</p></div>')
        data = parse_canarias_detail(page(connector=connector), URL)
        self.assertEqual(data["connectors"][0]["type"], "Unknown")
        self.assertEqual(data["connectors"][0]["power_kw"], 3.4)
        self.assertTrue(data["connectors"][0]["raw"]["power_unit_inferred"])

    def test_battery_energy_or_implausible_value_never_becomes_power(self):
        for text in ["Type 2 x1 Battery capacity 50 kWh", "Type 2 x1 999 kW", "Type 2 x1 0 kW"]:
            data = parse_canarias_detail(page(connector="<p>" + text + "</p>"), URL)
            self.assertIsNone(data["max_power_kw"])
        # Missing count stays unknown, rather than silently assuming one socket.
        data = parse_canarias_detail(page(connector='<div class="elementor-icon-box-wrapper">Type 2 22 kW</div>'), URL)
        self.assertIsNone(data["port_count"])

    def test_session_fee_and_minimum_spend_remain_different_components(self):
        data = parse_canarias_detail(page(tariff="<p>0,28 €/kWh + 1€ por sesión. Mínimo 2€</p>"), URL)
        self.assertEqual([(c["type"], c["amount"]) for c in data["tariff"]["components"]],
                         [("ENERGY", 0.28), ("MINIMUM", 2.0), ("FLAT", 1.0)])
        self.assertFalse(data["tariff"]["usable_as_simple_energy_price"])

    def test_multiple_energy_rates_are_not_flattened_into_one_price(self):
        data = parse_canarias_detail(page(tariff="<p>AC 0,28 €/kWh; DC 0,45 €/kWh</p>"), URL)
        self.assertEqual(data["tariff"]["energy_rates_eur_per_kwh"], [0.28, 0.45])
        self.assertIsNone(data["tariff"]["energy_eur_per_kwh"])
        self.assertFalse(data["tariff"]["usable_as_simple_energy_price"])

    def test_legacy_price_conditions_and_negative_malformed_rates_are_not_simplified(self):
        data = parse_canarias_detail(page(tariff="<p>0,28 €/kWh solo clientes, según horario.</p>"), URL)
        self.assertEqual(data["tariff"]["energy_eur_per_kwh"], 0.28)
        self.assertTrue(data["tariff"]["conditions"])
        self.assertFalse(data["tariff"]["usable_as_simple_energy_price"])
        self.assertEqual(parse_canarias_detail(page(tariff="<p>-0,28 €/kWh</p>"), URL)["tariff"]["components"], [])

    def test_source_page_identity_and_numeric_map_marker_are_preserved(self):
        html = ('<link rel="canonical" href="https://canariasterecarga.com/es/mapa/p00246/">' +
                page(extra='<iframe src="https://maps.google.com/maps?q=28.123,-16.456&amp;output=embed"></iframe>'))
        data = parse_canarias_detail(html, URL)
        self.assertEqual(data["name"], "Punto de Recarga – Test")
        self.assertEqual(data["station_name"], "Test")
        self.assertEqual(data["address"], "Test Street 1")
        self.assertEqual(data["canonical_url"], "https://canariasterecarga.com/es/mapa/p00246/")
        self.assertEqual(data["coordinates"]["lat"], 28.123)
        self.assertEqual(data["coordinates"]["lon"], -16.456)
        for query in ["q=Test+Street+1", "center=28.123,-16.456", "ll=28.123,-16.456"]:
            other = parse_canarias_detail(page(extra='<iframe src="https://maps.google.com/maps?' + query + '"></iframe>'), URL)
            self.assertIsNone(other["coordinates"])

    def test_safe_urls_reject_other_origins_secrets_routes_queries_and_malformed_ports(self):
        self.assertEqual(safe_detail_url("/es/mapa/p61008/"), "https://canariasterecarga.com/es/mapa/p61008/")
        self.assertEqual(safe_detail_url("https://canariasterecarga.com/es/parking-adeje"), "https://canariasterecarga.com/es/parking-adeje/")
        for url in ["http://canariasterecarga.com/es/p1/", "https://canariasterecarga.com.evil/es/p1/",
                    "https://u:p@canariasterecarga.com/es/p1/", "https://canariasterecarga.com:443/es/p1/",
                    "https://canariasterecarga.com:bad/es/p1/", "https://[invalid/es/p1/",
                    "/es/p1/?token=secret", "/es/p1/#fragment", "/es/%2e%2e/p1/", "/es/../p1/", "/es/abc/../p1/",
                    "/es/mi-cuenta1/", "/es/checkout/", "javascript:alert(1)", "//evil.example/es/p1/",
                    "https://canariasterecarga.com\\@evil.example/es/p1/"]:
            with self.subTest(url=url):
                self.assertEqual(safe_detail_url(url), "")

    def test_photo_urls_unwrap_only_same_origin_uploads_and_skip_decorations(self):
        self.assertEqual(safe_photo_url("https://i0.wp.com/canariasterecarga.com/wp-content/uploads/2024/03/charger.jpg?resize=500%2C300&ssl=1"),
                         "https://canariasterecarga.com/wp-content/uploads/2024/03/charger.jpg")
        for url in ["https://i0.wp.com/evil.example/wp-content/uploads/2024/03/image.jpg",
                    "https://evil.example/wp-content/uploads/2024/03/image.jpg",
                    "https://canariasterecarga.com/wp-content/uploads/../private.jpg",
                    "https://canariasterecarga.com/wp-content/uploads/2024/03/evil.svg",
                    "data:image/png;base64,untrusted"]:
            self.assertEqual(safe_photo_url(url), "")
        extra = ('<img src="https://canariasterecarga.com/wp-content/uploads/2024/03/logo.png">'
                 '<img width="40" height="40" src="https://canariasterecarga.com/wp-content/uploads/2024/03/mystery.png">'
                 '<img width="500" height="300" src="https://canariasterecarga.com/wp-content/uploads/2024/03/charger.jpg">')
        self.assertEqual(len(parse_canarias_detail(page(extra=extra), URL)["photos"]), 1)

    def test_html_depth_size_and_duplicate_roots_fail_closed(self):
        for html in ["x" * (MAX_HTML_CHARS + 1), "<div>" * 101 + page(), page() + page()]:
            self.assertFalse(parse_canarias_detail(html, URL)["recognized"])
        self.assertFalse(parse_canarias_detail(page(), "https://evil.example/es/p1/")["recognized"])

    def test_identity_rejects_same_url_for_a_different_island_or_station(self):
        detail = {"recognized": True, "station_name": "Puerto de Los Cristianos, Atraque Fred Olsen",
                  "address": "Puerto de Pescadores, 38650 Arona, Santa Cruz de Tenerife"}
        for marker in [
                {"name": "Barceló Fuerteventura Thalasso", "address": "Avenida del Castillo, 35610 Caleta de Fuste"},
                {"name": "C.C. Carrefour Añaza", "address": "Urbanización Añaza, 38111 Santa Cruz de Tenerife"}]:
            matched, reason = page_identity_matches({**marker, "source_url": "https://canariasterecarga.com/es/p66087/"}, detail)
            self.assertFalse(matched)
            self.assertIn("conflict", reason)
        self.assertFalse(page_identity_matches({"name": "Test", "source_url": URL}, {"recognized": False})[0])

    def test_identity_matches_abbreviated_names_with_address_or_nearby_explicit_point(self):
        detail = {"recognized": True, "station_name": "Ayuntamiento de Agüimes",
                  "address": "Calle Dr. Joaquín Artiles, 35260 Agüimes, Las Palmas"}
        marker = {"name": "Ayto. Agüimes", "address": "C/ Dr. Joaquin Artiles, 35260 Aguimes"}
        self.assertTrue(page_identity_matches(marker, detail)[0])
        point_detail = {"recognized": True, "station_name": "Ayuntamiento de Santa Cruz de Tenerife",
                        "coordinates": {"lat": 28.4601, "lon": -16.2501}}
        point_marker = {"name": "Ayto. Santa Cruz", "lat": 28.4600, "lon": -16.2500}
        matched, reason = page_identity_matches(point_marker, point_detail)
        self.assertTrue(matched)
        self.assertIn("nearby_explicit_coordinates", reason)

    def test_far_explicit_marker_and_conflicting_floors_cannot_be_overridden_by_names(self):
        marker = {"name": "C.C. Test", "address": "Calle Example, 1, 38650 Arona", "lat": 28.46, "lon": -16.25}
        detail = {"recognized": True, "station_name": "C.C. Test", "address": marker["address"],
                  "coordinates": {"lat": 29.0, "lon": -13.0}}
        self.assertEqual(page_identity_matches(marker, detail), (False, "detail_explicit_coordinate_conflict"))
        detail.pop("coordinates")
        self.assertFalse(page_identity_matches({**marker, "name": "C.C. Test Planta -1"},
                                              {**detail, "station_name": "C.C. Test Planta -2"})[0])

    def test_primary_popup_can_correct_stale_properties_but_cannot_prove_another_source_identity(self):
        detail = {"recognized": True, "station_name": "Club de Golf Meloneras",
                  "address": "Ctra. GC-500, S/N, 35100 Maspalomas, Las Palmas"}
        marker = {"source": "Canarias te recarga public map", "name": "BP El Doctoral",
                  "address": "Maspalomas Oasis Club, Avenida Touroperador Air Marin, 35100 Maspalomas",
                  "raw": {"popup_name": "Club de Golf Meloneras", "popup_address": detail["address"]}}
        matched, reason = page_identity_matches(marker, detail)
        self.assertTrue(matched)
        self.assertTrue(reason.endswith("marker_popup"))
        self.assertFalse(page_identity_matches({**marker, "source": "OpenChargeMap"}, detail)[0])

    def test_popup_address_error_does_not_hide_a_corroborated_property_address(self):
        detail = {"recognized": True, "station_name": "Hotel Sábila",
                  "address": "Avda. Ernesto Sarti, 5, 38679 Costa Adeje, Santa Cruz de Tenerife"}
        marker = {"source": "Canarias te recarga public map", "name": "Hotel Sábila", "address": detail["address"],
                  "raw": {"popup_name": "Hotel Sábila", "popup_address": detail["address"].replace("38679", "35679")}}
        matched, reason = page_identity_matches(marker, detail)
        self.assertTrue(matched)
        self.assertTrue(reason.endswith("marker_properties"))

    def test_popup_heading_and_address_exclude_buttons_bay_text_and_tariff(self):
        popup = ('<h1>C.C. La Villa</h1><p><strong>Autopista del norte, TF-5, 36, 38312 La Orotava</strong></p>'
                 '<div>Basic - 2 €/Hora</div><p><a href="/es/p54003/"><button>Disponible 2 plazas</button></a></p>')
        self.assertEqual(popup_station_identity(popup), {"name": "C.C. La Villa",
                         "address": "Autopista del norte, TF-5, 36, 38312 La Orotava"})
        self.assertEqual(popup_station_identity('<h2>Charger</h2><p>Gratis</p><p>Disponible 2 plazas</p>')["address"], "")
        self.assertEqual(popup_station_identity('<h1>Ayto Puerto</h1><p>Plaza de Europa, 1, 38400 Puerto de la Cruz</p>')["address"],
                         "Plaza de Europa, 1, 38400 Puerto de la Cruz")

    def test_wordpress_post_footer_and_split_inline_heading_never_pollute_station_facts(self):
        html = ('<title>Punto de Recarga – Plaza Poeta Luis Feria | Canarias te recarga</title>'
                '<div id="page-content"><p>Indic<span>aciones</span></p><p>Calle Leoncio Rodríguez, 38005 Santa Cruz</p>'
                '<h3>Tomas/Conectores</h3><p>Type 2 x1 7.4 kW</p><h3>Tarifas</h3><p>0 €</p>'
                '<div class="article-footer">23 febrero, 2016</div>'
                '<ul class="article-navigation"><li>Entrada siguiente: Tarifa 0,99 €/kWh</li></ul></div>')
        data = parse_canarias_detail(html, URL)
        self.assertTrue(data["recognized"])
        self.assertEqual(data["station_name"], "Plaza Poeta Luis Feria")
        self.assertEqual(data["address"], "Calle Leoncio Rodríguez, 38005 Santa Cruz")
        self.assertEqual(data["tariff"]["text"], "0 €")
        self.assertTrue(data["tariff"]["unconditional_free"])

    def test_misordered_contact_heading_does_not_hide_explicit_hardware_rows(self):
        connector = ('<h1>Información de contacto</h1><div class="elementor-icon-box-wrapper">'
                     '<h3>Type 2 x3</h3><p>43 kWh</p></div><p>help@example.com</p>')
        data = parse_canarias_detail(page(connector=connector), URL)
        self.assertEqual(data["port_count"], 3)
        self.assertEqual(data["max_power_kw"], 43.0)

    def test_construction_notice_is_preserved_as_source_access_information(self):
        html = page().replace("Test Street 1", "EN CONSTRUCCIÓN Test Street 1")
        data = parse_canarias_detail(html, URL)
        self.assertEqual(data["address"], "Test Street 1")
        self.assertEqual(data["access"], "EN CONSTRUCCIÓN")
        self.assertTrue(any("EN CONSTRUCCIÓN" in warning for warning in data["warnings"]))


if __name__ == "__main__":
    unittest.main()
