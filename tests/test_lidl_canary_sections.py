#!/usr/bin/env python3
"""Lidl official store list: Canary section scoping and pinned store coordinates.

The official Lidl page is the only free-charge authority for its stores, so the adapter must
(a) ingest exactly the sections Volta holds verified coordinates for, (b) never call a live
geocoder for a pinned store, and (c) keep one pin per store when the page repeats a row.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ing  # noqa: E402


def artifact(body: str) -> ing.FetchArtifact:
    return ing.FetchArtifact(
        body=body.encode("utf-8"),
        url="https://empresa.lidl.es/puntos-de-recarga",
        cache_name="lidl_puntos_recarga.html",
        fetched_at="2026-09-16T00:00:00Z",
        age_seconds=0.0,
        hit=False,
        fallback=False,
        stale=False,
    )


def link(label: str, query: str) -> str:
    return f'<li><a href="https://www.google.com/maps/search/?api=1&amp;query={query}">{label}</a></li>'


PAGE = (
    "<p><strong>Fuerteventura</strong></p><ul>"
    + link("Las Palmas - Calle El Guirre, s/n (CP 35640) - ( 1 cargador - Programa de Subvenciones MOVES II )", "Las+Palmas+-+Calle+El+Guirre")
    + "</ul>"
    "<p><strong>Tenerife</strong></p><ul>"
    + link("Puerto de la Cruz - Ctra Icod-Sta Cruz s/n (CP 38400)", "Puerto+de+la+Cruz")
    + link("Icod de Los Vinos - Ctra. General del Norte, 26 (CP 38434)", "Icod")
    + link("Ctra. General del Norte, 26 - 38434, Icod de Los Vinos (Tenerife)", "28.3679,-16.7136")
    + link("Adeje - Calle Charfa, 28 (CP 46012)", "Adeje")
    + "</ul>"
    "<p><strong>Córdoba</strong></p><ul>"
    + link("Córdoba - Carretera de Palma, 14005", "Cordoba")
    + "</ul>"
)


class LidlCanarySectionsTest(unittest.TestCase):
    def run_adapter(self):
        records: list[dict] = []
        with mock.patch.object(ing, "cached_fetch", return_value=artifact(PAGE)), \
             mock.patch.object(ing, "nominatim_geocode", side_effect=AssertionError("pinned store needed a live geocoder")):
            ing.source_lidl(records)
        return records

    def test_only_verified_sections_ingest_and_pinned_stores_need_no_geocoder(self):
        names = [r["name"] for r in self.run_adapter()]
        self.assertEqual(
            sorted(names),
            [
                "Lidl — Icod de Los Vinos - Ctra. General del Norte, 26",
                "Lidl — Las Palmas - Calle El Guirre, s/n",
                "Lidl — Puerto de la Cruz - Ctra Icod-Sta Cruz s/n",
            ],
        )

    def test_pinned_coordinates_and_provenance_survive_into_the_record(self):
        records = self.run_adapter()
        pdc = next(r for r in records if "Puerto de la Cruz" in r["name"])
        self.assertAlmostEqual(pdc["lat"], 28.39831, places=5)
        self.assertAlmostEqual(pdc["lon"], -16.54171, places=5)
        self.assertEqual(pdc["city"], "Puerto de la Cruz")
        self.assertEqual(pdc["price_min"], 0.0)
        self.assertEqual(pdc["status"], "VERIFY")
        self.assertEqual(pdc["reliability"], "official-free-policy")
        self.assertEqual(pdc["raw"]["coordinate_source"], "osm_lidl_store_poi")
        self.assertIn("OpenStreetMap way/91505373", pdc["raw"]["coordinate_evidence"])
        self.assertIn("30 minutes free charging", pdc["free_condition"])
        # The subsidy footnote belongs to the source row, never to the pin's identity.
        feuerte = next(r for r in records if "El Guirre" in r["name"])
        self.assertEqual(feuerte["city"], "Fuerteventura")
        self.assertAlmostEqual(feuerte["lat"], 28.71184, places=5)

    def test_repeated_store_row_stays_one_pin(self):
        records = self.run_adapter()
        # "Icod" appears in both the store's own name and its pinned road name; count rows.
        icod = [r for r in records if r["name"].startswith("Lidl — Icod")]
        self.assertEqual(len(icod), 1)
        # The pinned store object wins over the page's own (street-drifted) link point.
        self.assertAlmostEqual(icod[0]["lat"], 28.38029, places=5)


if __name__ == "__main__":
    unittest.main()
