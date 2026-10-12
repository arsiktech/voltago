#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import ingest_sources  # noqa: E402
import server  # noqa: E402


class ParkingClassificationTests(unittest.TestCase):
    def test_paid_parking_text_is_not_treated_as_free(self) -> None:
        cases = [
            ("15€", "paid_or_metered"),
            ("De pago", "paid_or_metered"),
            ("Precio por hora 2,30 €", "paid_or_metered"),
            ("Hotel parking tariff applies", "paid_or_metered"),
        ]

        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(ingest_sources.parking_kind_from_price(text, "unknown"), expected)

    def test_limited_or_validated_free_parking_stays_conditional(self) -> None:
        cases = [
            "Gratis 90 min. Luego de ese tiempo 0,70€/min",
            "1 hora gratis con cualquier compra, resto tarifa parking",
            "Free parking después pagar 2€",
        ]

        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(ingest_sources.parking_kind_from_price(text, "unknown"), "time_limited_or_validated")

    def test_clear_free_and_unknown_parking_are_preserved(self) -> None:
        self.assertEqual(ingest_sources.parking_kind_from_price("Gratis", "unknown"), "free_parking")
        self.assertEqual(ingest_sources.parking_kind_from_price("0€", "unknown"), "free_parking")
        self.assertEqual(ingest_sources.parking_kind_from_price("0 €", "paid_or_metered"), "free_parking")
        self.assertEqual(ingest_sources.parking_kind_from_price("Gratuito", "paid_or_metered"), "free_parking")
        self.assertEqual(ingest_sources.parking_kind_from_price("Gratuito", "retail_parking"), "retail_parking")
        self.assertEqual(ingest_sources.parking_kind_from_price("Por ver", "street_or_municipal"), "street_or_municipal")
        self.assertEqual(ingest_sources.parking_kind_from_price("", "retail_parking"), "retail_parking")

    def test_public_context_classifies_spanish_address_and_facility_parking(self) -> None:
        cases = [
            (
                ("Eroski Berango", "Sabino Arana Kalea, 50", "Free", "Public"),
                "retail_parking",
            ),
            (
                ("Feníe - CSIC CAMPUS UAH PR8", "AV. DE LEÓN, 1 CAMPUS UAH, ALCALÁ DE HENARES", "", ""),
                "facility_or_customer_parking",
            ),
            (
                ("Ben Comun Espazo", "Praza Torre Lombarda", "Gratis", "Public"),
                "street_or_municipal",
            ),
            (
                ("Perpignan Airport", "", "0", "Public"),
                "paid_or_metered",
            ),
            (
                ("Feníe - CAMPING RUILOBA", "Bº La Iglesia, Ruiloba", "", ""),
                "facility_or_customer_parking",
            ),
            (
                ("Feníe - ESTACION DE RENFE", "Victor Pruneda, 1, Teruel", "", ""),
                "facility_or_customer_parking",
            ),
            (
                ("Feníe - COFRADÍA PESCADORES DE GANDIA", "RECINTO PORTUARIO, S/N", "", ""),
                "facility_or_customer_parking",
            ),
            (
                ("Feníe - Puerto Venecia 2", "Travesía Jardines Reales nº7, Zaragoza", "", ""),
                "retail_parking",
            ),
            (
                ("algepsers", "Vía Sucronense 28 46687", "", ""),
                "street_or_municipal",
            ),
            (
                ("Aguilar de Campoo", "Paseo del Soto, 9, Aguilar de Campoo", "", ""),
                "street_or_municipal",
            ),
            (
                ("Gernika Lorategia 25", "Gernika Lorategia, 25", "0.00€/kWh", "Public"),
                "street_or_municipal",
            ),
            (
                ("Feníe — EL BALCON DE POTES", "EPIFANIO SANCHEZ MATEO, 20, POTES, Cantabria", "", ""),
                "street_or_municipal",
            ),
            (
                ("Feníe — ARGENTE BALAGUER", "Mare María Rafols 3, ALGEMESI, Valencia", "", ""),
                "street_or_municipal",
            ),
            (
                ("Pdr Aj.Ademuz", "", "Free", "Public"),
                "street_or_municipal",
            ),
            (
                ("EDRR mollerussa", "Llorenç Vilaró", "Free", "Public"),
                "street_or_municipal",
            ),
        ]

        for args, expected in cases:
            with self.subTest(args=args):
                kind, note = ingest_sources.parking_kind_from_context(*args)
                self.assertEqual(kind, expected)
                self.assertTrue(note)

    def test_openchargemap_public_town_only_parking_context_is_not_free_parking(self) -> None:
        kind, note = ingest_sources.openchargemap_parking_kind("vil", "", "Vilalba", "Gratis", "Public")

        self.assertEqual(kind, "street_or_municipal")
        self.assertIn("not free parking", note)
        self.assertIn("verify regulated signs", note)

    def test_openchargemap_membership_town_only_context_stays_unknown(self) -> None:
        kind, note = ingest_sources.openchargemap_parking_kind(
            "Edrr Torello",
            "",
            "Torelló",
            "gratis",
            "Public - Membership Required",
        )

        self.assertEqual(kind, "unknown")
        self.assertEqual(note, "")

    def test_parking_profile_exposes_cost_hint_and_low_free_likelihood(self) -> None:
        profile = ingest_sources.parking_profile(
            "unknown",
            "Check app and onsite signs.",
            {"parking_price": "15€"},
        )

        self.assertEqual(profile["kind"], "paid_or_metered")
        self.assertTrue(profile["paid"])
        self.assertEqual(profile["cost_hint"], "15€")
        self.assertEqual(profile["free_likelihood"], "low")
        self.assertIn("Parking source says: 15€", profile["note"])

    def test_parking_profile_preserves_source_spaces_and_ownership(self) -> None:
        profile = ingest_sources.parking_profile(
            "unknown",
            "Feníe public network point; verify signs.",
            {"aparcamientos": 2, "propiedad": 1},
        )

        self.assertEqual(profile["kind"], "street_or_municipal")
        self.assertFalse(profile["paid"])
        self.assertEqual(profile["spaces"], 2)
        self.assertEqual(profile["ownership"], "public")
        self.assertEqual(profile["ownership_label"], "Public point")
        self.assertIn("2 EV parking spaces", profile["note"])
        self.assertIn("street/municipal context, not free parking", profile["note"])
        self.assertIn("public", profile["note"])

    def test_private_ownership_evidence_restricts_access_without_guessing_parking_price(self) -> None:
        access = ingest_sources.access_from_evidence(
            "Feníe Energía public map: propiedad=2 (private), gratuito=1.",
            "official-free",
            "unknown",
        )

        self.assertEqual(access["level"], "restricted")

    def test_official_free_without_private_evidence_is_public_access(self) -> None:
        access = ingest_sources.access_from_evidence(
            "Feníe Energía public map: propiedad=1 (public), gratuito=1.",
            "official-free",
            "unknown",
        )

        self.assertEqual(access["level"], "public")

    def test_money_parser_handles_euro_and_cent_price_hints(self) -> None:
        self.assertEqual(ingest_sources.money_from_text("4 céntimos minuto"), 0.04)
        self.assertEqual(ingest_sources.money_from_text("0.07€/min"), 0.07)
        self.assertEqual(ingest_sources.money_from_text("Precio por hora 2,30 €"), 2.3)

    def test_time_limited_parking_forces_check_parking_decision(self) -> None:
        item = {
            "is_free": True,
            "is_cheap": True,
            "tesla_ok": True,
            "available": 0,
            "status": "VERIFY",
            "reliability": "official-free-policy",
            "parking": {
                "kind": "time_limited_or_validated",
                "note": "Free only with validation; paid after the first hour.",
            },
        }

        decision = server.decision_for(item, live=False)

        self.assertEqual(decision["label"], "Check parking")
        self.assertIn("paid or conditional", decision["reason"])

    def test_indoor_or_garage_name_downgrades_street_parking_to_unknown(self) -> None:
        item = {
            "name": "CC Arena - Parking interior 02",
            "is_free": True,
            "is_cheap": True,
            "tesla_ok": True,
            "available": 0,
            "status": "VERIFY",
            "source_url": "https://map.electromaps.com/en/p/853994",
            "parking": {
                "kind": "street_or_municipal",
                "note": "Electromaps directory data; parking_price is shown separately when published.",
            },
            "parking_claim": {
                "kind": "street_or_municipal",
                "summary": "Electromaps directory data; parking_price is shown separately when published.",
            },
            "sources": [{
                "name": "Electromaps web-map API",
                "url": "https://map.electromaps.com/en/p/853994",
                "evidence": "instructions=Free publicly accessible",
            }],
        }

        server.enrich_station(item, live=False)

        self.assertEqual(item["parking"]["kind"], "unknown")
        self.assertEqual(item["parking"]["label"], "Parking unknown")
        self.assertIn("indoor or garage parking", item["parking"]["note"])
        self.assertEqual(item["parking_claim"]["kind"], "unknown")
        self.assertIn("indoor or garage parking", item["parking_claim"]["summary"])

    def test_retail_name_downgrades_street_label_to_customer_parking(self) -> None:
        item = {
            "name": "CENTRO COMERCIAL BONAIRE GASOLINERA ALCAMPO",
            "is_free": True,
            "is_cheap": True,
            "tesla_ok": True,
            "available": 0,
            "status": "VERIFY",
            "source_url": "https://map.electromaps.com/en/p/869351",
            "parking": {"kind": "street_or_municipal"},
        }

        server.enrich_station(item, live=False)

        self.assertEqual(item["parking"]["kind"], "retail_parking")
        self.assertEqual(item["parking"]["label"], "Retail/customer parking")
        self.assertIn("retail or customer parking", item["parking"]["note"])

    def test_facility_name_downgrades_street_label_to_customer_parking(self) -> None:
        item = {
            "name": "CSIC CAMPUS UAH PR8",
            "address": "AV. DE LEÓN, 1 CAMPUS UAH, ALCALÁ DE HENARES",
            "is_free": True,
            "is_cheap": True,
            "tesla_ok": True,
            "available": 0,
            "status": "VERIFY",
            "source_url": "https://map.electromaps.com/en/p/869351",
            "parking": {"kind": "street_or_municipal"},
        }

        server.enrich_station(item, live=False)

        self.assertEqual(item["parking"]["kind"], "facility_or_customer_parking")
        self.assertEqual(item["parking"]["label"], "Facility/customer parking")
        self.assertIn("facility, visitor or customer parking", item["parking"]["note"])

    def test_dedupe_preserves_mixed_price_and_parking_claim_provenance(self) -> None:
        records = []
        ingest_sources.add_record(
            records,
            source="Chargemap public map API",
            source_url="https://chargemap.com/free-mercadona.html",
            name="Mercadona mixed claim",
            lat=39.47001,
            lon=-0.37001,
            price_min=0.0,
            evidence="Chargemap public map marks this charging pool as free.",
            reliability="directory-map-free",
            parking_kind="retail_parking",
        )
        ingest_sources.add_record(
            records,
            source="Electromaps web-map API",
            source_url="https://map.electromaps.com/en/p/999",
            name="Mercadona mixed claim",
            lat=39.47001,
            lon=-0.37001,
            price_min=0.0,
            evidence="Electromaps says free charging.",
            reliability="directory-map-free",
            parking_kind="unknown",
            raw={"parking_price": "Gratis 90 min. Luego 0,70€/min"},
        )

        merged = ingest_sources.dedupe(records)

        self.assertEqual(len(merged), 1)
        row = merged[0]
        self.assertEqual(row["parking"]["kind"], "time_limited_or_validated")
        self.assertEqual(row["charge_claim"]["url"], "https://chargemap.com/free-mercadona.html")
        self.assertEqual(row["parking_claim"]["url"], "https://map.electromaps.com/en/p/999")
        self.assertEqual(row["claim_conflict"]["level"], "mixed_sources")

    def test_single_connector_type_station_power_is_propagated_with_basis(self) -> None:
        connectors = [ingest_sources.connector_ref("Type 2", count=2)]

        filled = ingest_sources.apply_single_type_station_power(connectors, 12.8, "OSM voltage/amperage")

        self.assertEqual(filled[0]["power_kw"], 12.8)
        self.assertEqual(filled[0]["raw"]["power_basis"], "OSM voltage/amperage")

    def test_station_power_is_not_propagated_to_mixed_connector_types(self) -> None:
        connectors = [
            ingest_sources.connector_ref("CCS2"),
            ingest_sources.connector_ref("Type 2"),
        ]

        filled = ingest_sources.apply_single_type_station_power(connectors, 50, "OSM charge/description text")

        self.assertTrue(all(c["power_kw"] is None for c in filled))


if __name__ == "__main__":
    unittest.main()
