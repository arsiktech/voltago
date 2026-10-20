#!/usr/bin/env python3
"""Contracts for the licensed official-source growth tranches in Plan 011."""
from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ingestion  # noqa: E402
import server  # noqa: E402


class BarcelonaEndollaTests(unittest.TestCase):
    TARIFF_HTML = """
        <section>EcoVehicles Amb subscripció mensual</section>
        <section>Aparcament BSM Tipus de recàrrega: Normal 0.22 €/kWh Nocturn</section>
    """
    FAQ_HTML = """
        <p>El precio de las tarifas nocturnas se aplican de 00 h a las 7:59 de la mañana.</p>
    """

    @staticmethod
    def artifact(url: str, cache_name: str, fetched_at: str) -> ingestion.FetchArtifact:
        return ingestion.FetchArtifact(
            body=b"",
            url=url,
            cache_name=cache_name,
            fetched_at=fetched_at,
            age_seconds=0,
            hit=False,
            fallback=False,
            stale=False,
        )

    def test_offstreet_normal_tariff_keeps_every_condition_but_never_persists_live_status(self) -> None:
        locations = {
            "gelfs_version": "0.96",
            "locations": [{
                "id": "3713",
                "network_brand_name": "Endolla Barcelona",
                "network_name": "Barcelona de Serveis Municipals S.A.",
                "coordinates": {"latitude": 41.419664, "longitude": 2.118465},
                "address": {
                    "address_string": "Camí de Vallvidrera al Tibidabo 32",
                    "locality": "Barcelona",
                    "postal_code": "08001",
                },
                "host": {"name": "B:SM Barcelona de Serveis Municipals"},
                "stations": [{
                    "id": "11258",
                    "label": "CCL1_TIBIDABO1",
                    "notes": "Aparcament Ap. - Tibidabo",
                    "ports": [{
                        "id": "1",
                        "connector_type": "MENNEKES",
                        "power_kw": 22.0,
                        "port_status": [{"status": "UNKNOWN"}],
                        "last_updated": "2026-07-15T09:00:00+02:00",
                        "authentications": [{"authentication_id": "A1", "payment_required": True}],
                    }],
                }],
                "onstreet_location": False,
                "last_updated": "2026-07-15T08:00:00+02:00",
            }, {
                "id": "street",
                "network_brand_name": "Endolla Barcelona",
                "coordinates": {"latitude": 41.40, "longitude": 2.18},
                "address": {"address_string": "Carrer Test", "locality": "Barcelona"},
                "host": {"name": "B:SM Barcelona de Serveis Municipals"},
                "stations": [{"id": "street-station", "ports": [{
                    "id": "1", "connector_type": "MENNEKES", "power_kw": 22.0,
                }]}],
                "onstreet_location": True,
            }],
        }
        statuses = {
            "locations": [{
                "id": "3713",
                "stations": [{"id": "11258", "ports": [{
                    "id": "1",
                    "port_status": [{"status": "AVAILABLE"}],
                    "last_updated": "2026-07-15T10:04:00+02:00",
                }]}],
            }],
        }

        rows = ingestion.parse_barcelona_endolla(
            locations,
            statuses,
            self.TARIFF_HTML,
            self.FAQ_HTML,
            observed_at=datetime(2026, 7, 15, 8, 5, tzinfo=timezone.utc),
            minimum_locations=1,
            location_artifact=self.artifact(
                ingestion.BARCELONA_ENDOLLA_LOCATIONS_URL,
                "locations.json",
                "2026-07-15T08:01:00Z",
            ),
            status_artifact=self.artifact(
                ingestion.BARCELONA_ENDOLLA_STATUS_URL,
                "status.json",
                "2026-07-15T08:05:00Z",
            ),
            tariff_artifact=self.artifact(
                ingestion.BARCELONA_ENDOLLA_TARIFF_URL,
                "tariff.html",
                "2026-07-15T07:59:00Z",
            ),
            faq_artifact=self.artifact(
                ingestion.BARCELONA_ENDOLLA_FAQ_URL,
                "faq.html",
                "2026-07-15T08:00:00Z",
            ),
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["price_min"], 0.22)
        self.assertEqual(row["status"], "VERIFY")
        self.assertEqual(row["connectors"][0]["status"], "UNKNOWN")
        self.assertEqual(row["connectors"][0]["raw"]["observed_status"], "AVAILABLE")
        self.assertTrue(row["connectors"][0]["raw"]["observed_status_fresh"])
        self.assertEqual(row["connectors"][0]["label"], "Type 2")
        self.assertEqual(row["pricing"][0]["valid_from"], "00:00")
        self.assertEqual(row["pricing"][0]["valid_until"], "08:00")
        self.assertIn("subscription", row["pricing"][0]["human"].lower())
        self.assertIn("b:sm", row["charge_claim"]["summary"].lower())
        self.assertIn("normal", row["charge_claim"]["summary"].lower())
        self.assertEqual(row["access"]["level"], "restricted")
        self.assertNotEqual(row["charge_claim"]["url"], row["parking_claim"]["url"])
        self.assertEqual(row["parking_claim"]["kind"], "paid_or_metered")
        self.assertTrue(row["parking"]["paid"])
        self.assertIn("separate b:sm", row["parking_claim"]["cost_hint"].lower())
        self.assertTrue(all(source.get("license") == "CC BY 4.0" for source in row["sources"][:2]))
        self.assertTrue(all(source.get("license_url") == "https://creativecommons.org/licenses/by/4.0/" for source in row["sources"][:2]))
        self.assertTrue(all(source.get("attribution") == "Ajuntament de Barcelona / Endolla" for source in row["sources"][:2]))
        self.assertEqual(row["source_fetched_at"], "2026-07-15T07:59:00Z")
        self.assertEqual(row["sources"][2]["source_fetched_at"], "2026-07-15T07:59:00Z")
        self.assertEqual(row["sources"][3]["source_fetched_at"], "2026-07-15T08:00:00Z")

    def test_stale_port_status_never_becomes_available(self) -> None:
        payload = {
            "locations": [{
                "id": "stale",
                "network_brand_name": "Endolla Barcelona",
                "coordinates": {"latitude": 41.41, "longitude": 2.17},
                "address": {"address_string": "Aparcament Test", "locality": "Barcelona"},
                "host": {"name": "B:SM Barcelona de Serveis Municipals"},
                "stations": [{"id": "s1", "notes": "Aparcament Ap. BSM", "ports": [{
                    "id": "p1", "connector_type": "WALL_OUTLET", "power_kw": 7.2,
                }]}],
                "onstreet_location": False,
            }],
        }
        statuses = {"locations": [{"id": "stale", "stations": [{"id": "s1", "ports": [{
            "id": "p1", "port_status": [{"status": "AVAILABLE"}],
            "last_updated": "2026-07-14T08:00:00+02:00",
        }]}]}]}

        row = ingestion.parse_barcelona_endolla(
            payload,
            statuses,
            self.TARIFF_HTML,
            self.FAQ_HTML,
            observed_at=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc).astimezone(),
            minimum_locations=1,
        )[0]

        self.assertEqual(row["status"], "VERIFY")
        self.assertEqual(row["connectors"][0]["status"], "UNKNOWN")
        self.assertEqual(row["connectors"][0]["raw"]["observed_status"], "AVAILABLE")
        self.assertFalse(row["connectors"][0]["raw"]["observed_status_fresh"])

    def test_motorcycle_only_ports_are_removed_and_car_inventory_is_not_truncated(self) -> None:
        car_ports = [
            {"id": f"car-{index}", "connector_type": "WALL_OUTLET", "power_kw": 3.6}
            for index in range(20)
        ]
        motorcycle_ports = [
            {
                "id": f"moto-{index}",
                "connector_type": "WALL_OUTLET",
                "power_kw": 3.6,
                "notes": "MOTORCYCLE_ONLY",
            }
            for index in range(3)
        ]
        payload = {
            "locations": [{
                "id": "mixed",
                "coordinates": {"latitude": 41.41, "longitude": 2.17},
                "address": {"address_string": "Aparcament Mixed", "locality": "Barcelona"},
                "host": {"name": "B:SM Barcelona de Serveis Municipals"},
                "stations": [{"id": "mixed-station", "ports": car_ports + motorcycle_ports}],
                "onstreet_location": False,
            }, {
                "id": "moto-only",
                "coordinates": {"latitude": 41.42, "longitude": 2.18},
                "address": {"address_string": "Aparcament Moto", "locality": "Barcelona"},
                "host": {"name": "B:SM Barcelona de Serveis Municipals"},
                "stations": [{"id": "moto-station", "ports": motorcycle_ports}],
                "onstreet_location": False,
            }],
        }

        rows = ingestion.parse_barcelona_endolla(
            payload,
            {"locations": []},
            self.TARIFF_HTML,
            self.FAQ_HTML,
            minimum_locations=1,
        )

        self.assertEqual(len(rows), 1, "an all-motorcycle location is not usable by the car app")
        self.assertEqual(len(rows[0]["connectors"]), 20)
        self.assertEqual(rows[0]["connector_count"], 20)
        self.assertEqual(rows[0]["total_evse"], 20)
        deduped = ingestion.dedupe(rows)
        self.assertEqual(len(deduped[0]["connectors"]), 20)
        self.assertEqual(deduped[0]["hardware_claim"]["port_count"], 20)

    def test_authoritative_endolla_inventory_cannot_gain_tesla_compatibility_from_ocm(self) -> None:
        payload = {
            "locations": [{
                "id": "endolla-wall",
                "coordinates": {"latitude": 41.41, "longitude": 2.17},
                "address": {"address_string": "Aparcament Same Site", "locality": "Barcelona"},
                "host": {"name": "B:SM Barcelona de Serveis Municipals"},
                "stations": [{"id": "s1", "notes": "Same Site", "ports": [{
                    "id": "p1", "connector_type": "WALL_OUTLET", "power_kw": 3.6,
                }]}],
                "onstreet_location": False,
            }],
        }
        endolla = ingestion.parse_barcelona_endolla(
            payload,
            {"locations": []},
            self.TARIFF_HTML,
            self.FAQ_HTML,
            minimum_locations=1,
        )[0]
        ocm_rows = []
        ingestion.add_record(
            ocm_rows,
            source="OpenChargeMap public snapshot",
            source_url="https://map.openchargemap.io/?mode=details&id=123",
            name=endolla["name"],
            address=endolla["address"],
            city="Barcelona",
            lat=endolla["lat"],
            lon=endolla["lon"],
            price_min=0.22,
            reliability="community-cost",
            connector_labels=["Type 2"],
            connectors=[ingestion.connector_ref("Type 2", power_kw=22.0)],
            raw={"DateLastVerified": "2026-07-01T00:00:00Z"},
        )

        merged, count = ingestion.merge_cross_source_duplicates([endolla, ocm_rows[0]])

        self.assertEqual(count, 1)
        self.assertEqual(merged[0]["connector_labels"], ["Wall outlet (connector unspecified)"])
        self.assertFalse(merged[0]["tesla_ok"])
        self.assertEqual(merged[0]["hardware_claim"]["source"], "Barcelona Endolla location/details open data")

    def test_tariff_or_night_window_wording_change_fails_closed(self) -> None:
        payload = {
            "locations": [{
                "id": "one",
                "coordinates": {"latitude": 41.41, "longitude": 2.17},
                "address": {"address_string": "Aparcament Test", "locality": "Barcelona"},
                "host": {"name": "B:SM Barcelona de Serveis Municipals"},
                "stations": [{"id": "s1", "ports": [{
                    "id": "p1", "connector_type": "MENNEKES", "power_kw": 22.0,
                }]}],
                "onstreet_location": False,
            }],
        }

        with self.assertRaises(RuntimeError):
            ingestion.parse_barcelona_endolla(
                payload,
                {"locations": []},
                "<p>Tariff details are temporarily unavailable.</p>",
                self.FAQ_HTML,
                minimum_locations=1,
            )
        with self.assertRaises(RuntimeError):
            ingestion.parse_barcelona_endolla(
                payload,
                {"locations": []},
                self.TARIFF_HTML,
                "<p>Night hours are changing.</p>",
                minimum_locations=1,
            )


class MadridOfficialTests(unittest.TestCase):
    def test_utm_conversion_and_parser_produce_corroboration_only_evidence(self) -> None:
        csv_text = (
            "COD_DIS;DISTRITO;COD_BAR;BARRIO;UBICACIÓN;EMPLAZAMIE;INFO_ADICI;Nº_EQUIPO;CARACTERÍ;OPERADOR;POINT_X;POINT_Y\n"
            "02;Arganzuela;1;Imperial;Calle Chulapos s/n;Vía pública;-;1;50 kW 2 conectores CC (CCS/Chademo) y 1 conector AC modo 2 Mennekes;IBERDROLA;438893.73;4473226.26\n"
        )

        artifact = ingestion.FetchArtifact(
            body=csv_text.encode("utf-8"),
            url=ingestion.MADRID_CHARGERS_CSV_URL,
            cache_name="madrid.csv",
            fetched_at="2026-07-15T12:34:56Z",
            age_seconds=0,
            hit=False,
            fallback=False,
            stale=False,
        )
        rows = ingestion.parse_madrid_official_csv(
            csv_text,
            minimum_records=1,
            source_artifact=artifact,
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["lat"], 40.4059, places=2)
        self.assertAlmostEqual(row["lon"], -3.7207, places=2)
        self.assertIsNone(row["price_min"])
        self.assertFalse(row["is_cheap"])
        self.assertTrue(row["raw"]["corroboration_only"])
        self.assertIn("CC BY 4.0", row["sources"][0]["license"])
        self.assertEqual(row["sources"][0]["attribution"], "Ayuntamiento de Madrid")
        self.assertIn("ETRS89", row["sources"][0]["transformation"])
        self.assertEqual(row["sources"][0]["source_fetched_at"], "2026-07-15T12:34:56Z")
        self.assertEqual({c["label"] for c in row["connectors"]}, {"CCS2", "CHAdeMO", "Type 2"})

    def test_corroboration_only_row_does_not_merge_a_distinct_cheap_site_30m_away(self) -> None:
        official = []
        ingestion.add_record(
            official,
            source="Madrid public charger open data",
            source_url=ingestion.MADRID_CHARGERS_CSV_URL,
            name="Madrid public charger — Calle Uno 10",
            address="Calle Uno 10",
            city="Madrid",
            lat=40.41000,
            lon=-3.70000,
            price_min=None,
            reliability="official-charger-location-price-unknown",
            raw={"corroboration_only": True},
        )
        cheap = []
        ingestion.add_record(
            cheap,
            source="OpenChargeMap public snapshot",
            source_url="https://map.openchargemap.io/?mode=details&id=1",
            name="Hotel Distinto",
            address="Calle Dos 22",
            city="Madrid",
            lat=40.41027,
            lon=-3.70000,
            price_min=0.0,
            reliability="community-free",
        )

        merged, merge_count = ingestion.merge_cross_source_duplicates(official + cheap)

        self.assertEqual(merge_count, 0)
        self.assertEqual(len(merged), 2)

    def test_ancient_ocm_price_cannot_admit_or_overwrite_current_official_madrid_hardware(self) -> None:
        official = ingestion.parse_madrid_official_csv(
            (
                "COD_DIS;DISTRITO;COD_BAR;BARRIO;UBICACIÓN;EMPLAZAMIE;INFO_ADICI;Nº_EQUIPO;CARACTERÍ;OPERADOR;POINT_X;POINT_Y\n"
                "02;Salamanca;1;Goya;Calle Goya 36;Vía pública;-;1;50 kW CCS CHAdeMO Mennekes;IBERDROLA;440928.0;4475042.0\n"
            ),
            minimum_records=1,
        )[0]
        community = []
        ingestion.add_record(
            community,
            source="OpenChargeMap public snapshot",
            source_url="https://map.openchargemap.io/?mode=details&id=85404",
            name="Calle Goya 36",
            address="Calle Goya 36",
            city="Madrid",
            lat=official["lat"],
            lon=official["lon"],
            price_min=0.0,
            reliability="community-free",
            connector_labels=["Schuko"],
            connectors=[ingestion.connector_ref("Schuko", power_kw=3.0)],
            raw={"DateLastVerified": "2020-01-01T00:00:00Z"},
        )

        exact_coordinate = ingestion.dedupe([official, community[0]])

        self.assertEqual(len(exact_coordinate), 1)
        self.assertIsNone(exact_coordinate[0]["price_min"])
        self.assertEqual(set(exact_coordinate[0]["connector_labels"]), {"CCS2", "CHAdeMO", "Type 2"})

        merged, count = ingestion.merge_cross_source_duplicates([official, community[0]])

        self.assertEqual(count, 1)
        row = merged[0]
        self.assertIsNone(row["price_min"])
        self.assertEqual(set(row["connector_labels"]), {"CCS2", "CHAdeMO", "Type 2"})
        self.assertEqual({c["source_url"] for c in row["connectors"]}, {ingestion.MADRID_CHARGERS_CSV_URL})
        self.assertEqual(row["hardware_claim"]["source"], "Madrid public charger open data")
        self.assertEqual(row["hardware_claim"]["url"], ingestion.MADRID_CHARGERS_CSV_URL)

    def test_canonical_madrid_drops_ancient_community_price_matches(self) -> None:
        payload = json.loads((ROOT / "data" / "free_intelligence.json").read_text(encoding="utf-8"))
        rows = [
            row for row in payload["records"]
            if row.get("source") == "Madrid public charger open data" and row.get("price_min") is not None
        ]

        self.assertEqual(rows, [])
        priced_addresses = {row.get("address") for row in rows}
        self.assertTrue({
            "Calle Goya 36",
            "Calle Génova 24",
            "Calle Bueso Pineda 29",
            "Plaza del Conde de Casal 6",
        }.isdisjoint(priced_addresses))


class ZaragozaOfficialTests(unittest.TestCase):
    def test_page_parser_keeps_official_hardware_but_never_infers_price(self) -> None:
        html = """
        <div class="geo" id="geo">
          <strong property="title" data-term="title" content="Puntos de recarga VE Camino las torres 4">Puntos</strong>
          <li property="calle" data-term="calle" content="TORRES, CAMINO DE LAS, 4"></li>
          <li>Puntos de carga: <strong>2</strong></li>
          <li data-term="gestora"><strong>Gestora:</strong><br/> Endesa x Way</li>
          <meta property="longitude" content="-0.87275864"/>
          <meta property="latitude" content="41.64714107"/>
          <meta property="id" content="MU1_puntos_recarga.46"/>
          <li data-term="tecnologia"><span>Quick</span><span>2 del tipo Type 2 (Mennekes)</span><span>28 kW</span></li>
          <li><strong>Pago:</strong> con aplicación Endesa Mobility</li>
        </div>
        """

        rows = ingestion.parse_zaragoza_official_page(html, minimum_records=1)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsNone(row["price_min"])
        self.assertTrue(row["raw"]["corroboration_only"])
        self.assertEqual(row["connector_labels"], ["Type 2"])
        self.assertEqual(row["max_power_kw"], 28.0)
        self.assertEqual(row["raw"]["operator"], "Endesa x Way")
        self.assertIn("reuse", row["sources"][0]["license"].lower())
        self.assertEqual(row["sources"][0]["attribution"], "Ayuntamiento de Zaragoza")
        self.assertEqual(row["sources"][0]["license_url"], ingestion.ZARAGOZA_REUSE_URL)

    def test_population_shrink_fails_closed(self) -> None:
        with self.assertRaises(RuntimeError):
            ingestion.parse_zaragoza_official_page("<html></html>", minimum_records=37)


class NarrowOfficialPolicyTests(unittest.TestCase):
    def test_red_movem_tariff_applies_only_to_unambiguous_at_most_22kw_equipment(self) -> None:
        tags = {
            "name": "Red Movem Talarrubias",
            "operator": "Diputación de Badajoz",
            "operator:website": "https://movembadajoz.es/",
        }
        self.assertEqual(ingestion.red_movem_price(tags, 22.0), 0.25)
        self.assertIsNone(ingestion.red_movem_price(tags, 25.0))
        self.assertIsNone(ingestion.red_movem_price(tags, 50.0))
        self.assertIsNone(ingestion.red_movem_price({"name": "Badajoz charger"}, 22.0))
        self.assertEqual(ingestion.red_movem_price({"operator:website": "https://movembadajoz.es/"}, 22.0), 0.25)
        self.assertEqual(
            ingestion.osm_station_name({"operator:website": "https://movembadajoz.es/"}),
            "Red MOVEM charger",
        )
        self.assertEqual(ingestion.osm_station_name({}), "OSM free charging station")

    def test_red_movem_manual_tariff_review_expires_fail_closed(self) -> None:
        reviewed = datetime.fromisoformat(ingestion.RED_MOVEM_TARIFF_CHECKED_AT.replace("Z", "+00:00"))
        ingestion.assert_reference_review_current(
            ingestion.RED_MOVEM_TARIFF_CHECKED_AT,
            ingestion.RED_MOVEM_REVIEW_MAX_AGE_DAYS,
            now=reviewed + timedelta(hours=12),
            label="Red MOVEM tariff",
        )
        with self.assertRaises(RuntimeError):
            ingestion.assert_reference_review_current(
                ingestion.RED_MOVEM_TARIFF_CHECKED_AT,
                ingestion.RED_MOVEM_REVIEW_MAX_AGE_DAYS,
                now=reviewed + timedelta(days=ingestion.RED_MOVEM_REVIEW_MAX_AGE_DAYS + 1),
                label="Red MOVEM tariff",
            )

    @staticmethod
    def _tariff_page(body: str, fetched_at: str = "2026-10-20T09:00:00Z"):
        def fetcher():
            return ingestion.FetchArtifact(
                body=body.encode("utf-8"), url=ingestion.RED_MOVEM_TARIFF_PAGE_URL,
                cache_name="red_movem_tariff_page.html", fetched_at=fetched_at,
                age_seconds=0, hit=False, fallback=False, stale=False,
            )
        return fetcher

    def test_red_movem_review_renews_from_the_operator_tariff_page(self) -> None:
        page = "<h3>Hasta 22 kW</h3>\n<p>0,25&euro; / kWh</p><h3>Hasta 50 kW</h3><p>0,30€ / kWh</p>"
        review = ingestion.red_movem_tariff_review(
            now=datetime(2027, 6, 1, tzinfo=timezone.utc),
            page_fetcher=self._tariff_page(page, fetched_at="2027-05-31T08:00:00Z"),
        )
        self.assertEqual(review["status"], "page-verified")
        self.assertEqual(review["checked_at"], "2027-05-31T08:00:00Z")
        self.assertEqual(review["url"], ingestion.RED_MOVEM_TARIFF_PAGE_URL)

    def test_red_movem_review_rejects_a_changed_or_old_tariff_page(self) -> None:
        changed = self._tariff_page("<p>Hasta 22 kW</p><p>0,35€ / kWh</p>")
        old_cache = self._tariff_page("<p>Hasta 22 kW 0,25€ / kWh</p>", fetched_at="2026-01-01T00:00:00Z")
        for fetcher in (changed, old_cache):
            with self.subTest(fetcher=fetcher):
                manual = ingestion.red_movem_tariff_review(now=datetime(2026, 10, 21, tzinfo=timezone.utc), page_fetcher=fetcher)
                self.assertEqual(manual["status"], "manual")
                stale = ingestion.red_movem_tariff_review(now=datetime(2027, 6, 1, tzinfo=timezone.utc), page_fetcher=fetcher)
                self.assertEqual(stale["status"], "stale")

    def test_red_movem_review_survives_an_unreachable_tariff_page(self) -> None:
        def unreachable():
            raise OSError("offline")
        review = ingestion.red_movem_tariff_review(now=datetime(2027, 6, 1, tzinfo=timezone.utc), page_fetcher=unreachable)
        self.assertEqual(review["status"], "stale")
        self.assertIn("unavailable", review["detail"])

    def test_stale_red_movem_review_withholds_the_tariff_but_keeps_osm_rows(self) -> None:
        elements = [
            {"type": "node", "id": 9100000001, "lat": 39.0475, "lon": -5.2389, "tags": {
                "amenity": "charging_station", "name": "Red Movem Talarrubias", "operator": "Diputación de Badajoz",
                "operator:website": "https://movembadajoz.es/", "socket:type2": "2", "socket:type2:output": "22 kW",
                "fee": "no",
            }},
            {"type": "node", "id": 9100000002, "lat": 39.5726, "lon": -1.2109, "tags": {
                "amenity": "charging_station", "name": "Plaza Mayor", "socket:type2": "1", "fee": "no",
            }},
        ]
        body = json.dumps({"version": 0.6, "elements": elements}).encode("utf-8")
        originals = (ingestion.cached_overpass, ingestion.OVERPASS_TILE_PAUSE_SECONDS, ingestion.red_movem_tariff_review)
        self.addCleanup(lambda: (
            setattr(ingestion, "cached_overpass", originals[0]),
            setattr(ingestion, "OVERPASS_TILE_PAUSE_SECONDS", originals[1]),
            setattr(ingestion, "red_movem_tariff_review", originals[2]),
        ))
        ingestion.cached_overpass = lambda query, cache_name, ttl_hours=12: body
        ingestion.OVERPASS_TILE_PAUSE_SECONDS = 0
        for status, expected_price in (("stale", None), ("page-verified", 0.25)):
            with self.subTest(status=status):
                ingestion.red_movem_tariff_review = lambda status=status: {
                    "status": status, "checked_at": "2026-10-20T09:00:00Z",
                    "url": ingestion.RED_MOVEM_TARIFF_PAGE_URL, "detail": "fixture",
                }
                records: list = []
                ingestion.source_osm(records)  # a stale tariff must not fail the source
                by_id = {row["raw"]["osm_id"]: row for row in records}
                self.assertEqual(set(by_id), {9100000001, 9100000002})
                movem = by_id[9100000001]
                self.assertEqual(movem["price_min"], expected_price)
                self.assertFalse(movem["is_free"])  # the network charges; fee=no never wins
                self.assertTrue(by_id[9100000002]["is_free"])
                if status == "stale":
                    self.assertNotEqual((movem.get("charge_claim") or {}).get("source"), "Red MOVEM official public-price regulation")
                else:
                    self.assertEqual(movem["charge_claim"]["checked_at"], "2026-10-20T09:00:00Z")

    def test_canonical_red_movem_rows_attribute_price_to_the_official_regulation(self) -> None:
        payload = json.loads((ROOT / "data" / "free_intelligence.json").read_text(encoding="utf-8"))
        rows = [
            row for row in payload["records"]
            if (row.get("charge_claim") or {}).get("source") == "Red MOVEM official public-price regulation"
        ]

        self.assertEqual(len(rows), 4)
        for row in rows:
            with self.subTest(station=row["id"]):
                normalized = server._normalize_lead(deepcopy(row), row["source"])
                windows = normalized["price_window_status"]["matching_windows"]
                self.assertTrue(windows)
                self.assertEqual(windows[0]["source"], "Red MOVEM official public-price regulation")
                self.assertEqual(windows[0]["source_url"], ingestion.RED_MOVEM_TARIFF_URL)

    def test_moraleja_green_is_static_not_tesla_safe_with_conditional_access_and_separate_parking(self) -> None:
        rows = []
        acciona = ingestion.FetchArtifact(
            body=b"",
            url=ingestion.MORALEJA_FREE_URL,
            cache_name="acciona.html",
            fetched_at="2026-07-15T07:00:00Z",
            age_seconds=0,
            hit=False,
            fallback=False,
            stale=False,
        )
        venue = ingestion.FetchArtifact(
            body=b"",
            url=ingestion.MORALEJA_VENUE_URL,
            cache_name="venue.html",
            fetched_at="2026-07-15T08:00:00Z",
            age_seconds=0,
            hit=False,
            fallback=False,
            stale=False,
        )
        ingestion.parse_moraleja_green(
            rows,
            "<title>Green Smart Charge | ACCIONA</title><p>Proyecto piloto de innovación. La recarga es gratuita. Use la tarjeta RFID. Acuda al punto de información.</p>",
            "<p>Tres cargadores de la empresa Acciona con una nueva tecnología para la recarga inteligente. Parking Norte. Planta -2. 2 horas gratuitas de lunes a viernes. Parking gratis el fin de semana desde el viernes a las 16h.</p>",
            acciona_artifact=acciona,
            venue_artifact=venue,
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertFalse(row["is_free"])
        self.assertEqual(row["price_claim_type"], "access_limited_free")
        self.assertEqual(row["status"], "VERIFY")
        self.assertIsNone(row["tesla_ok"])
        self.assertIn("rfid", row["free_condition"].lower())
        self.assertIn("information desk", row["free_condition"].lower())
        self.assertNotEqual(row["parking"]["kind"], "free_parking")
        self.assertIn("first two hours", row["parking_claim"]["summary"].lower())
        self.assertIn("friday 16:00", row["parking_claim"]["summary"].lower())
        self.assertNotEqual(row["charge_claim"]["url"], row["parking_claim"]["url"])
        self.assertEqual(row["source_fetched_at"], "2026-07-15T07:00:00Z")
        self.assertEqual(row["access"]["level"], "restricted")
        self.assertTrue(row["access_claim"]["private_or_restricted"])
        self.assertIn("information desk", row["access_claim"]["summary"].lower())
        self.assertIsNone(row["connector_count"])
        self.assertIsNone(row["total_evse"])
        self.assertEqual(row["raw"]["venue_chargers"], 3)
        self.assertIsNone(row["hardware_claim"]["port_count"])
        self.assertEqual(row["hardware_claim"]["connector_types"], [])
        self.assertIn("not published", row["hardware_claim"]["summary"].lower())
        self.assertEqual(row["location_claim"]["method"], "official_venue_address_plus_osm_geometry")
        deduped = ingestion.dedupe(rows)[0]
        self.assertFalse(deduped["is_free"])
        self.assertEqual(deduped["access"]["level"], "restricted")
        self.assertIn("information desk", deduped["access"]["note"].lower())
        self.assertIn("information desk", deduped["access_claim"]["summary"].lower())
        self.assertEqual(deduped["location_claim"]["method"], "official_venue_address_plus_osm_geometry")
        self.assertEqual(
            deduped["location_claim"]["source"],
            "Moraleja Green current services page + OSM venue geometry",
        )
        normalized = server._normalize_lead(deepcopy(row), row["source"])
        # No connector types published: unknown, never "Not Tesla-safe".
        self.assertIsNone(normalized["tesla_ok"])
        self.assertNotEqual(normalized["decision"]["label"], "Not Tesla-safe")
        self.assertEqual(normalized["decision"]["label"], "Verify first")

    def test_moraleja_green_claim_fails_closed_when_first_party_wording_disappears(self) -> None:
        acciona = ingestion.FetchArtifact(
            body=b"",
            url=ingestion.MORALEJA_FREE_URL,
            cache_name="acciona.html",
            fetched_at="2026-07-15T07:00:00Z",
            age_seconds=0,
            hit=False,
            fallback=False,
            stale=False,
        )
        venue = ingestion.FetchArtifact(
            body=b"",
            url=ingestion.MORALEJA_VENUE_URL,
            cache_name="venue.html",
            fetched_at="2026-07-15T08:00:00Z",
            age_seconds=0,
            hit=False,
            fallback=False,
            stale=False,
        )

        with self.assertRaisesRegex(RuntimeError, "no longer proves free RFID"):
            ingestion.parse_moraleja_green(
                [],
                "<title>Green Smart Charge | ACCIONA</title><p>Proyecto piloto de innovación. Consulte las condiciones.</p>",
                "<p>Tres cargadores de la empresa Acciona con una nueva tecnología para la recarga inteligente. Parking Norte, planta -2.</p>",
                acciona_artifact=acciona,
                venue_artifact=venue,
            )

    def test_moraleja_green_parking_claim_fails_closed_when_free_window_wording_disappears(self) -> None:
        acciona = ingestion.FetchArtifact(
            body=b"",
            url=ingestion.MORALEJA_FREE_URL,
            cache_name="acciona.html",
            fetched_at="2026-07-15T07:00:00Z",
            age_seconds=0,
            hit=False,
            fallback=False,
            stale=False,
        )
        venue = ingestion.FetchArtifact(
            body=b"",
            url=ingestion.MORALEJA_VENUE_URL,
            cache_name="venue.html",
            fetched_at="2026-07-15T08:00:00Z",
            age_seconds=0,
            hit=False,
            fallback=False,
            stale=False,
        )

        with self.assertRaisesRegex(RuntimeError, "no longer proves the current limited free-parking windows"):
            ingestion.parse_moraleja_green(
                [],
                "<title>Green Smart Charge | ACCIONA</title><p>Proyecto piloto de innovación. La recarga es gratuita. Use la tarjeta RFID. Acuda al punto de información.</p>",
                "<p>Tres cargadores de la empresa Acciona con una nueva tecnología para la recarga inteligente. Parking Norte. Planta -2. Consulte las condiciones de parking.</p>",
                acciona_artifact=acciona,
                venue_artifact=venue,
            )

    def test_moraleja_green_identity_contract_fails_closed_on_generic_replacement_pages(self) -> None:
        artifact = ingestion.FetchArtifact(
            body=b"",
            url=ingestion.MORALEJA_FREE_URL,
            cache_name="identity.html",
            fetched_at="2026-07-15T07:00:00Z",
            age_seconds=0,
            hit=False,
            fallback=False,
            stale=False,
        )

        with self.assertRaisesRegex(RuntimeError, "identity contract"):
            ingestion.parse_moraleja_green(
                [],
                "<p>La recarga es gratuita. Use la tarjeta RFID. Acuda al punto de información.</p>",
                "<p>Tres cargadores. Parking Norte. Planta -2. 2 horas gratuitas. Parking gratis el fin de semana desde el viernes a las 16h.</p>",
                acciona_artifact=artifact,
                venue_artifact=artifact,
            )

    def test_moraleja_green_stale_fallback_fails_closed(self) -> None:
        stale_age = ingestion.MORALEJA_EVIDENCE_MAX_AGE_SECONDS + 1
        acciona = ingestion.FetchArtifact(
            body=b"",
            url=ingestion.MORALEJA_FREE_URL,
            cache_name="acciona.html",
            fetched_at="2026-07-01T00:00:00Z",
            age_seconds=stale_age,
            hit=True,
            fallback=True,
            stale=True,
        )
        venue = ingestion.FetchArtifact(
            body=b"",
            url=ingestion.MORALEJA_VENUE_URL,
            cache_name="venue.html",
            fetched_at="2026-07-01T00:00:00Z",
            age_seconds=stale_age,
            hit=True,
            fallback=True,
            stale=True,
        )

        with self.assertRaisesRegex(RuntimeError, "too old"):
            ingestion.parse_moraleja_green(
                [],
                "<p>La recarga es gratuita. Use la tarjeta RFID. Acuda al punto de información.</p>",
                "<p>Tres cargadores. Parking Norte. Planta -2.</p>",
                acciona_artifact=acciona,
                venue_artifact=venue,
            )

    def test_all_unblocked_official_adapters_are_active(self) -> None:
        active = {fn.__name__ for fn in ingestion.ACTIVE_SOURCE_ADAPTERS}
        self.assertTrue({
            "source_barcelona_endolla",
            "source_madrid_official",
            "source_zaragoza_official",
            "source_moraleja_green",
        }.issubset(active))


if __name__ == "__main__":
    unittest.main()
