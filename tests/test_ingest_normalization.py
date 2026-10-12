#!/usr/bin/env python3
"""City-label normalization + price-claim honesty tests (ingest_sources.add_record).

Both cover real defects found in the 2026-07-13 database:
  * Electromaps' web-map API hands back a bare postcode where a town name belongs, and
    add_record only re-derived 'empty'/'Valencia province', so '46910' survived as a city
    on 41 records (and 'BARCELONA' vs 'Barcelona' split one city into two).
  * a charger whose *published* price (0.40 EUR/kWh) was admitted as a price_unknown_lead,
    contradicting its own price. Paid chargers are now admitted, labelled confirmed_paid.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ing  # noqa: E402

VALENCIA_LAT, VALENCIA_LON = 39.4699, -0.3763
MADRID_LAT, MADRID_LON = 40.4168, -3.7038


def add(records, **over):
    """Call add_record with a minimal free/valid baseline, overridden per test."""
    kwargs = dict(
        source="Test source",
        source_url="https://example.org/x",
        name="Test charger",
        lat=VALENCIA_LAT,
        lon=VALENCIA_LON,
        price_min=0.0,
    )
    kwargs.update(over)
    ing.add_record(records, **kwargs)
    return records


class TitlecasePlaceTests(unittest.TestCase):
    def test_allcaps_city_is_titlecased(self):
        self.assertEqual(ing.titlecase_place("BARCELONA"), "Barcelona")

    def test_particles_stay_lowercase(self):
        self.assertEqual(ing.titlecase_place("QUART DE POBLET"), "Quart de Poblet")

    def test_mixed_case_name_is_untouched(self):
        self.assertEqual(ing.titlecase_place("València"), "València")


class CityFromAddressTests(unittest.TestCase):
    def test_town_after_postcode(self):
        self.assertEqual(
            ing.city_from_address("C/ de Morella, 86, 46910 Sedaví, Valencia, Es"), "Sedaví"
        )

    def test_town_after_postcode_and_comma(self):
        self.assertEqual(
            ing.city_from_address("Plaça Poligono N 1, 41, 46723, Almoines"), "Almoines"
        )

    def test_address_ending_at_postcode_yields_nothing(self):
        self.assertEqual(ing.city_from_address("Av. de la Mestra Josefina Fernández, 3, 46600"), "")

    def test_address_without_postcode_yields_nothing(self):
        self.assertEqual(ing.city_from_address("Calle Mayor 1"), "")


class ValenciaMunicipalAddressTests(unittest.TestCase):
    def test_backslash_street_prefix_is_rendered_as_a_slash(self):
        self.assertEqual(
            ing.normalize_valencia_municipal_address(r"C\ General San Martin, 7"),
            "C/ General San Martin, 7",
        )

    def test_multiline_lowercase_prefix_is_normalized(self):
        self.assertEqual(
            ing.normalize_valencia_municipal_address("Mercado Rojas Clemente\nc\\ Simon Ortiz"),
            "Mercado Rojas Clemente\nC/ Simon Ortiz",
        )

    def test_existing_slash_is_preserved(self):
        self.assertEqual(
            ing.normalize_valencia_municipal_address("C/ SAN VICENTE MARTIR 57"),
            "C/ SAN VICENTE MARTIR 57",
        )


class IsPlaceholderCityTests(unittest.TestCase):
    def test_postcode_is_placeholder(self):
        self.assertTrue(ing.is_placeholder_city("46910"))

    def test_legacy_valencia_default_is_placeholder(self):
        self.assertTrue(ing.is_placeholder_city("Valencia province"))

    def test_blank_is_placeholder(self):
        self.assertTrue(ing.is_placeholder_city("   "))

    def test_real_town_is_not_placeholder(self):
        self.assertFalse(ing.is_placeholder_city("Sedaví"))

    def test_truncated_street_prefix_is_placeholder_only_with_matching_address(self):
        self.assertTrue(ing.is_placeholder_city("Via Laet", "Via Laetana 69"))
        self.assertFalse(ing.is_placeholder_city("Ronda", "Ronda de Atocha 12"))


class AddRecordCityTests(unittest.TestCase):
    def test_postcode_city_is_replaced_by_town_from_address(self):
        recs = add([], city="46910", address="C/ de Morella, 86, 46910 Sedaví, Valencia")
        self.assertEqual(recs[0]["city"], "Sedaví")

    def test_postcode_city_without_town_falls_back_to_province(self):
        recs = add([], city="46600", address="Av. de la Mestra, 3, 46600",
                   raw={"addr:postcode": "46600"})
        self.assertEqual(recs[0]["city"], "València")

    def test_allcaps_city_from_source_is_titlecased(self):
        recs = add([], city="BARCELONA", lat=41.3874, lon=2.1686)
        self.assertEqual(recs[0]["city"], "Barcelona")

    def test_real_town_from_source_is_preserved(self):
        recs = add([], city="Sedaví")
        self.assertEqual(recs[0]["city"], "Sedaví")

    def test_valencia_default_outside_valencia_is_never_mislabelled(self):
        recs = add([], city="Valencia province", lat=MADRID_LAT, lon=MADRID_LON)
        self.assertNotIn("alencia", recs[0]["city"])

    def test_truncated_street_prefix_city_falls_back_to_postcode_province(self):
        recs = add(
            [],
            city="Via Laet",
            address="Via Laetana 69",
            lat=41.388003,
            lon=2.173182,
            raw={"addr:postcode": "08003"},
        )
        self.assertEqual(recs[0]["city"], "Barcelona")


class PriceClaimTests(unittest.TestCase):
    def test_published_over_cap_price_is_admitted_as_paid_not_unknown(self):
        recs = add([], price_min=0.4)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["price_claim_type"], "confirmed_paid")
        self.assertEqual(recs[0]["charge_claim"]["type"], "paid")
        self.assertEqual(recs[0]["price_min"], 0.4)
        self.assertFalse(recs[0]["is_free"])
        self.assertFalse(recs[0]["is_cheap"])

    def test_genuinely_unknown_price_is_admitted_as_a_lead(self):
        recs = add([], price_min=None)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["price_claim_type"], "price_unknown_lead")
        self.assertFalse(recs[0]["is_free"])

    def test_free_is_admitted(self):
        recs = add([], price_min=0.0)
        self.assertEqual(recs[0]["price_claim_type"], "confirmed_free")

    def test_price_at_the_cap_is_admitted_as_cheap(self):
        recs = add([], price_min=0.25)
        self.assertEqual(recs[0]["price_claim_type"], "confirmed_cheap")

    def test_directory_free_supercharger_claim_is_rejected_without_official_evidence(self):
        self.assertEqual(
            add(
                [],
                name="Supercharger Torrent North, Spain",
                price_min=0.0,
                source="Electromaps web-map API",
                source_url="https://map.electromaps.com/en/p/174606",
                reliability="directory-map-free",
            ),
            [],
        )

    def test_official_free_supercharger_claim_can_still_be_admitted(self):
        self.assertEqual(
            len(
                add(
                    [],
                    name="Municipal Supercharger",
                    price_min=0.0,
                    source="Official city feed",
                    source_url="https://data.example.es/station/1",
                    reliability="official-free",
                )
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
