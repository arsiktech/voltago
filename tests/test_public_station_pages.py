"""Offline fixture tests for Telpark and Elektrum Drive public-page parsers.

Fixtures are trimmed from real responses verified 2026-10-12 (Telpark wp/v2/parkings and
detail JSON-LD; Elektrum public-charging-points HTML). Stdlib unittest only, no network.
"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import source_public_station_pages as sp  # noqa: E402

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def rest_item(pid, slug, city, title, address, lat, lon, prize="2€ / 1€"):
    return {
        "id": pid, "slug": slug, "modified_gmt": "2026-10-09T08:29:54",
        "link": f"https://www.telpark.com/es/ciudades/{city}/{slug}/",
        "title": {"rendered": title},
        "serviceList": [],
        "meta_box": {"header": {"address": address, "latitude": lat, "longitude": lon, "prize": f"<p>{prize}</p>"}},
    }


REST_PAGE = [
    rest_item(61310, "hermanos-fernandez-galiano", "guadalajara", "Parking Hermanos Fernandez Galiano",
              "Calle Hermanos Fernandez Galiano s/n, 19004", "40.636305663345404", "-3.17522073205325"),
    rest_item(70001, "no-ev", "madrid", "Parking Sin Carga", "Calle Uno 1, 28001", "40.4", "-3.7"),
    rest_item(70002, "parking-mercado-de-alvalade", "lisboa", "Parking Mercado de Alvalade",
              "Rua X 5, 1700-001", "38.75", "-9.14"),
    rest_item(70003, "unlabelled-pt", "ciudad-nueva", "Parking PT sin slug", "Rua Y 9, 4000-123", "41.15", "-8.61"),
    rest_item(70004, "parking-escaldes-centre", "andorra", "Parking Escaldes", "Constitución 8-10, AD700", "42.509659", "1.5387602"),
    rest_item(70005, "tenerife", "santa-cruz-de-tenerife", "Parking Tenerife", "Calle Z, 38001", "28.4636", "-16.2518"),
]


def detail_html(*, ev, lat=40.437925, lon=-3.719618, phone="+34 915 44 92 29", with_org_offer=True):
    amenity = [{"@type": "LocationFeatureSpecification", "name": "Reservas", "value": True},
               {"@type": "LocationFeatureSpecification", "name": "Carga eléctrica", "value": ev}]
    parking = {"@type": "ParkingFacility", "name": "Parking Hospital La Concepción", "telephone": phone,
               "address": {"@type": "PostalAddress", "streetAddress": "Avd. Reyes Católicos, 2, 28040",
                           "addressLocality": "Madrid", "addressCountry": "Aparcar en España"},
               "geo": {"@type": "GeoCoordinates", "latitude": lat, "longitude": lon},
               "amenityFeature": amenity}
    org = {"@type": "Organization", "name": "Telpark"}
    if with_org_offer:  # present on EVERY Telpark page; must not count as an EV flag
        org["hasOfferCatalog"] = {"itemListElement": [{"@type": "Offer", "itemOffered": {"name": "Carga eléctrica"}}]}
    doc = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, org, parking]}
    return f'<html><head><script type="application/ld+json">{json.dumps(doc)}</script></head><body>Carga eléctrica</body></html>'


class TelparkParserTests(unittest.TestCase):
    def test_rest_page_normalizes_ids_coords_and_country(self):
        rows = {r["source_id"]: r for r in sp.parse_telpark_parkings_page(REST_PAGE)}
        self.assertEqual(len(rows), 6)
        r = rows["telpark:61310"]
        self.assertEqual((r["lat"], r["lon"]), (40.636305663345404, -3.17522073205325))
        self.assertEqual(r["country"], "ES")
        self.assertEqual(r["name"], "Parking Hermanos Fernandez Galiano")
        self.assertEqual(r["parking_price_text"], "2€ / 1€")
        self.assertIsNone(r["ev_charging"])  # REST alone never proves EV
        self.assertEqual(rows["telpark:70002"]["country"], "PT")   # slug
        self.assertEqual(rows["telpark:70003"]["country"], "PT")   # postcode shape
        self.assertEqual(rows["telpark:70004"]["country"], "AD")
        self.assertEqual(rows["telpark:70005"]["country"], "ES")   # Canaries

    def test_rest_accepts_bytes_and_rejects_error_envelope(self):
        self.assertEqual(len(sp.parse_telpark_parkings_page(json.dumps(REST_PAGE).encode())), 6)
        with self.assertRaises(ValueError):
            sp.parse_telpark_parkings_page({"code": "rest_post_invalid_page_number"})

    def test_bad_coords_become_none(self):
        item = rest_item(1, "x", "madrid", "X", "Calle 1, 28001", "0", "0")
        self.assertIsNone(sp.parse_telpark_parkings_page([item])[0]["lat"])

    def test_detail_ev_true_false_and_org_offer_ignored(self):
        yes = sp.parse_telpark_detail(detail_html(ev=True))
        self.assertIs(yes["ev_charging"], True)
        self.assertEqual((yes["lat"], yes["lon"]), (40.437925, -3.719618))
        no = sp.parse_telpark_detail(detail_html(ev=False))
        self.assertIs(no["ev_charging"], False)  # Organization offer says EV, parking says no
        none = sp.parse_telpark_detail("<html><body>Carga eléctrica</body></html>")
        self.assertIsNone(none["ev_charging"])

    def test_malformed_jsonld_is_skipped(self):
        page = '<script type="application/ld+json">{broken</script>' + detail_html(ev=True)
        self.assertIs(sp.parse_telpark_detail(page)["ev_charging"], True)

    def test_portuguese_phone_flips_country(self):
        row = sp.parse_telpark_parkings_page([rest_item(5, "a", "ciudad-nueva", "A", "Calle sin cp", "40.0", "-3.0")])[0]
        merged = sp.merge_telpark_detail(row, sp.parse_telpark_detail(detail_html(ev=True, phone="+351 21 000 00 00")))
        self.assertEqual(merged["country"], "PT")


def make_fetch(pages, details, log=None):
    def fetch(url, headers=None, timeout=None):
        if log is not None:
            log.append(url)
        if "wp-json/wp/v2/parkings" in url:
            page = int(url.rsplit("page=", 1)[1])
            if page in pages:
                return json.dumps(pages[page]).encode()
            raise RuntimeError("HTTP 400 past last page")
        if url in details:
            value = details[url]
            if isinstance(value, Exception):
                raise value
            return value
        raise RuntimeError("unexpected url " + url)
    return fetch


class TelparkFetchTests(unittest.TestCase):
    def setUp(self):
        self.details: dict = {
            "https://www.telpark.com/es/ciudades/guadalajara/hermanos-fernandez-galiano/": detail_html(ev=True, lat=40.6363, lon=-3.1752),
            "https://www.telpark.com/es/ciudades/madrid/no-ev/": detail_html(ev=False),
            "https://www.telpark.com/es/ciudades/santa-cruz-de-tenerife/tenerife/": detail_html(ev=True, lat=28.46, lon=-16.25),
        }

    def run_fetch(self, **kw):
        log = []
        out = sp.fetch_telpark_rows(make_fetch({1: REST_PAGE}, self.details, log), per_page=100, now=NOW, **kw)
        return out, log

    def test_spain_only_ev_rows_with_honest_fields(self):
        out, log = self.run_fetch()
        ids = [r["source_id"] for r in out["rows"]]
        self.assertEqual(ids, ["telpark:61310", "telpark:70005"])
        self.assertEqual(out["stats"]["portugal_excluded"], 2)
        self.assertEqual(out["stats"]["andorra_excluded"], 1)
        self.assertEqual(out["stats"]["ev_false"], 1)
        self.assertEqual(out["stats"]["detail_fetched"], 3)
        self.assertFalse(any("lisboa" in u or "andorra" in u for u in log))  # excluded rows never fetched
        row = out["rows"][0]
        self.assertEqual(row["evidence"], "directory")
        self.assertEqual(row["connectors"], [])
        self.assertIsNone(row["power_kw"])
        self.assertIsNone(row["charging_tariff"])
        self.assertIsNone(row["availability"])
        self.assertEqual(row["coords_source"], "detail_jsonld_geo")
        self.assertEqual(row["retrieved_at"], "2026-10-12T12:00:00Z")
        self.assertEqual(out["geography"], "ES")

    def test_ids_are_stable_across_runs(self):
        a, _ = self.run_fetch()
        b, _ = self.run_fetch()
        self.assertEqual([r["source_id"] for r in a["rows"]], [r["source_id"] for r in b["rows"]])

    def test_detail_failure_is_unconfirmed_not_emitted(self):
        self.details["https://www.telpark.com/es/ciudades/santa-cruz-de-tenerife/tenerife/"] = RuntimeError("boom")
        out, _ = self.run_fetch()
        self.assertEqual([r["source_id"] for r in out["rows"]], ["telpark:61310"])
        self.assertEqual(out["stats"]["ev_unconfirmed"], 1)
        self.assertEqual(len(out["errors"]), 1)

    def test_detail_budget_bounds_requests(self):
        out, log = self.run_fetch(max_detail_fetches=1)
        self.assertEqual(sum("/ciudades/" in u for u in log), 1)
        self.assertEqual(out["stats"]["ev_unconfirmed"], 2)

    def test_rest_paging_stops_on_short_page_and_caps_pages(self):
        full = [rest_item(i, f"s{i}", "madrid", f"P{i}", "Calle 1, 28001", "40.4", "-3.7") for i in range(1, 3)]
        log = []
        out = sp.fetch_telpark_rows(make_fetch({1: full, 2: full, 3: full}, {}, log), per_page=2, max_pages=2,
                                    max_detail_fetches=0, now=NOW)
        self.assertEqual(out["stats"]["rest_pages"], 2)
        self.assertEqual(sum("wp-json" in u for u in log), 2)

    def test_rest_failure_reports_error(self):
        out = sp.fetch_telpark_rows(make_fetch({}, {}), now=NOW)
        self.assertEqual(out["rows"], [])
        self.assertTrue(out["errors"])


def li(lat, lon, typ, name, addr, conns, fast="1"):
    c = "".join(f'<p class="connector-information">{x}</p>' for x in conns)
    return f'''<li data-latitude="{lat}"
    data-longitude="{lon}"
    data-type="{typ}"
    data-has-fast-charging="{fast}"
    data-power-types="50kW"
    data-connection-types="CCS"
>
    <h1 class="heading">
        <a class="with-icon" href="https://www.google.com/maps?daddr={lat},{lon}">
            <span class="icon"><svg><use xlink:href="x.svg#a"/></svg></span>
            {name}
        </a>
    </h1>
    <div class="details">
        <p class="address">{addr}</p>
        <p class="description">The charging station operates daily 00:00 - 24:00.<br />
<br />
At this station you will charge with 100% green energy.</p>
        {c}
    </div>
</li>'''


ELEKTRUM_HTML = f'''<html><body><div class="stations-content"><section class="accordion">
<section id="station-0"><header><h3 class="heading">Abja-Paluoja</h3></header><ul class="stations">
{li("58.125991175655", "25.355570786446", "latvenergo", "Abja Konsum", "Pärnu mnt 13", ["2 x 50kW DC (CCS)"])}
</ul></section>
<section id="station-122"><header><h3 class="heading">Daugavpils</h3></header><ul class="stations">
{li("55.878421", "26.51442", "partner", "Daugavpils S1 (0141)", "Stadiona iela 1", ["1 x 43kW AC (Type 2)", "2 x 50kW DC (CHAdeMO, CCS)"])}
{li("55.878421", "26.51442", "partner", "Daugavpils S1 (0141)", "Stadiona iela 1", ["1 x 43kW AC (Type 2)"])}
</ul></section>
<section id="station-200"><header><h3 class="heading">Vilnius</h3></header><ul class="stations">
{li("54.687", "25.279", "partner", "Vilnius Centras", "Gedimino pr. 1", ["weird text"], fast="0")}
{li("0", "0", "partner", "Bad Coords", "Nowhere", [])}
</ul></section></div></section></body></html>'''


class ElektrumTests(unittest.TestCase):
    def setUp(self):
        self.rows = sp.parse_elektrum_public_points(ELEKTRUM_HTML)

    def test_rows_coordinates_names_and_cities(self):
        self.assertEqual(len(self.rows), 4)  # bad-coords row dropped, duplicate kept
        a = self.rows[0]
        self.assertEqual((a["name"], a["city"], a["country"]), ("Abja Konsum", "Abja-Paluoja", "EE"))
        self.assertEqual((a["lat"], a["lon"]), (58.125991175655, 25.355570786446))
        self.assertEqual(a["network_type"], "latvenergo")
        self.assertEqual(a["address"], "Pärnu mnt 13")
        self.assertIn("00:00 - 24:00", a["hours_text"])
        self.assertEqual(self.rows[1]["country"], "LV")
        self.assertEqual(self.rows[1]["station_code"], "0141")
        self.assertEqual(self.rows[3]["country"], "LT")

    def test_connectors_structured_without_invention(self):
        conns = self.rows[1]["connectors"]
        self.assertEqual([c["count"] for c in conns], [1, 2])
        self.assertEqual(conns[0]["plugs"], ["Type 2"])
        self.assertEqual(conns[1]["plugs"], ["CHAdeMO", "CCS"])
        self.assertEqual(conns[1]["power_kw"], 50.0)
        self.assertEqual(conns[1]["current"], "DC")
        self.assertEqual(self.rows[1]["power_kw_max"], 50.0)
        odd = self.rows[3]["connectors"][0]
        self.assertEqual(odd["raw"], "weird text")
        self.assertIsNone(odd["power_kw"])
        self.assertEqual(odd["plugs"], [])
        self.assertIsNone(self.rows[3]["power_kw_max"])
        self.assertFalse(self.rows[3]["has_fast_charging"])
        for r in self.rows:
            self.assertIsNone(r["charging_tariff"])
            self.assertIsNone(r["availability"])
            self.assertEqual(r["evidence"], "directory")

    def test_ids_unique_and_stable_with_duplicates(self):
        ids = [r["source_id"] for r in self.rows]
        self.assertEqual(len(set(ids)), 4)
        self.assertTrue(ids[2].endswith("-2"))
        again = [r["source_id"] for r in sp.parse_elektrum_public_points(ELEKTRUM_HTML)]
        self.assertEqual(ids, again)

    def test_no_spain_rows_and_country_boxes(self):
        self.assertEqual(sp.elektrum_country(40.4, -3.7)[0], "unknown")
        self.assertEqual(sp.elektrum_country(59.4, 24.7)[0], "EE")
        self.assertEqual(sp.elektrum_country(56.9, 24.1)[0], "LV")
        # EE/LV box overlap with no address hint stays unknown rather than guessed.
        self.assertEqual(sp.elektrum_country(57.6, 24.0), ("unknown", "ambiguous"))

    def test_fetch_orchestration_injected(self):
        calls = []

        def fetch(url, headers=None, timeout=None):
            calls.append(url)
            return ELEKTRUM_HTML.encode()

        out = sp.fetch_elektrum_rows(fetch, now=NOW)
        self.assertEqual(calls, [sp.ELEKTRUM_URL])
        self.assertEqual(out["spain_rows"], 0)
        self.assertEqual(out["stats"]["by_country"], {"EE": 1, "LT": 1, "LV": 2})
        self.assertEqual(out["rows"][0]["retrieved_at"], "2026-10-12T12:00:00Z")

    def test_fetch_failure_and_host_policy(self):
        def boom(url, headers=None, timeout=None):
            raise OSError("down")

        out = sp.fetch_elektrum_rows(boom, now=NOW)
        self.assertEqual(out["rows"], [])
        self.assertTrue(out["errors"])
        with self.assertRaises(ValueError):
            sp.fetch_elektrum_rows(boom, url="https://evil.example/x")
        with self.assertRaises(ValueError):
            sp.fetch_elektrum_rows(boom, url="http://elektrum.ee/x")


class CombinedTests(unittest.TestCase):
    def test_combined_keeps_geographies_separate(self):
        def fetch(url, headers=None, timeout=None):
            if url == sp.ELEKTRUM_URL:
                return ELEKTRUM_HTML
            return make_fetch({1: REST_PAGE}, {
                "https://www.telpark.com/es/ciudades/guadalajara/hermanos-fernandez-galiano/": detail_html(ev=True),
            })(url, headers, timeout)

        out = sp.fetch_public_station_pages(fetch, now=NOW, max_detail_fetches=10)
        self.assertEqual(out["telpark"]["geography"], "ES")
        self.assertIn("Baltics", out["elektrum_drive"]["geography"])
        self.assertTrue(all(r["country"] == "ES" for r in out["telpark"]["rows"]))
        self.assertTrue(all(r["country"] in {"EE", "LV", "LT", "unknown"} for r in out["elektrum_drive"]["rows"]))


if __name__ == "__main__":
    unittest.main()
