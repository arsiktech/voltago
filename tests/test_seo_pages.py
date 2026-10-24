#!/usr/bin/env python3
"""Crawlable Spanish city and station pages, and the dynamic sitemap."""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import seo_pages as sp  # noqa: E402


def station(id_: str, *, city: str = "València", sources=("DGT National Access Point charging registry",), price=None,
            free=False, name: str = "Estación Test", lat: float = 39.47) -> dict:
    return {
        "id": id_, "name": name, "address": "Carrer de Colón, 1", "city": city, "lat": lat, "lon": -0.37,
        "price_min": 0.0 if free else price, "is_free": free, "connector_labels": ["Type 2", "CCS2"], "max_power_kw": 50,
        "total_evse": 4, "charge_claim": {"source": sources[0]}, "raw": {"operator": "Iberdrola"},
        "sources": [{"name": name_, "url": "https://example.test/s", "evidence": "Listed.", "license": "CC BY"} for name_ in sources],
    }


def city_rows(n: int, city: str = "València") -> list:
    return [station(f"s{i}", city=city) for i in range(n)]


class SeoPagesTests(unittest.TestCase):
    def test_station_pages_only_for_corroborated_or_priced_stations(self) -> None:
        self.assertFalse(sp.has_page(station("a")))
        self.assertTrue(sp.has_page(station("b", sources=("DGT National Access Point charging registry", "OpenStreetMap public tags"))))
        self.assertTrue(sp.has_page(station("c", price=0.39)))
        self.assertTrue(sp.has_page(station("d", free=True)))

    def test_cities_need_five_stations_and_placeholders_are_not_cities(self) -> None:
        index = sp.SiteIndex(city_rows(5) + city_rows(4, "Alzira") + city_rows(6, "Spain"), "2026-10-24T19:55:00Z")
        self.assertEqual(list(index.cities), ["valencia"])
        self.assertEqual(index.city_names["valencia"], "València")

    def test_station_slug_is_stable_and_url_safe(self) -> None:
        slug = sp.station_slug(station("intel_x_1", name="Ñandú — Plaça Major!"))
        self.assertEqual(slug, sp.station_slug(station("intel_x_1", name="Ñandú — Plaça Major!")))
        self.assertRegex(slug, r"^nandu-placa-major-[0-9a-f]{8}$")

    def test_pages_never_claim_a_price_or_live_status_that_no_source_gives(self) -> None:
        record = station("p", sources=("DGT National Access Point charging registry", "OpenStreetMap public tags"))
        index = sp.SiteIndex(city_rows(5) + [record], None)
        page = sp.render_station(index, sp.station_slug(record))
        self.assertIn("Ninguna fuente publica el precio", page)
        self.assertIn("La disponibilidad en directo no se muestra aquí", page)
        self.assertNotIn("Disponible ahora", page)

    def test_free_claim_names_its_source(self) -> None:
        record = station("f", free=True, sources=("Feníe Energía public map (Gratuitos)",))
        index = sp.SiteIndex([record], None)
        self.assertIn("Gratis según Feníe Energía public map (Gratuitos)", sp.render_station(index, sp.station_slug(record)))

    def test_a_price_says_when_its_source_was_fetched(self) -> None:
        record = station("d", price=0.51, sources=("MapaREVE public OCPI snapshot",))
        record["sources"][0]["source_fetched_at"] = "2026-10-20T19:38:36Z"
        self.assertEqual(sp.price_text(record), "Desde 0.51 €/kWh según MapaREVE public OCPI snapshot (dato del 2026-10-20).")

    def test_hostile_text_is_escaped_including_json_ld(self) -> None:
        record = station("x", price=0.2, name='Evil</script><script>alert(1)</script>"')
        index = sp.SiteIndex([record], None)
        page = sp.render_station(index, sp.station_slug(record))
        self.assertNotIn("<script>alert(1)", page)
        self.assertEqual(page.count("</script>"), 1)  # only the JSON-LD block closes

    def test_sitemap_lists_home_index_cities_and_station_pages(self) -> None:
        priced = station("q", price=0.3)
        index = sp.SiteIndex(city_rows(5) + [priced], "2026-10-24T19:55:00Z")
        sitemap = sp.render_sitemap(index)
        self.assertIn("<loc>https://voltago.app/cargadores/valencia</loc>", sitemap)
        self.assertIn(f"<loc>https://voltago.app/cargador/{sp.station_slug(priced)}</loc>", sitemap)
        self.assertIn("<lastmod>2026-10-24</lastmod>", sitemap)
        self.assertEqual(sitemap.count("<loc>"), 4)

    def test_unknown_paths_are_404_pages(self) -> None:
        index = sp.SiteIndex(city_rows(5), None)
        self.assertEqual(sp.route(index, "/cargadores/atlantis")[0], 404)
        self.assertEqual(sp.route(index, "/cargador/nope-00000000")[0], 404)
        self.assertEqual(sp.route(index, "/cargadores/valencia/")[0], 200)


class CrawlableRouteTests(unittest.TestCase):
    def test_server_serves_pages_and_sitemap_from_live_data(self) -> None:
        import server
        intel = {"generated_at": "2026-10-24T19:55:00Z", "records": city_rows(5) + [station("q", price=0.3)]}
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with patch("server.load_intelligence_meta", return_value=intel):
                with urllib.request.urlopen(base + "/cargadores/valencia") as response:
                    self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
                    self.assertIn("Puntos de recarga en València", response.read().decode())
                with urllib.request.urlopen(base + "/sitemap.xml") as response:
                    self.assertIn("/cargadores/valencia", response.read().decode())
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + "/cargador/nope-00000000")
                self.assertEqual(caught.exception.code, 404)
                caught.exception.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
