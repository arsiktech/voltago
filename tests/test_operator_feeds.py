"""Fixture tests for the operator open-feed adapters: Etecnic, Atlante, Zunder, Repsol.

Fixtures are trimmed copies of the real public payloads fetched on 2026-10-12. Stdlib
unittest only, no network.
"""

import sys
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import ingest_sources as ing  # noqa: E402
import source_transport as transport  # noqa: E402
from station_identity import identity_keys  # noqa: E402


ETECNIC_ROWS = [
    {"id": 1, "name": "Pescador Sud - EdRSR 1", "status": 0, "power": 22.0, "address": "Carrer Robert Gerhard, 2",
     "lat": "41.064699", "lon": "1.0576201", "phases": 3, "test_errors": True, "mobile_app_manage": True, "show_map": True,
     "charger_sockets": [{"id": 1, "socket_number": 1, "status": 0, "connector_type_id": 2},
                         {"id": 2, "socket_number": 2, "status": 0, "connector_type_id": 2}]},
    {"id": 2, "name": "Palma - MELIB 1", "status": 1, "power": 50.0, "address": "Carrer X", "lat": "39.57", "lon": "2.65",
     "show_map": True, "charger_sockets": [{"id": 3, "status": 1, "connector_type_id": 1},
                                           {"id": 4, "status": 1, "connector_type_id": 3},
                                           {"id": 5, "status": 1, "connector_type_id": 7}]},
    {"id": 3, "name": "Hidden", "lat": "40.4", "lon": "-3.7", "show_map": False, "charger_sockets": []},
    {"id": 4, "name": "Hotel Metropolis", "lat": "42.51035792", "lon": "1.54068426", "show_map": True, "charger_sockets": []},
    {"id": 5, "name": "Figueres - SELBA EdRSR 3", "lat": "0.0", "lon": "0.0", "show_map": True, "charger_sockets": []},
    {"id": 6, "name": "SANTO DOMINGO GBT 1", "lat": "25.750424", "lon": "-100.255821", "show_map": True, "charger_sockets": []},
]


def atlante_location(loc_id, country, status, lat, lon):
    return {
        "id": loc_id, "party_id": "ATE", "name": "Atlante  - Abanto Gallarta", "street": "Plaza de Eusko Gudariak",
        "city": "Abanto y Ciérvana-Abanto Zierbena", "postal_code": "48500", "country": country, "site_status": status,
        "last_updated": "2026-10-12 03:27:31",
        "evses": [
            {"evse_id": "ES*ATE*E00026*1", "status": "AVAILABLE", "coordinates": {"latitude": lat, "longitude": lon},
             "connectors": [{"id": "1", "standard": "IEC_62196_T2_COMBO", "format": "CABLE", "power_type": "DC", "max_electric_power": 60000}]},
            {"evse_id": "ES*ATE*E00026*2", "status": "CHARGING", "coordinates": {"latitude": lat, "longitude": lon},
             "connectors": [{"id": "2", "standard": "CHADEMO", "format": "CABLE", "power_type": "DC", "max_electric_power": 50000}]},
        ],
    }


ZUNDER_PAGE = """
<script>
 var marker4 = new google.maps.Marker({
   position: { lat: 42.71779934, lng: -4.29173329},
   map: map,
   title: 'Alar del Rey - Puerta de la Monta\\'ña',
   icon: { url: '/wp-content/plugins/easycharger/icons/icono-marino.png' }
 });
 marker4.addListener('click', function() {
   xmlhttp.open("GET", "/wp-content/plugins/easycharger/location.php?id=4");
 });
 var marker5 = new google.maps.Marker({
   position: { lat: 41.15, lng: -8.61},
   map: map,
   title: 'ZUNDER Porto',
 });
 marker5.addListener('click', function() {
   xmlhttp.open("GET", "/wp-content/plugins/easycharger/location.php?id=5");
 });
 var marker6 = new google.maps.Marker({ map: map });
</script>
"""

REPSOL_ITEMS = [
    {"id": "10005", "nombre": "Repsol ES, Cerdeira", "x": -3.611191, "y": 40.424397,
     "direccion": "AVENIDA DE CANILLEJAS A VICÁLVARO, 147", "cp": "28022", "localidad": "Madrid", "provincia": "Madrid",
     "pais": "España", "velocidad": ["Rápida"], "platform": 1, "provinceFormated": "madrid", "locationFormated": "madrid",
     "directionFormated": "avenida-de-canillejas-a-vicalvaro-147"},
    {"id": "10006", "nombre": "Repsol Lisboa", "x": -9.14, "y": 38.72, "pais": "Portugal", "velocidad": ["Lenta"]},
    {"id": "10007", "nombre": "Repsol Lenta", "x": -0.37, "y": 39.47, "pais": "España", "velocidad": ["Lenta"],
     "provinceFormated": "valencia", "locationFormated": "valencia", "directionFormated": "calle ../../x"},
]


class SpainOutlineTests(unittest.TestCase):
    def test_border_towns_land_on_the_right_side(self):
        cases = {
            (38.88, -6.97): True,    # Badajoz
            (38.88, -7.16): False,   # Elvas, Portugal
            (38.72, -9.14): False,   # Lisbon
            (41.15, -8.61): False,   # Porto
            (43.60, 3.88): False,    # Montpellier
            (42.51, 1.54): False,    # Andorra la Vella
            (42.43, 1.93): True,     # Puigcerdà
            (36.13, -5.45): True,    # Algeciras
            (28.46, -16.25): True,   # Santa Cruz de Tenerife
            (39.57, 2.65): True,     # Palma
            (35.89, -5.32): True,    # Ceuta
            (35.29, -2.94): True,    # Melilla
        }
        for (lat, lon), expected in cases.items():
            self.assertEqual(ing.likely_in_spain(lat, lon), expected, (lat, lon))
        self.assertFalse(ing.likely_in_spain(None, 1.0))


class EtecnicTests(unittest.TestCase):
    def test_spanish_visible_chargers_only_with_raw_status_and_no_price(self):
        rows = ing.parse_etecnic_chargers(ETECNIC_ROWS)
        self.assertEqual([r["name"] for r in rows], ["Pescador Sud - EdRSR 1", "Palma - MELIB 1"])
        first, melib = rows
        self.assertIsNone(first["price_min"])
        self.assertEqual(first["charge_claim"]["type"], "price_unknown")
        self.assertEqual(first["status"], "VERIFY")
        self.assertIsNone(first["max_power_kw"], "mixed kW/amp units must never be published as power")
        self.assertEqual(first["raw"]["power_raw"], 22.0)
        self.assertEqual(first["connector_labels"], [], "Etecnic connector ids have no public legend: never guess Type 2")
        self.assertIsNone(first["tesla_ok"], "unknown connectors are unknown, not Tesla-compatible or incompatible")
        self.assertEqual(first["total_evse"], 2)
        self.assertEqual(first["raw"]["network"], "Etecnic platform (operator not established)")
        self.assertIn("MELIB", melib["raw"]["network"], "MELIB only because the row name says so")
        self.assertEqual(melib["connector_labels"], [])
        self.assertEqual({c["label"] for c in melib["connectors"]}, {"Unknown connector"})
        self.assertEqual(sorted(c["raw"]["etecnic_connector_type_id"] for c in melib["connectors"]), [1, 3, 7],
                         "raw ids are kept for later mapping")
        self.assertEqual(melib["raw"]["socket_status_codes"], [1, 1, 1])

    def test_balearic_location_alone_never_implies_melib(self):
        rows = ing.parse_etecnic_chargers([
            {"id": 253, "name": "SIXT PMI_253_22kW", "lat": "39.55", "lon": "2.73", "show_map": True,
             "charger_sockets": []},
        ])
        self.assertEqual(len(rows), 1)
        self.assertNotIn("MELIB", rows[0]["raw"]["network"])
        self.assertNotIn("MELIB", rows[0]["sources"][0]["evidence"])

    def test_identity_key_is_the_etecnic_charger_id(self):
        row = ing.parse_etecnic_chargers(ETECNIC_ROWS[:1])[0]
        self.assertIn(("Etecnic EVcharge public charger map", "etecnic_charger_id", "1"), identity_keys(row))


class AtlanteTests(unittest.TestCase):
    def test_only_operational_spanish_sites(self):
        rows = ing.parse_atlante_locations([
            atlante_location("ES00056", "ESP", None, 43.316551, -3.073291),
            atlante_location("ES00057", "ES", "WAITING FOR CONNECTION", 40.4, -3.7),
            atlante_location("PT00001", "PRT", None, 41.13, -8.63),
        ])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "Atlante - Abanto Gallarta")
        self.assertEqual(row["max_power_kw"], 60.0)
        self.assertTrue(row["fast"])
        self.assertIsNone(row["price_min"])
        self.assertEqual(row["status"], "VERIFY")
        self.assertTrue(all(c["status"] == "UNKNOWN" for c in row["connectors"]), "EVSE status must not become live availability")
        self.assertEqual(row["raw"]["evse_status_counts"], {"AVAILABLE": 1, "CHARGING": 1})
        self.assertEqual(row["raw"]["evse_ids"], ["ES*ATE*E00026*1", "ES*ATE*E00026*2"])
        self.assertEqual(row["connector_labels"], ["CCS2", "CHAdeMO"])
        self.assertIn(("Atlante public charger map", "atlante_location_id", "ES00056"), identity_keys(row))

    def test_unknown_standards_are_not_guessed_or_defaulted(self):
        loc = atlante_location("ES00060", "ES", None, 40.4, -3.7)
        for evse in loc["evses"]:
            evse["connectors"][0]["standard"] = "XYZ_PLUG"
        row = ing.parse_atlante_locations([loc])[0]
        self.assertEqual(row["connector_labels"], [], "no Type 2 default and no title-cased invented type")
        self.assertIsNone(row["tesla_ok"])
        self.assertEqual({c["label"] for c in row["connectors"]}, {"Unknown connector"})
        self.assertEqual(row["connectors"][0]["raw"]["standard"], "XYZ_PLUG")
        bare = atlante_location("ES00061", "ES", None, 40.4, -3.7)
        for evse in bare["evses"]:
            evse["connectors"] = []
        row = ing.parse_atlante_locations([bare])[0]
        self.assertEqual(row["connector_labels"], [])
        self.assertIsNone(row["tesla_ok"])


class ZunderTests(unittest.TestCase):
    def test_inline_markers_parse_and_portugal_is_dropped(self):
        rows = ing.parse_zunder_map_page(ZUNDER_PAGE)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "Alar del Rey - Puerta de la Monta'ña")
        self.assertEqual(row["raw"]["zunder_location_id"], "4")
        self.assertEqual(row["connector_labels"], [], "no connector evidence must not default to Type 2")
        self.assertIsNone(row["price_min"])
        self.assertIn(("Zunder public charger map", "zunder_location_id", "4"), identity_keys(row))


class RepsolTests(unittest.TestCase):
    def test_spanish_points_keep_feed_provenance_without_invented_station_urls(self):
        rows = ing.parse_repsol_charging_points(REPSOL_ITEMS)
        self.assertEqual([r["raw"]["repsol_point_id"] for r in rows], ["10005", "10007"])
        fast, slow = rows
        self.assertEqual(fast["source_url"], ing.REPSOL_FINDER_URL)
        self.assertEqual(fast["primary_source"]["kind"], "official_data")
        self.assertIn("not an exact station/start page", fast["primary_source"]["warning"])
        self.assertFalse(fast["fast"], "a qualitative speed class cannot establish numeric power")
        self.assertIsNone(fast["max_power_kw"])
        self.assertEqual(fast["raw"]["speed_classes"], ["Rápida"])
        self.assertIn("Rápida", fast["sources"][0]["evidence"])
        self.assertFalse(slow["fast"])
        self.assertIsNone(slow["max_power_kw"])
        self.assertEqual(slow["source_url"], ing.REPSOL_FINDER_URL, "the feed does not supply a verified station-page URL")
        self.assertEqual(fast["connector_labels"], [])
        self.assertIsNone(fast["price_min"])

    def test_transport_allows_only_the_exact_charging_point_list_post(self):
        ok = urllib.request.Request(ing.REPSOL_SEARCH_URL, data=b"", method="POST")
        self.assertEqual(transport.classify_post_exception(ok), "repsol-charging-points-list")
        for url, body in (
            ("https://www.repsol.es/bin/repsol/searchmiddleware/station-search.json?action=search&idioma=es&tipo=1,2", b""),
            ("https://www.repsol.es/bin/repsol/searchmiddleware/station-search.json?action=search&idioma=es&tipo=3&x=1", b""),
            (ing.REPSOL_SEARCH_URL, b"{}"),
            ("https://www.repsol.es/bin/repsol/other.json?action=search&idioma=es&tipo=3", b""),
            ("http://www.repsol.es/bin/repsol/searchmiddleware/station-search.json?action=search&idioma=es&tipo=3", b""),
        ):
            with self.assertRaises(transport.SourcePolicyBlocked, msg=url):
                transport.classify_post_exception(urllib.request.Request(url, data=body, method="POST"))


class AdapterRegistrationTests(unittest.TestCase):
    def test_operator_feeds_are_active(self):
        names = [fn.__name__ for fn in ing.ACTIVE_SOURCE_ADAPTERS]
        for name in ("source_etecnic", "source_atlante", "source_zunder", "source_repsol"):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()
