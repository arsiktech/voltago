"""Fixture tests for the Tenerife-first public sources: Canarias te recarga + MapaREVE.

Both fixtures are trimmed excerpts of the real public payloads (verified 2026-09-16):
the Canarias te recarga map export (`/maps/export/geojson/4/`) and the public MapaREVE
OCPI web API (`/api/public/v1/locations`). Stdlib unittest only, no network.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import ingest_sources as ing  # noqa: E402
import station_truth  # noqa: E402


def ct_feature(record_id, name, lon, lat, icon, popup_html):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "id": record_id,
            "name": name,
            "address": "Avda. Tres de Mayo, 5, 38003 Santa Cruz de Tenerife, España",
            "zoom": "13.5",
            "icon": f"https://canariasterecarga.com/wp-content/uploads/maps-marker-pro/icons/{icon}",
            "popup": popup_html,
        },
    }


BADGE_GRATIS = (
    '<h1><strong>Intercambiador Santa Cruz de Tenerife</strong></h1>'
    '<p><strong>Avda. Tres de Mayo, 5, 38003 Santa Cruz de Tenerife</strong></p>'
    '<p><img src="https://canariasterecarga.com/wp-content/uploads/2022/07/ev-plug-type2.svg" alt="x1" /></p>'
    '<div style="margin: auto;background-color: #fff;width: 155px;padding: 7px;">Gratis</div>'
    '<p><a href="https://canariasterecarga.com/es/p61002">Más Información</a>'
    '<span>Disponible 2 plazas</span></p>'
)
ICON_ONLY = (
    '<p><img src="https://canariasterecarga.com/wp-content/uploads/2016/01/g7526-1.png" alt="g7526" /></p>'
    "<h2>Vía P.C/Acusa</h2>"
    '<div><img src="https://canariasterecarga.com/wp-content/uploads/2016/02/conector2.png" alt="conector2" /> x1</div>'
    '<p><a href="https://canariasterecarga.com/es/punto-de-recarga-via-pc-acusa/">Más Información</a></p>'
)
BADGE_SUPER = (
    "<h1>Pcan Alcalá</h1>"
    '<p><img src="https://canariasterecarga.com/wp-content/uploads/2022/07/ev-plug-ccs2.svg" alt="" />'
    '<img src="https://canariasterecarga.com/wp-content/uploads/2022/07/ev-plug-type2.svg" alt="x1" /></p>'
    '<div style="width: 155px">Super- 0,38€/kWh</div>'
    '<p><span>Disponible 1 plaza</span></p>'
)
BADGE_HOURLY = (
    "<h1>C.C. La Villa</h1>"
    '<div style="width: 155px">Basic - 2 €/ Hora</div>'
    '<p><span>Disponible 2 plazas</span></p>'
)
BADGE_CONDITIONAL_GRATIS = (
    "<h1>Ayuntamiento La Matanza</h1>"
    '<div style="width: 155px">Gratis - 1º Hora</div>'
    '<div style="width: 155px">Basic - 2 €/ Hora</div>'
    '<p><span>Disponible 2 plazas</span></p>'
)


def ct_payload(features):
    return {"type": "FeatureCollection", "features": features}


def reve_row(location_id, name, lat, lon, price_components, status="AVAILABLE", power_watts=22080, standard="IEC_62196_T2"):
    return {
        "id": location_id,
        "name": name,
        "address": "C/LOS CARPINTEROS, S/N",
        "postal_code": "38419",
        "coordinates": {"latitude": str(lat), "longitude": str(lon)},
        "status": status,
        "total_evse": 1,
        "owner": {"name": "REPSOL SOLUCIONES ENERGÉTICAS SA", "website": "WWW.REPSOL.COM", "phone": "676461625", "logo": None},
        "evses": [
            {
                "evse_id": f"ES*REP*E{location_id}*1",
                "status": "AVAILABLE",
                "status_updated_at": "2026-09-16T13:48:10.288Z",
                "last_updated": "2026-09-16T13:48:10.288Z",
                "connectors": [
                    {
                        "id": f"connector-{location_id}",
                        "standard": standard,
                        "format": "SOCKET",
                        "max_electric_power": power_watts,
                        "tariffs": [
                            {
                                "tariff": {
                                    "id": "tariff-1",
                                    "currency": "EUR",
                                    "elements": [{"price_components": price_components}],
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }


def reve_pages(rows):
    return [{"data": rows, "pagination": {"page": 1, "per_page": 20, "next": None, "total_count": len(rows), "total_pages": 1}}]


class CanariasTeRecargaParserTests(unittest.TestCase):
    def parse(self, features):
        return ing.parse_canarias_tecarga_geojson(ct_payload(features), minimum_features=1)

    def test_gratis_badge_row_is_a_free_directory_lead(self) -> None:
        records = self.parse([ct_feature("4", "Intercambiador Santa Cruz de Tenerife", -16.25238, 28.459126, "icono_free.png", BADGE_GRATIS)])
        self.assertEqual(len(records), 1)
        row = records[0]
        self.assertEqual(row["source"], ing.CANARIAS_TE_RECARGA_SOURCE)
        self.assertEqual(row["source_url"], "https://canariasterecarga.com/es/p61002")
        self.assertEqual(row["price_min"], 0.0)
        self.assertTrue(row["is_free"])
        self.assertEqual(row["reliability"], "directory-page")
        self.assertEqual(row["status"], "VERIFY")
        self.assertEqual(row["city"], "Santa Cruz de Tenerife")
        self.assertEqual(row["connector_labels"], ["Type 2"])
        self.assertEqual(row["total_evse"], 1)
        self.assertEqual(row["raw"]["tariff_basis"], "popup_badge_gratis")
        self.assertEqual(row["raw"]["marker_badge"], "Gratis")
        self.assertEqual(row["raw"]["popup_bay_snapshot"], "Disponible 2 plazas")
        self.assertEqual(row["available"], 0)
        # The bay text is a snapshot, never live telemetry.
        self.assertIn("not live telemetry", row["sources"][0]["evidence"])
        self.assertEqual(row["source_integration"]["mode"], "public_map_snapshot")
        self.assertFalse(row["source_integration"]["live_status_integrated"])

    def test_icon_only_free_marker_is_free(self) -> None:
        records = self.parse([ct_feature("73", "Vía P.C/Acusa", -16.24786, 28.46814, "icono_free.png", ICON_ONLY)])
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["is_free"])
        self.assertEqual(records[0]["raw"]["tariff_basis"], "marker_icon_free")
        # conector1/conector2.png do not name a connector type: hardware stays unknown.
        self.assertEqual(records[0]["connector_labels"], [])
        self.assertIsNone(records[0]["total_evse"])

    def test_paid_badge_wins_over_free_icon(self) -> None:
        # Real contradiction (C.C. Galeón Outlet, verified 2026-09-16): the marker icon is
        # the free icon while the badge (and the station page) publish a paid tariff.
        records = self.parse([ct_feature("254", "C.C. Galeón Outlet", -16.72, 28.09, "icono_free.png", BADGE_SUPER)])
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["is_free"])
        self.assertEqual(records[0]["price_min"], 0.38)
        self.assertEqual(records[0]["price_claim_type"], "confirmed_paid")
        self.assertEqual(records[0]["raw"]["tariff_basis"], "popup_kwh_price")

    def test_hourly_and_conditional_gratis_badges_are_not_free_claims(self) -> None:
        records = self.parse([
            ct_feature("6", "C.C. La Villa", -16.54728, 28.396082, "icono_mega.png", BADGE_HOURLY),
            ct_feature("256", "Ayuntamiento La Matanza", -16.53, 28.45, "icono_mega.png", BADGE_CONDITIONAL_GRATIS),
        ])
        # Both stay on the map, but neither badge becomes a free or €/kWh claim.
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertFalse(record["is_free"])
            self.assertIsNone(record["price_min"])
            self.assertEqual(record["price_claim_type"], "price_unknown_lead")
            self.assertIn(record["raw"]["tariff_basis"], {"popup_conditional_gratis", "popup_hourly_price"})

    def test_rows_without_tariff_evidence_are_price_unknown(self) -> None:
        popup = '<p><img src="https://canariasterecarga.com/wp-content/uploads/maps-marker-pro/icons/marcador-4.png" /></p>'
        records = self.parse([ct_feature("9", "Sin precio", -16.25, 28.46, "marcador-4.png", popup)])
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["price_min"])
        self.assertEqual(records[0]["raw"]["tariff_basis"], "no_tariff_evidence")

    def test_paid_icon_class_keeps_its_class_price(self) -> None:
        records = self.parse([ct_feature("74", "Icono mega", -16.25, 28.47, "icono_mega.png", ICON_ONLY)])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["price_min"], 0.45)
        self.assertEqual(records[0]["price_claim_type"], "confirmed_paid")

    def test_cheap_kwh_badge_stays_cheap_with_its_printed_price(self) -> None:
        # Not present in today's export (all €/kWh badges are > €0.25), but the parser path
        # must stay honest if the map ever publishes a cheap tariff class.
        popup = '<h1>Cheap</h1><div style="width: 155px">Mini - 0,25€/kWh</div>'
        records = self.parse([ct_feature("cheap-1", "Cheap Charger", -16.25, 28.46, "icono_mini.png", popup)])
        self.assertEqual(len(records), 1)
        row = records[0]
        self.assertEqual(row["price_min"], 0.25)
        self.assertTrue(row["is_cheap"])
        self.assertFalse(row["is_free"])
        self.assertEqual(row["raw"]["tariff_basis"], "popup_kwh_price")
        self.assertIn("0.25 €/kWh", row["sources"][0]["evidence"])
        self.assertIn("0.25", row["free_condition"])

    def test_short_export_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "shrank"):
            ing.parse_canarias_tecarga_geojson(
                ct_payload([ct_feature("4", "One", -16.25, 28.46, "icono_free.png", BADGE_GRATIS)]),
                minimum_features=5,
            )


class MapaReveParserTests(unittest.TestCase):
    def parse(self, rows):
        return ing.parse_mapareve_public_pages(reve_pages(rows))

    def test_cheap_energy_component_becomes_a_cheap_lead(self) -> None:
        rows = self.parse([reve_row("03ab142b", "Mercadona, 3836, Los Carpinteros, Tenerife", 28.382335, -16.574184, [{"type": "ENERGY", "price": 0.25}])])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], ing.MAPAREVE_PUBLIC_SOURCE)
        self.assertEqual(row["price_min"], 0.25)
        self.assertTrue(row["is_cheap"])
        self.assertFalse(row["is_free"])
        self.assertEqual(row["reliability"], "directory-page")
        self.assertEqual(row["status"], "VERIFY")
        self.assertEqual(row["source_url"], ing.MAPAREVE_PUBLIC_MAP_PAGE_URL)
        self.assertEqual(row["connector_labels"], ["Type 2"])
        self.assertEqual(row["max_power_kw"], 22.1)
        self.assertEqual(row["last_seen"], "2026-09-16T13:48:10.288Z")
        self.assertIn("cached intelligence snapshot", row["sources"][0]["evidence"])
        self.assertFalse(row["source_integration"]["live_status_integrated"])

    def test_zero_energy_components_are_free(self) -> None:
        rows = self.parse([reve_row("free-1", "Ayuntamiento", 28.4, -16.3, [{"type": "ENERGY", "price": 0.0}])])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_free"])
        self.assertEqual(rows[0]["price_min"], 0.0)
        self.assertEqual(rows[0]["reliability"], "directory-page")

    def test_paid_and_unpriced_rows_are_kept_with_honest_prices(self) -> None:
        rows = self.parse([
            reve_row("p1", "Priced", 28.40, -16.30, [{"type": "ENERGY", "price": 0.38}]),
            reve_row("p2", "Parking only", 28.41, -16.31, [{"type": "PARKING_TIME", "price": 2.0}]),
        ])
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["Priced"]["price_min"], 0.38)
        self.assertEqual(by_name["Priced"]["price_claim_type"], "confirmed_paid")
        # A parking-time component is not a charging price.
        self.assertIsNone(by_name["Parking only"]["price_min"])
        self.assertEqual(by_name["Parking only"]["price_claim_type"], "price_unknown_lead")

    def test_min_energy_price_wins_over_time_windows(self) -> None:
        rows = self.parse([reve_row(
            "windowed", "DISA CRISTIANOS I", 28.0533811, -16.7156587,
            [{"type": "ENERGY", "price": 0.38}, {"type": "ENERGY", "price": 0.25}],
        )])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price_min"], 0.25)
        self.assertEqual(rows[0]["raw"]["energy_prices"], [0.25, 0.38])

    def test_numeric_only_source_name_falls_back_to_address_head(self) -> None:
        rows = self.parse([reve_row("61945052", "61945052640002", 28.390254, -16.58176, [{"type": "ENERGY", "price": 0.25}])])
        self.assertEqual(rows[0]["name"], "C/LOS CARPINTEROS")
        self.assertEqual(rows[0]["raw"]["name_raw"], "61945052640002")

    def test_postcode_contradicting_the_coordinates_is_dropped(self) -> None:
        # Real upstream row: a Costa Adeje (Tenerife) station carrying postal code 14001.
        row = reve_row("22XC11M2M03DB00877", "Parking Costa Adeje", 28.071616, -16.732407, [{"type": "ENERGY", "price": 0.25}])
        row["postal_code"] = "14001"
        rows = self.parse([row])
        self.assertEqual(rows[0]["raw"]["postal_code"], "")
        self.assertNotEqual(rows[0]["city"], "Córdoba")
        # A Canary postcode inside the Canary box is kept (the city label may use it).
        keep = reve_row("keep-1", "Puerto Escondido", 28.47, -16.25, [{"type": "ENERGY", "price": 0.25}])
        keep["postal_code"] = "38002"
        self.assertEqual(self.parse([keep])[0]["raw"]["postal_code"], "38002")


class SourceRegistrationTests(unittest.TestCase):
    def test_adapters_are_active(self) -> None:
        active = {fn.__name__ for fn in ing.ACTIVE_SOURCE_ADAPTERS}
        self.assertIn("source_canarias_tecarga", active)
        self.assertIn("source_mapareve_public", active)

    def test_canarias_station_page_classifies_exact_and_map_eligible(self) -> None:
        source = station_truth.classify_source_resource({
            "name": ing.CANARIAS_TE_RECARGA_SOURCE,
            "url": "https://canariasterecarga.com/es/p61002",
        })
        self.assertEqual(source["kind"], "station_page")
        self.assertEqual(source["quality"], "exact")
        map_page = station_truth.classify_source_resource({
            "name": ing.CANARIAS_TE_RECARGA_SOURCE,
            "url": ing.CANARIAS_TE_RECARGA_MAP_PAGE_URL,
        })
        self.assertEqual(map_page["kind"], "live_map")

    def test_parsed_rows_carry_no_live_claim(self) -> None:
        records = ing.parse_canarias_tecarga_geojson(
            ct_payload([ct_feature("4", "Intercambiador Santa Cruz de Tenerife", -16.25238, 28.459126, "icono_free.png", BADGE_GRATIS)]),
            minimum_features=1,
        )
        row = records[0]
        self.assertFalse(row["source_integration"]["live_status_integrated"])
        self.assertEqual(row["available"], 0)
        self.assertNotEqual(row["status"], "AVAILABLE")
        self.assertEqual((row.get("access") or {}).get("level"), "unknown")
        # Free claim with directory-grade evidence: confirmed_free, but a directory lead.
        self.assertEqual(row["price_claim_type"], "confirmed_free")
        self.assertEqual(row["reliability"], "directory-page")


if __name__ == "__main__":
    unittest.main()
