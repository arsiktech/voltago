#!/usr/bin/env python3
"""Location × source matrix: five honest cell states."""
from __future__ import annotations

import json
import sys
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import source_matrix as sm  # noqa: E402

LAT, LON = 41.6100, 0.6200
METRE = 1 / 111_320
BBOX = {"latitude_sw": 41.60, "longitude_sw": 0.61, "latitude_ne": 41.62, "longitude_ne": 0.63}


NOW = datetime(2026, 11, 6, 14, 0, tzinfo=timezone.utc)
LISTING_FILES = {"dgt": "dgt_nap_electrolineras.xml", "osm": "osm_es_area_chargers_00.json", "ocm": "ocm_poi.json.gz",
                 "mapareve": "mapareve_public_tenerife_p1.json", "canarias": "canarias_tecarga_map4.geojson.json"}


def fetched(hours_ago: float, name: str, *, now: datetime = NOW, **cache: bool) -> dict:
    stamp = (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {"cache_name": name, "fetched_at": stamp, "cache": cache}


def fresh_evidence(*artifacts: dict) -> dict:
    """Every listing fetched an hour ago, plus the given files."""
    return {"artifacts": [fetched(1, name) for name in LISTING_FILES.values()] + list(artifacts)}


def row(id_: str, *sources: str, north_m: float = 0, price_sources=(), fallback: bool = False) -> dict:
    return {
        "id": id_, "name": f"Station {id_}", "address": "Av. Test 1", "city": "Lleida",
        "lat": LAT + north_m * METRE, "lon": LON,
        "sources": [{"name": s, "url": f"https://example.test/{id_}", "evidence": "e",
                     **({"source_cache_fallback": True, "source_fetched_at": "2026-10-20T00:00:00Z"} if fallback else {})}
                    for s in sources],
        "price_evidence": [{"source": s, "price_per_kwh": 0.49, "source_url": "https://example.test/p"} for s in price_sources],
    }


def cells(intel: dict, id_: str) -> dict:
    matrix = sm.build_matrix({"fetch_evidence": fresh_evidence(), **intel}, BBOX, now=NOW)
    location = next(item for item in matrix["locations"] if item["id"] == id_)
    return location["cells"]


class SourceMatrixTests(unittest.TestCase):
    def test_listed_and_not_listed_for_complete_national_sources(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry", "OpenStreetMap public tags")]}
        result = cells(intel, "a")
        self.assertEqual(result["dgt"]["state"], "listed")
        self.assertEqual(result["osm"]["state"], "listed")
        self.assertEqual(result["ocm"]["state"], "not_listed")
        self.assertEqual(result["dgt"]["entries"][0]["url"], "https://example.test/a")

    def test_network_and_out_of_scope_sources_never_claim_absence(self) -> None:
        result = cells({"records": [row("a", "DGT National Access Point charging registry")]}, "a")
        self.assertEqual(result["fenie"]["state"], "unknown")
        self.assertIn("own network", result["fenie"]["reason"])
        self.assertEqual(result["canarias"]["state"], "unknown")
        self.assertIn("Outside", result["canarias"]["reason"])

    def test_price_only_sources_are_restricted_or_unknown(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry", price_sources=("Repsol",))]}
        result = cells(intel, "a")
        self.assertEqual(result["repsol"]["state"], "restricted")
        self.assertEqual(result["repsol"]["entries"][0]["price_per_kwh"], 0.49)
        self.assertEqual(result["chargemap"]["state"], "unknown")

    def test_a_nearby_unmatched_listing_is_uncertain_not_absent(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry"),
                             row("b", "OpenStreetMap public tags", north_m=50)]}
        result = cells(intel, "a")
        self.assertEqual(result["osm"]["state"], "uncertain")
        self.assertEqual(result["osm"]["candidate"]["id"], "b")
        self.assertEqual(result["osm"]["candidate"]["distance_m"], 50)

    def test_a_listing_beyond_80_m_does_not_make_a_cell_uncertain(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry"),
                             row("b", "OpenStreetMap public tags", north_m=120)]}
        self.assertEqual(cells(intel, "a")["osm"]["state"], "not_listed")

    def test_fallback_or_failed_sources_cannot_prove_absence(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry"),
                             row("far", "OpenStreetMap public tags", north_m=5000, fallback=True)],
                 "errors": [{"source": "source_openchargemap", "error": "RuntimeError: x"}]}
        result = cells(intel, "a")
        self.assertEqual(result["osm"]["state"], "unknown")
        self.assertIn("cached copy from 2026-10-20", result["osm"]["reason"])
        self.assertEqual(result["ocm"]["state"], "unknown")
        self.assertIn("failed", result["ocm"]["reason"])

    def test_fetch_evidence_fallback_degrades_its_column(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry")],
                 "fetch_evidence": {"artifacts": [{"cache_name": "openchargemap_es.json", "fetched_at": "2026-10-19T00:00:00Z",
                                                   "cache": {"fallback": True}}]}}
        self.assertEqual(cells(intel, "a")["ocm"]["state"], "unknown")

    def test_failed_metadata_fetch_says_nothing_about_the_listing(self) -> None:
        rows_at, metadata_at = "2026-11-06T18:00:00Z", "2026-11-01T00:00:00Z"
        for prefix, source in (("catalonia_icaen", "catalonia"), ("castilla_leon", "castilla_leon")):
            intel = {"errors": [], "fetch_evidence": {"artifacts": [
                {"cache_name": f"{prefix}.json", "fetched_at": rows_at, "cache": {"fallback": False}},
                {"cache_name": f"{prefix}_metadata.json", "fetched_at": metadata_at, "cache": {"fallback": True}}]}}
            self.assertNotIn(source, sm.degraded_columns(intel, []))
            self.assertEqual(sm.column_fetches(intel)[source],
                             {"at": rows_at, "oldest_part_at": rows_at, "parts": 1, "failed_parts": 0})

    def test_listing_clocks_come_only_from_that_listing(self) -> None:
        record = row("a", "DGT National Access Point charging registry", "OpenStreetMap public tags")
        record["source_fetched_at"] = "2026-11-04T00:00:00Z"  # Oldest across sources: not attributable to a cell.
        record["sources"][0].update(source_fetched_at="2026-11-03T12:39:31.040277Z", source_published_at="not a date",
                                    source_last_updated="2024-08-08T14:21:38")
        result = cells({"records": [record]}, "a")
        self.assertEqual(result["dgt"]["entries"][0]["clocks"], {
            "fetched_at": "2026-11-03T12:39:31Z", "published_at": None, "source_last_updated": "2024-08-08T14:21:38"})
        self.assertEqual(result["osm"]["entries"][0]["clocks"], {
            "fetched_at": None, "published_at": None, "source_last_updated": None})

    def test_price_clocks_keep_their_own_meaning(self) -> None:
        record = row("a", "DGT National Access Point charging registry", price_sources=("Electromaps",))
        record["price_evidence"][0].update(fetched_at="2026-10-20T10:20:00.5Z", source_updated_at="2025-05-27T14:21:58+0000")
        self.assertEqual(cells({"records": [record]}, "a")["electromaps"]["entries"][0]["clocks"], {
            "fetched_at": "2026-10-20T10:20:00Z", "published_at": None, "source_last_updated": "2025-05-27T14:21:58Z"})

    def test_clock_never_turns_a_bad_value_into_a_time(self) -> None:
        for value in (None, "", " ", "yesterday", "03/11/2026", 0, 1793973099, True, "0001-01-01T00:00:00+14:00", "1970-01-01T00:00:00Z"):
            with self.subTest(value=value):
                self.assertIsNone(sm.clock(value))
        self.assertEqual(sm.clock("2026-11-03"), "2026-11-03")
        self.assertEqual(sm.clock("2026-11-03T13:00:00+01:00"), "2026-11-03T12:00:00Z")

    def test_column_last_fetch_reports_newest_oldest_and_failed_files(self) -> None:
        intel = {"records": [row("a", "DGT National Access Point charging registry")], "fetch_evidence": {"artifacts": [
            {"cache_name": "dgt_nap_electrolineras.xml", "fetched_at": "2026-11-03T12:39:31.5Z", "cache": {"hit": True}},
            {"cache_name": "osm_es_area_chargers_00.json", "fetched_at": "2026-11-04T00:35:00Z", "cache": {}},
            {"cache_name": "osm_es_area_chargers_01.json", "fetched_at": "2026-11-02T18:39:00Z", "cache": {"fallback": True}},
            {"cache_name": "barcelona_endolla_status.json", "fetched_at": "2026-11-04T12:36:00Z", "cache": {}},
            {"cache_name": "madrid_public_chargers.csv", "fetched_at": "unknown", "cache": {}},
            {"cache_name": "panoramax_picture_1.json", "fetched_at": "2026-11-04T12:36:00Z", "cache": {}},
        ]}}
        columns = {c["id"]: c for c in sm.build_matrix(intel, BBOX)["columns"]}
        self.assertEqual(columns["dgt"]["last_fetch"], {"at": "2026-11-03T12:39:31Z", "oldest_part_at": "2026-11-03T12:39:31Z", "parts": 1, "failed_parts": 0})
        self.assertEqual(columns["osm"]["last_fetch"], {"at": "2026-11-04T00:35:00Z", "oldest_part_at": "2026-11-02T18:39:00Z", "parts": 2, "failed_parts": 1})
        self.assertEqual(columns["endolla"]["last_fetch"]["at"], "2026-11-04T12:36:00Z")
        self.assertIsNone(columns["madrid"]["last_fetch"])
        self.assertIsNone(columns["lidl"]["last_fetch"])

    def test_columns_are_unknown_without_fetch_evidence(self) -> None:
        matrix = sm.build_matrix({"records": [], "generated_at": "2026-11-04T12:37:55Z"}, BBOX)
        self.assertTrue(all(column["last_fetch"] is None for column in matrix["columns"]))

    def test_a_cached_copy_with_no_date_does_not_print_a_blank_date(self) -> None:
        intel = {"records": [row("a", "OpenStreetMap public tags", fallback=True)]}
        intel["records"][0]["sources"][0].pop("source_fetched_at")
        self.assertEqual(sm.degraded_columns(intel, intel["records"])["osm"], "Latest fetch failed; cached copy from an unknown date.")

    def test_locations_outside_the_area_are_excluded_and_the_limit_is_reported(self) -> None:
        intel = {"records": [row(str(i), "DGT National Access Point charging registry", north_m=i) for i in range(5)]
                 + [row("outside", "DGT National Access Point charging registry", north_m=50_000)]}
        matrix = sm.build_matrix(intel, BBOX, limit=3)
        self.assertEqual(len(matrix["locations"]), 3)
        self.assertTrue(matrix["truncated"])
        self.assertNotIn("outside", [item["id"] for item in matrix["locations"]])
        self.assertEqual(len(matrix["columns"]), len(sm.COLUMNS))


class FreshnessWindowTests(unittest.TestCase):
    RECORDS = [row("a", "DGT National Access Point charging registry")]

    def ocm(self, *artifacts: dict, now: datetime = NOW) -> dict:
        intel = {"records": self.RECORDS, "fetch_evidence": {"artifacts": list(artifacts)}}
        location = sm.build_matrix(intel, BBOX, now=now)["locations"][0]
        return location["cells"]["ocm"]

    def test_windows_are_cache_ttl_plus_one_refresh_and_a_margin(self) -> None:
        windows = {c["id"]: sm.freshness_window_hours(c) for c in sm.COLUMNS if c["coverage"] == "complete"}
        self.assertEqual(windows, {"dgt": 31, "osm": 19, "ocm": 175, "mapareve": 19, "canarias": 31})

    def test_window_inputs_match_the_ingest_ttls_and_the_refresh_timer(self) -> None:
        ingest = (ROOT / "scripts/ingest_sources.py").read_text(encoding="utf-8")
        for expected in ('"dgt_nap_electrolineras.xml", ttl_hours=24', 'f"osm_es_area_chargers_{label}.json", ttl_hours=12',
                         '"ocm_poi.json.gz", ttl_hours=7 * 24', "MAPAREVE_PUBLIC_TTL_HOURS = 12",
                         'f"mapareve_public_tenerife_p{page}.json"', 'CANARIAS_TE_RECARGA_CACHE_NAME = "canarias_tecarga_map4.geojson.json"'):
            self.assertIn(expected, ingest)
        self.assertRegex(ingest, r"CANARIAS_TE_RECARGA_CACHE_NAME,\s+ttl_hours=24,")
        timer = (ROOT / "deploy/systemd/volta-data-refresh.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 00,06,12,18:30:00 UTC", timer)
        self.assertEqual(sm.REFRESH_HOURS, 6)

    def test_every_complete_column_names_its_listing_files(self) -> None:
        complete = [c for c in sm.COLUMNS if c["coverage"] == "complete"]
        self.assertEqual({c["id"] for c in complete}, set(LISTING_FILES))
        for column in complete:
            self.assertTrue(LISTING_FILES[column["id"]].startswith(column["listing_files"]))

    def test_a_listing_inside_its_window_can_show_not_listed(self) -> None:
        self.assertEqual(self.ocm(fetched(175, "ocm_poi.json.gz"))["state"], "not_listed")

    def test_a_listing_past_its_window_is_unknown_and_says_why(self) -> None:
        cell = self.ocm(fetched(175.5, "ocm_poi.json.gz"))
        self.assertEqual(cell["state"], "unknown")
        self.assertIn("175 h freshness window", cell["reason"])
        self.assertIn((NOW - timedelta(hours=175.5)).strftime("%Y-%m-%dT%H:%M:%SZ"), cell["reason"])

    def test_each_source_uses_its_own_window(self) -> None:
        intel = {"records": self.RECORDS + [row("b", "OpenChargeMap", north_m=500)],
                 "fetch_evidence": {"artifacts": [fetched(30, "osm_es_area_chargers_00.json"), fetched(30, "ocm_poi.json.gz"),
                                                  fetched(30, "dgt_nap_electrolineras.xml")]}}
        location = next(item for item in sm.build_matrix(intel, BBOX, now=NOW)["locations"] if item["id"] == "b")
        self.assertEqual(location["cells"]["osm"]["state"], "unknown")
        self.assertEqual(location["cells"]["dgt"]["state"], "not_listed")

    def test_no_evidence_for_the_listing_is_unknown(self) -> None:
        for name, artifacts in (("none", ()), ("other source", (fetched(1, "dgt_nap_electrolineras.xml"),))):
            with self.subTest(name):
                cell = self.ocm(*artifacts)
                self.assertEqual(cell["state"], "unknown")
                self.assertIn("No fetch evidence", cell["reason"])

    def test_an_unusable_fetch_time_is_unknown(self) -> None:
        for value in ("unknown", "", None, "2026-11-06", "2026-11-06T13:00:00"):
            with self.subTest(value=value):
                cell = self.ocm({"cache_name": "ocm_poi.json.gz", "fetched_at": value, "cache": {}})
                self.assertEqual(cell["state"], "unknown")
                self.assertIn("no usable fetch time", cell["reason"])

    def test_a_fetch_time_in_the_future_is_unknown(self) -> None:
        cell = self.ocm(fetched(-1, "ocm_poi.json.gz"))
        self.assertEqual(cell["state"], "unknown")
        self.assertIn("in the future", cell["reason"])
        self.assertEqual(self.ocm(fetched(-0.05, "ocm_poi.json.gz"))["state"], "not_listed")

    def test_the_oldest_file_decides_for_a_multi_file_source(self) -> None:
        intel = {"records": self.RECORDS, "fetch_evidence": {"artifacts": [
            fetched(1, "osm_es_area_chargers_00.json"), fetched(20, "osm_es_area_chargers_01.json")]}}
        cell = sm.build_matrix(intel, BBOX, now=NOW)["locations"][0]["cells"]["osm"]
        self.assertEqual(cell["state"], "unknown")
        self.assertIn("Oldest of 2 listing files", cell["reason"])

    def test_one_file_with_an_unusable_time_blocks_a_multi_file_source(self) -> None:
        intel = {"records": self.RECORDS, "fetch_evidence": {"artifacts": [
            fetched(1, "osm_es_area_chargers_00.json"), {"cache_name": "osm_es_area_chargers_01.json", "fetched_at": "unknown"}]}}
        self.assertEqual(sm.build_matrix(intel, BBOX, now=NOW)["locations"][0]["cells"]["osm"]["state"], "unknown")

    def test_only_the_newest_record_of_a_file_counts(self) -> None:
        self.assertEqual(self.ocm(fetched(500, "ocm_poi.json.gz"), fetched(1, "ocm_poi.json.gz"))["state"], "not_listed")

    def test_detail_pages_do_not_age_the_listing(self) -> None:
        intel = {"records": [dict(row("a", "DGT National Access Point charging registry"), lat=28.1, lon=-15.4)],
                 "fetch_evidence": {"artifacts": [fetched(1, "canarias_tecarga_map4.geojson.json"),
                                                  fetched(900, "canarias_details/abc.html")]}}
        bbox = {"latitude_sw": 28.05, "longitude_sw": -15.45, "latitude_ne": 28.15, "longitude_ne": -15.35}
        self.assertEqual(sm.build_matrix(intel, bbox, now=NOW)["locations"][0]["cells"]["canarias"]["state"], "not_listed")
        intel["fetch_evidence"]["artifacts"][0] = fetched(32, "canarias_tecarga_map4.geojson.json")
        self.assertEqual(sm.build_matrix(intel, bbox, now=NOW)["locations"][0]["cells"]["canarias"]["state"], "unknown")

    def test_the_build_time_cannot_refresh_an_old_fetch_or_expire_a_new_one(self) -> None:
        now = datetime.now(timezone.utc)
        recent_build = (now - timedelta(minutes=1)).isoformat()
        old_build = (now - timedelta(days=30)).isoformat()
        for build, hours, expected in ((recent_build, 200, "unknown"), (old_build, 1, "not_listed")):
            with self.subTest(build=build):
                intel = {"generated_at": build, "records": self.RECORDS,
                         "fetch_evidence": {"artifacts": [fetched(hours, "ocm_poi.json.gz", now=now)]}}
                self.assertEqual(sm.build_matrix(intel, BBOX)["locations"][0]["cells"]["ocm"]["state"], expected)

    def test_a_stale_column_is_marked_in_the_header(self) -> None:
        intel = {"records": self.RECORDS, "fetch_evidence": {"artifacts": [fetched(200, "ocm_poi.json.gz")]}}
        columns = {c["id"]: c for c in sm.build_matrix(intel, BBOX, now=NOW)["columns"]}
        self.assertIn("freshness window", columns["ocm"]["degraded"])

    def test_a_failed_fetch_outranks_age_and_a_place_outside_scope_says_so(self) -> None:
        intel = {"records": self.RECORDS, "fetch_evidence": {"artifacts": [
            fetched(200, "ocm_poi.json.gz", fallback=True), fetched(200, "canarias_tecarga_map4.geojson.json")]}}
        result = sm.build_matrix(intel, BBOX, now=NOW)["locations"][0]["cells"]
        self.assertIn("cached copy", result["ocm"]["reason"])
        self.assertEqual(result["canarias"]["reason"], "Outside this source's area.")

    def test_network_and_price_columns_are_not_judged_by_a_window(self) -> None:
        columns = {c["id"]: c for c in sm.build_matrix({"records": self.RECORDS}, BBOX, now=NOW)["columns"]}
        self.assertIsNone(columns["fenie"]["degraded"])
        self.assertIsNone(columns["repsol"]["degraded"])


class LocationSourcesEndpointTests(unittest.TestCase):
    def test_endpoint_requires_a_small_bbox(self) -> None:
        import server
        intel = {"records": [row("a", "DGT National Access Point charging registry")]}
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        import threading
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}/api/location-sources"
        try:
            with patch("server.load_intelligence_meta", return_value=intel):
                with urllib.request.urlopen(f"{base}?bbox=41.60,0.61,41.62,0.63") as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["locations"][0]["id"], "a")
                for query in ("", "?bbox=40,0,42,2"):
                    with self.subTest(query=query), self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(base + query)
                    self.assertEqual(caught.exception.code, 400)
                    caught.exception.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
