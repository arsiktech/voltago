#!/usr/bin/env python3
"""A charger that leaves its source is kept by ID, then retires, and cannot hide a bad build."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
import agent_mcp  # noqa: E402
from station_identity import preserve_record_ids  # noqa: E402
from station_retention import (  # noqa: E402
    GRACE_DAYS, MARKER, RETAINED_KEY, RETIRED_KEY, apply_retention, identity_baseline, per_run_cap, retained_cap)
from tests import _support  # noqa: E402


def setUpModule():
    _support.isolate_community_store()


SOURCE = "Etecnic public charger map"
T0 = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def station(row_id: str, source: str = SOURCE, post: str | None = None, **changes) -> dict:
    url = "https://etecnic.net/mapa"
    row = {"id": row_id, "name": f"Etecnic — {row_id}", "lat": 42.6, "lon": -5.6, "source": source, "source_url": url,
           "sources": [{"name": source, "url": url}], "parking": {"kind": "unknown"},
           "reliability": "official-charger-location-price-unknown", "status": "VERIFY",
           "raw": {"etecnic_charger_id": post or row_id}}
    row.update(changes)
    return row


def rows(count: int, source: str = SOURCE) -> list:
    return [station(f"s{index}", source, post=str(index)) for index in range(count)]


def listing(cache_name: str, moment: datetime = T0, **fields) -> dict:
    return {"cache_name": cache_name, "fetched_at": stamp(moment), "cache": fields}


LISTINGS = ("etecnic_public_chargers.json", "zunder_location_map.html", "madrid_chargers.json", "zaragoza_chargers.json")


def build(records: list, moment: datetime = T0, **extra) -> dict:
    """A build whose listings were fetched, unless `extra` gives other fetch evidence."""
    evidence = {"artifacts": [listing(name, moment) for name in LISTINGS]}
    return {"generated_at": stamp(moment), "records": records, "fetch_evidence": evidence, **extra}


def clean_steps(payload: dict, start: datetime, end: datetime, rows_now: list) -> dict:
    """Publish a clean build every 24 hours, so the proof of absence stays continuous."""
    moment = start
    while moment < end:
        moment = min(moment + timedelta(hours=24), end)
        payload = apply_retention(payload, build(rows_now, moment))[0]
    return payload


def fallback(cache_name: str = "etecnic_public_chargers.json", moment: datetime = T0) -> dict:
    """Fetch evidence where one file was served from a cached copy."""
    files = [listing(name, moment) for name in LISTINGS if name != cache_name]
    return {"fetch_evidence": {"artifacts": files + [listing(cache_name, moment, fallback=True)]}}


def gate(baseline: dict, rebuilt: dict) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        base, cand = Path(tmp) / "baseline.json", Path(tmp) / "rebuilt.json"
        base.write_text(json.dumps(baseline), encoding="utf-8")
        cand.write_text(json.dumps(rebuilt), encoding="utf-8")
        run = subprocess.run(
            [sys.executable, "scripts/qa_ingestion_delta.py", "--baseline", str(base), "--rebuilt", str(cand)],
            cwd=ROOT, text=True, capture_output=True, check=False)
    return run.returncode, json.loads(run.stdout)


class RetentionTests(unittest.TestCase):
    def test_missing_row_is_kept_beside_the_records_and_passes_the_gate(self) -> None:
        baseline = build(rows(3), T0 - timedelta(hours=6))
        rebuilt, report = apply_retention(baseline, build(rows(2)))
        self.assertEqual([row["id"] for row in rebuilt["records"]], ["s0", "s1"])
        kept = rebuilt[RETAINED_KEY][0]
        self.assertEqual(kept["id"], "s2")
        self.assertEqual(kept[MARKER], {"source": SOURCE, "last_listed_at": baseline["generated_at"],
                                        "first_absent_at": rebuilt["generated_at"],
                                        "clock_since": rebuilt["generated_at"]})
        self.assertEqual(report["new"], ["s2"])
        code, result = gate(baseline, rebuilt)
        self.assertEqual(code, 0, result["failures"])
        self.assertEqual(result["delta"]["missing_ids"], 0)
        self.assertEqual(result["rebuilt"]["records"], 2)
        self.assertEqual(result["station_retention"]["new"], 1)

    def test_a_clean_build_changes_nothing(self) -> None:
        baseline, candidate = build(rows(2), T0 - timedelta(hours=6)), build(rows(2))
        rebuilt, report = apply_retention(baseline, candidate)
        self.assertEqual(rebuilt, candidate)
        self.assertFalse(report["changed"])

    def test_retained_row_stays_across_builds_and_keeps_its_first_absence(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        second, _ = apply_retention(first, build(rows(2), T0 + timedelta(hours=6)))
        self.assertEqual(second[RETAINED_KEY], first[RETAINED_KEY])
        self.assertEqual(gate(first, second)[0], 0)

    def test_returned_row_clears_the_marker_and_restarts_the_clock(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        back, report = apply_retention(first, build(rows(3), T0 + timedelta(hours=6)))
        self.assertNotIn(RETAINED_KEY, back)
        self.assertEqual(report["returned"], ["s2"])
        self.assertEqual(gate(first, back)[0], 0)
        again, _ = apply_retention(back, build(rows(2), T0 + timedelta(hours=12)))
        self.assertEqual(again[RETAINED_KEY][0][MARKER]["first_absent_at"], stamp(T0 + timedelta(hours=12)))

    def test_row_back_with_a_new_coordinate_id_regains_its_old_id(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        moved = rows(2) + [station("s2_moved", post="2", lat=42.6001)]
        payload, report = preserve_record_ids(identity_baseline(first), build(moved, T0 + timedelta(hours=6)))
        self.assertEqual(report["preserved_ids"], 1)
        rebuilt, _ = apply_retention(first, payload)
        self.assertNotIn(RETAINED_KEY, rebuilt)
        self.assertEqual({row["id"] for row in rebuilt["records"]}, {"s0", "s1", "s2"})
        self.assertEqual(gate(first, rebuilt)[0], 0)

    def test_row_retires_at_the_end_of_the_grace_period_not_before(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        early = clean_steps(first, T0, T0 + timedelta(days=GRACE_DAYS, seconds=-1), rows(2))
        self.assertEqual(len(early[RETAINED_KEY]), 1)
        self.assertNotIn(RETIRED_KEY, early)
        moment = T0 + timedelta(days=GRACE_DAYS)
        final, report = apply_retention(early, build(rows(2), moment))
        self.assertEqual(report["auto_retired"], ["s2"])
        self.assertNotIn(RETAINED_KEY, final)
        self.assertEqual(final[RETIRED_KEY], [{**first[RETAINED_KEY][0][MARKER], "id": "s2", "retired_at": stamp(moment)}])
        code, result = gate(early, final)
        self.assertEqual(code, 0, result["failures"])
        self.assertEqual(result["delta"]["retired_ids"], 1)
        self.assertEqual(result["delta"]["missing_ids"], 0)

    def test_retirement_record_survives_later_builds_and_clears_when_the_row_returns(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        final = clean_steps(first, T0, T0 + timedelta(days=GRACE_DAYS), rows(2))
        later, _ = apply_retention(final, build(rows(2), T0 + timedelta(days=GRACE_DAYS + 1)))
        self.assertEqual(later[RETIRED_KEY], final[RETIRED_KEY])
        self.assertEqual(gate(final, later)[0], 0)
        back, _ = apply_retention(later, build(rows(3), T0 + timedelta(days=GRACE_DAYS + 2)))
        self.assertNotIn(RETIRED_KEY, back)
        self.assertEqual(gate(later, back)[0], 0)
        dropped = {key: value for key, value in later.items() if key != RETIRED_KEY}
        self.assertEqual(gate(later, dropped)[0], 1)

    def test_retirements_in_one_build_stop_at_the_cap_and_the_rest_wait(self) -> None:
        self.assertEqual(per_run_cap(200), 5)
        first, _ = apply_retention(build(rows(200), T0 - timedelta(hours=6)), build(rows(195)))
        second, _ = apply_retention(first, build(rows(190), T0 + timedelta(hours=1)))
        almost = clean_steps(second, T0 + timedelta(hours=1), T0 + timedelta(days=GRACE_DAYS), rows(190))
        final, report = apply_retention(almost, build(rows(190), T0 + timedelta(days=GRACE_DAYS + 1)))
        self.assertEqual(len(almost[RETIRED_KEY]), 5)
        self.assertEqual(report["retained"], 0)
        self.assertEqual(len(final[RETIRED_KEY]), 10)
        self.assertEqual(gate(almost, final)[0], 0)

    def test_source_failure_stops_retirement_and_the_gate_rejects_the_run(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        almost = clean_steps(first, T0, T0 + timedelta(days=GRACE_DAYS - 1), rows(2))
        bad = build(rows(2), T0 + timedelta(days=GRACE_DAYS), errors=[{"source": "source_etecnic", "error": "x"}])
        rebuilt, report = apply_retention(almost, bad)
        self.assertEqual(report["auto_retired"], [])
        self.assertEqual(rebuilt[RETAINED_KEY][0]["id"], "s2")
        self.assertEqual(gate(almost, rebuilt)[0], 1)

    def test_cached_fallback_proves_nothing_so_the_row_stays_missing(self) -> None:
        baseline = build(rows(3), T0 - timedelta(hours=6))
        rebuilt, report = apply_retention(baseline, build(rows(2), **fallback()))
        self.assertNotIn(RETAINED_KEY, rebuilt)
        self.assertIn("etecnic", report["unproven_sources"])
        result = gate(baseline, rebuilt)[1]
        self.assertEqual(result["delta"]["missing_ids"], 1)  # the refresh requires exactly zero

    def test_another_source_falling_back_does_not_stop_retention(self) -> None:
        baseline = build(rows(3), T0 - timedelta(hours=6))
        rebuilt, _ = apply_retention(baseline, build(rows(2), **fallback("zunder_location_map.html")))
        self.assertEqual(len(rebuilt[RETAINED_KEY]), 1)

    def test_a_build_without_a_usable_time_retains_nothing(self) -> None:
        candidate = {"generated_at": "fixture", "records": rows(2)}
        rebuilt, report = apply_retention(build(rows(3)), candidate)
        self.assertEqual(rebuilt, candidate)
        self.assertIn("skipped", report)

    def test_reviewed_retirement_wins_over_retention(self) -> None:
        reviewed = {"s2": {"id": "s2"}}
        rebuilt, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)), retirements=reviewed)
        self.assertNotIn(RETAINED_KEY, rebuilt)

    def test_retirement_with_an_absent_successor_retires_nothing(self) -> None:
        reviewed = {"s2": {"id": "s2", "successor_id": "gone"}}
        rebuilt, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)), retirements=reviewed)
        self.assertEqual(len(rebuilt[RETAINED_KEY]), 1)


class GateProofTests(unittest.TestCase):
    def good(self) -> tuple[dict, dict]:
        baseline = build(rows(3), T0 - timedelta(hours=6))
        return baseline, apply_retention(baseline, build(rows(2)))[0]

    def assert_rejected(self, baseline: dict, rebuilt: dict, text: str) -> None:
        code, result = gate(baseline, rebuilt)
        self.assertEqual(code, 1)
        self.assertTrue(any(text in failure for failure in result["failures"]), result["failures"])

    def test_edited_row_is_rejected(self) -> None:
        baseline, rebuilt = self.good()
        rebuilt[RETAINED_KEY][0]["name"] = "Edited"
        self.assert_rejected(baseline, rebuilt, "differs from its baseline")

    def test_carried_row_cannot_change(self) -> None:
        baseline, first = self.good()
        second = deepcopy(apply_retention(first, build(rows(2), T0 + timedelta(hours=6)))[0])
        second[RETAINED_KEY][0][MARKER]["clock_since"] = stamp(T0 - timedelta(days=99))
        self.assert_rejected(first, second, "changed since the baseline")

    def test_row_that_was_never_in_the_baseline_is_rejected(self) -> None:
        baseline, rebuilt = self.good()
        rebuilt[RETAINED_KEY].append({**station("invented"), MARKER: dict(rebuilt[RETAINED_KEY][0][MARKER])})
        self.assert_rejected(baseline, rebuilt, "not in the baseline")

    def test_row_also_listed_in_records_is_rejected(self) -> None:
        baseline, rebuilt = self.good()
        rebuilt["records"].append(station("s2", post="2"))
        self.assert_rejected(baseline, rebuilt, "duplicates a station")

    def test_backdated_marker_is_rejected(self) -> None:
        baseline, rebuilt = self.good()
        rebuilt[RETAINED_KEY][0][MARKER]["clock_since"] = stamp(T0 - timedelta(days=99))
        self.assert_rejected(baseline, rebuilt, "unproven marker")

    def test_retained_row_with_a_failed_source_is_rejected(self) -> None:
        baseline, rebuilt = self.good()
        rebuilt.update(fallback())
        self.assert_rejected(baseline, rebuilt, "absence is not proven")

    def test_too_many_leaving_in_one_build_is_rejected(self) -> None:
        baseline = build(rows(1000), T0 - timedelta(hours=6))
        rebuilt = apply_retention(baseline, build(rows(989)))[0]
        self.assertEqual(per_run_cap(1000), 10)
        self.assert_rejected(baseline, rebuilt, "above the cap 10")
        ok = apply_retention(baseline, build(rows(990)))[0]
        self.assertEqual(gate(baseline, ok)[0], 0)

    def test_too_many_retained_in_one_source_is_rejected(self) -> None:
        marker = {"source": SOURCE, "last_listed_at": stamp(T0), "first_absent_at": stamp(T0), "clock_since": stamp(T0)}
        held = [{**station(f"h{index}", post=f"h{index}"), MARKER: marker} for index in range(11)]
        baseline = build(rows(90), T0, **{RETAINED_KEY: held})
        self.assertEqual(retained_cap(101), 11)
        next_build = T0 + timedelta(hours=6)
        self.assertEqual(gate(baseline, apply_retention(baseline, build(rows(90), next_build))[0])[0], 0)
        rebuilt = apply_retention(baseline, build(rows(89), next_build))[0]
        self.assert_rejected(baseline, rebuilt, "retained stations, above the cap")

    def test_early_retirement_is_rejected(self) -> None:
        first = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))[0]
        moment = T0 + timedelta(days=GRACE_DAYS - 1)
        forged = build(rows(2), moment, **{RETIRED_KEY: [
            {**first[RETAINED_KEY][0][MARKER], "id": "s2", "retired_at": stamp(moment)}]})
        forged = {**forged, "generated_at": stamp(moment)}
        self.assert_rejected(first, forged, "automatic retirement is not proven")
        self.assertEqual(gate(first, forged)[1]["delta"]["missing_ids"], 1)

    def test_retained_row_silently_dropped_is_missing(self) -> None:
        first = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))[0]
        result = gate(first, build(rows(2), T0 + timedelta(hours=6)))[1]
        self.assertEqual(result["delta"]["missing_ids"], 1)  # the refresh requires exactly zero

    def test_retained_rows_cannot_hide_a_whole_source_loss(self) -> None:
        other = station("other", "Zunder public location map", post="9")
        baseline = build(rows(3) + [other], T0 - timedelta(hours=6))
        rebuilt = apply_retention(baseline, build([other]))[0]
        self.assertEqual(len(rebuilt[RETAINED_KEY]), 3)
        self.assert_rejected(baseline, rebuilt, "lost entire source families")

    def test_retained_rows_cannot_hold_up_the_total_floor(self) -> None:
        marker = {"source": SOURCE}
        zunder = [station(f"z{index}", "Zunder public location map", post=f"z{index}") for index in range(4)]
        baseline = build(rows(8) + zunder, T0 - timedelta(hours=6))
        rebuilt = apply_retention(baseline, build(rows(4) + zunder))[0]
        self.assertEqual(len(rebuilt[RETAINED_KEY]), 4)
        self.assertEqual(rebuilt[RETAINED_KEY][0][MARKER]["source"], marker["source"])
        self.assert_rejected(baseline, rebuilt, "below minimum")

    def test_alias_on_a_retained_row_survives_and_retires_with_it(self) -> None:
        holder = station("s2", post="2", merged_ids=["old-alias"])
        baseline = build(rows(2) + [holder], T0 - timedelta(hours=6))
        first = apply_retention(baseline, build(rows(2)))[0]
        code, result = gate(baseline, first)
        self.assertEqual(code, 0, result["failures"])
        self.assertEqual(result["delta"]["missing_aliases"], 0)
        almost = clean_steps(first, T0, T0 + timedelta(days=GRACE_DAYS - 1), rows(2))
        final = apply_retention(almost, build(rows(2), T0 + timedelta(days=GRACE_DAYS)))[0]
        self.assertEqual(final[RETIRED_KEY][0]["aliases"], ["old-alias"])
        code, result = gate(almost, final)
        self.assertEqual(code, 0, result["failures"])
        self.assertEqual(result["delta"]["missing_aliases"], 0)


class ProofOfAbsenceTests(unittest.TestCase):
    OSM = "OpenStreetMap public tags"

    def osm_row(self, row_id: str, lat: float) -> dict:
        url = f"https://www.openstreetmap.org/node/{abs(hash(row_id)) % 10**9}"
        return station(row_id, self.OSM, lat=lat, lon=2.7, source_url=url, sources=[{"name": self.OSM, "url": url}])

    def osm_evidence(self, moment: datetime) -> dict:
        tile = {"cache_name": "osm_es_area_chargers_23.json", "fetched_at": stamp(moment), "cache": {"fallback": True},
                "url": "https://overpass.example/?data=" + "node(area.spain)(39.0000,2.0000,40.0000,3.0000)"}
        healthy = listing("osm_es_area_chargers_00.json", moment)
        return {"fetch_evidence": {"artifacts": [tile, healthy, *[listing(name, moment) for name in LISTINGS]]}}

    def test_failed_tile_proves_nothing_only_near_its_own_area(self) -> None:
        inside, outside = self.osm_row("inside", 39.5), self.osm_row("outside", 41.5)
        keep = [self.osm_row(f"k{index}", 42.0 + index / 100) for index in range(800)]
        baseline = build(keep + [inside, outside], T0 - timedelta(hours=6))
        rebuilt, report = apply_retention(baseline, build(keep, T0, **self.osm_evidence(T0)))
        self.assertEqual([row["id"] for row in rebuilt[RETAINED_KEY]], ["outside"])
        self.assertNotIn("osm", report["unproven_sources"])
        self.assertEqual(report["failed_areas"], 1)
        self.assertEqual(gate(baseline, rebuilt)[1]["delta"]["missing_ids"], 1)  # inside: the refresh requires zero

    def test_failed_tile_with_no_stated_area_fails_the_whole_source(self) -> None:
        evidence = self.osm_evidence(T0)
        evidence["fetch_evidence"]["artifacts"][0].pop("url")
        keep = [self.osm_row(f"k{index}", 42.0 + index / 100) for index in range(800)]
        baseline = build(keep + [self.osm_row("outside", 41.5)], T0 - timedelta(hours=6))
        rebuilt, report = apply_retention(baseline, build(keep, T0, **evidence))
        self.assertNotIn(RETAINED_KEY, rebuilt)
        self.assertIn("osm", report["unproven_sources"])

    def test_failed_detail_pages_are_not_a_failed_listing(self) -> None:
        baseline = build(rows(3, "Zunder public location map"), T0 - timedelta(hours=6))
        moment = T0
        details = {"fetch_evidence": {"artifacts": [listing("zunder_location_map.html", moment),
                                                    listing("zunder_location_17.json", moment, fallback=True)]}}
        rebuilt, _ = apply_retention(baseline, build(rows(2, "Zunder public location map"), moment, **details))
        self.assertEqual(len(rebuilt[RETAINED_KEY]), 1)
        canarias = "Canarias te recarga public map"
        base = build(rows(30, canarias), T0 - timedelta(hours=6))
        pages = {"fetch_evidence": {"artifacts": [listing("canarias_tecarga_map.json", moment),
                                                  listing("canarias_details/abc.html", moment, fallback=True)]}}
        rebuilt, report = apply_retention(base, build(rows(29, canarias), moment, **pages))
        self.assertEqual(len(rebuilt[RETAINED_KEY]), 1)
        self.assertNotIn("canarias", report["unproven_sources"])

    def test_missing_listing_page_fails_even_with_a_detail_page(self) -> None:
        baseline = build(rows(3, "Zunder public location map"), T0 - timedelta(hours=6))
        only_detail = {"fetch_evidence": {"artifacts": [listing("zunder_location_17.json")]}}
        rebuilt, _ = apply_retention(baseline, build(rows(2, "Zunder public location map"), T0, **only_detail))
        self.assertNotIn(RETAINED_KEY, rebuilt)

    def test_no_fetch_evidence_proves_nothing(self) -> None:
        baseline = build(rows(3), T0 - timedelta(hours=6))
        rebuilt, _ = apply_retention(baseline, {**build(rows(2)), "fetch_evidence": {}})
        self.assertNotIn(RETAINED_KEY, rebuilt)

    def test_every_source_on_a_row_must_be_proven(self) -> None:
        both = station("s2", post="2", sources=[{"name": SOURCE, "url": "https://x.example"},
                                                {"name": "Zunder public location map", "url": "https://y.example"}])
        baseline = build(rows(2) + [both], T0 - timedelta(hours=6))
        rebuilt, _ = apply_retention(baseline, build(rows(2), **fallback("zunder_location_map.html")))
        self.assertNotIn(RETAINED_KEY, rebuilt)

    def test_unknown_source_proves_nothing(self) -> None:
        baseline = build(rows(2) + [station("s2", "A source with no column", post="2")], T0 - timedelta(hours=6))
        rebuilt, _ = apply_retention(baseline, build(rows(2)))
        self.assertNotIn(RETAINED_KEY, rebuilt)


class OverpassRemarkTests(unittest.TestCase):
    def fetch(self, replies: list) -> tuple:
        import ingest_sources as ingest
        answers = iter(replies)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(ingest, "CACHE", Path(tmp)), mock.patch.object(ingest, "OVERPASS_MAX_SUBDIVISION", 0), \
             mock.patch.object(ingest, "OVERPASS_TILE_PAUSE_SECONDS", 0), mock.patch.object(ingest, "OVERPASS_BACKOFF_SECONDS", 0), \
             mock.patch.object(ingest, "cached_overpass", side_effect=lambda *a, **k: next(answers)):
            cached = Path(tmp) / "osm_es_area_chargers_00.json"
            cached.write_text("partial")
            found, failed = ingest.osm_fetch_box(39.0, 2.0, 40.0, 3.0, "00")
            return found, failed, cached.exists()

    def test_remark_means_a_failed_tile_not_proven_absence(self) -> None:
        partial = json.dumps({"remark": "runtime error: Query timed out", "elements": [{"id": 1}]}).encode()
        found, failed, cached = self.fetch([partial, partial, partial])
        self.assertEqual(found, [])
        self.assertIn("Overpass remark", failed[0])
        self.assertFalse(cached)

    def test_retry_after_a_remark_can_succeed(self) -> None:
        partial = json.dumps({"remark": "runtime error", "elements": []}).encode()
        whole = json.dumps({"elements": [{"id": 1}]}).encode()
        found, failed, _ = self.fetch([partial, whole])
        self.assertEqual(failed, [])
        self.assertEqual(found[0][0]["id"], 1)


class ClockTests(unittest.TestCase):
    def test_unproven_build_restarts_the_clock_and_cannot_retire(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        almost = clean_steps(first, T0, T0 + timedelta(days=29), rows(2))
        moment = T0 + timedelta(days=29, hours=12)
        unproven, _ = apply_retention(almost, build(rows(2), moment, **fallback(moment=moment)))
        marker = unproven[RETAINED_KEY][0][MARKER]
        self.assertEqual(marker["clock_since"], stamp(moment))
        self.assertEqual(marker["first_absent_at"], stamp(T0))
        self.assertEqual(gate(almost, unproven)[0], 0)
        clean, report = apply_retention(unproven, build(rows(2), T0 + timedelta(days=30)))
        self.assertEqual(report["auto_retired"], [])
        self.assertEqual(clean[RETAINED_KEY][0][MARKER]["clock_since"], stamp(moment))
        self.assertEqual(gate(unproven, clean)[0], 0)

    def test_gate_rejects_a_build_that_keeps_the_clock_through_an_unproven_build(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        moment = T0 + timedelta(hours=6)
        forged = deepcopy(apply_retention(first, build(rows(2), moment, **fallback(moment=moment)))[0])
        forged[RETAINED_KEY][0][MARKER]["clock_since"] = stamp(T0)
        code, result = gate(first, forged)
        self.assertEqual(code, 1)
        self.assertTrue(any("changed since the baseline" in failure for failure in result["failures"]))

    def test_long_gap_between_published_builds_restarts_the_clock(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        moment = T0 + timedelta(days=30)
        after_gap, report = apply_retention(first, build(rows(2), moment))
        self.assertEqual(report["auto_retired"], [])
        self.assertEqual(after_gap[RETAINED_KEY][0][MARKER]["clock_since"], stamp(moment))
        self.assertEqual(gate(first, after_gap)[0], 0)

    def test_retirement_right_after_a_gap_is_rejected(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        moment = T0 + timedelta(days=30)
        forged = build(rows(2), moment, **{RETIRED_KEY: [{**first[RETAINED_KEY][0][MARKER], "id": "s2", "retired_at": stamp(moment)}]})
        code, result = gate(first, forged)
        self.assertEqual(code, 1)
        self.assertTrue(any("automatic retirement is not proven" in failure for failure in result["failures"]))

    def test_future_build_time_is_rejected(self) -> None:
        baseline = build(rows(2), T0)
        future = datetime.now(timezone.utc) + timedelta(days=400)
        code, result = gate(baseline, build(rows(2), future))
        self.assertEqual(code, 1)
        self.assertTrue(any("in the future" in failure for failure in result["failures"]))

    def test_build_older_than_the_baseline_cannot_carry_retention(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        older = {**first, "generated_at": stamp(T0 - timedelta(days=1))}
        code, result = gate(first, older)
        self.assertEqual(code, 1)
        self.assertTrue(any("before the baseline" in failure for failure in result["failures"]))


class AliasReturnTests(unittest.TestCase):
    def test_retired_id_that_returns_as_a_proven_alias_does_not_block(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        retired = clean_steps(first, T0, T0 + timedelta(days=GRACE_DAYS), rows(2))
        self.assertEqual(retired[RETIRED_KEY][0]["id"], "s2")
        moment = T0 + timedelta(days=GRACE_DAYS + 1)
        holder = rows(2)
        holder[0] = {**holder[0], "merged_ids": ["s2"]}
        back, _ = apply_retention(retired, build(holder, moment))
        self.assertNotIn(RETIRED_KEY, back)
        code, result = gate(retired, back)
        self.assertEqual(code, 0, result["failures"])


class KeylessRenameTests(unittest.TestCase):
    MADRID = "Madrid public charger open data"

    def row(self, row_id: str, lat: float = 40.4, lon: float = -3.7) -> dict:
        return station(row_id, self.MADRID, lat=lat, lon=lon, source_url="https://datos.madrid.es/x",
                       sources=[{"name": self.MADRID, "url": "https://datos.madrid.es/x"}], raw={})

    def others(self, count: int = 40) -> list:
        return [self.row(f"o{index}", lat=41.0 + index) for index in range(count)]

    def test_new_row_four_metres_away_keeps_the_old_id_as_an_alias(self) -> None:
        old = {**self.row("old"), "merged_ids": ["older"]}
        baseline = build(self.others() + [old], T0 - timedelta(hours=6))
        renamed = self.row("renamed", lat=40.40003)
        rebuilt, report = apply_retention(baseline, build(self.others() + [renamed]))
        self.assertNotIn(RETAINED_KEY, rebuilt)
        self.assertEqual(report["keyless_renames"], {"old": "renamed"})
        holder = next(row for row in rebuilt["records"] if row["id"] == "renamed")
        self.assertEqual(holder["merged_ids"], ["old", "older"])
        code, result = gate(baseline, rebuilt)
        self.assertEqual(code, 0, result["failures"])
        self.assertEqual(result["delta"]["missing_ids"], 0)
        self.assertEqual(result["delta"]["merged_ids"], 1)

    def test_row_beyond_the_radius_is_not_a_rename(self) -> None:
        baseline = build(self.others() + [self.row("old")], T0 - timedelta(hours=6))
        rebuilt, report = apply_retention(baseline, build(self.others() + [self.row("far", lat=40.401)]))
        self.assertEqual(report["keyless_renames"], {})
        self.assertEqual(rebuilt[RETAINED_KEY][0]["id"], "old")

    def test_other_source_nearby_is_not_a_rename(self) -> None:
        other = station("zaragoza", "Zaragoza municipal open data", lat=40.40003, lon=-3.7, raw={})
        baseline = build(self.others() + [self.row("old")], T0 - timedelta(hours=6))
        _, report = apply_retention(baseline, build(self.others() + [other]))
        self.assertEqual(report["keyless_renames"], {})

    def test_two_candidates_are_ambiguous_so_the_row_is_kept(self) -> None:
        baseline = build(self.others() + [self.row("old")], T0 - timedelta(hours=6))
        new = [self.row("a", lat=40.40003), self.row("b", lat=40.39997)]
        rebuilt, report = apply_retention(baseline, build(self.others() + new))
        self.assertEqual(report["keyless_renames"], {})
        self.assertEqual(rebuilt[RETAINED_KEY][0]["id"], "old")

    def test_two_missing_rows_for_one_candidate_are_ambiguous(self) -> None:
        baseline = build(self.others() + [self.row("old1"), self.row("old2", lat=40.40001)], T0 - timedelta(hours=6))
        rebuilt, report = apply_retention(baseline, build(self.others() + [self.row("new", lat=40.40003)]))
        self.assertEqual(report["keyless_renames"], {})
        self.assertEqual(len(rebuilt[RETAINED_KEY]), 2)

    def test_row_with_a_keyed_neighbour_source_is_not_a_rename(self) -> None:
        keyed = station("osm", "OpenStreetMap public tags", lat=40.40003, lon=-3.7,
                        source_url="https://www.openstreetmap.org/node/1",
                        sources=[{"name": "OpenStreetMap public tags", "url": "https://www.openstreetmap.org/node/1"}])
        old = {**keyed, "id": "old-osm", "source_url": "https://www.openstreetmap.org/node/2",
               "sources": [{"name": "OpenStreetMap public tags", "url": "https://www.openstreetmap.org/node/2"}]}
        baseline = build(self.others() + [old], T0 - timedelta(hours=6))
        _, report = apply_retention(baseline, build(self.others() + [keyed]))
        self.assertEqual(report["keyless_renames"], {})

    def test_retained_keyless_row_later_renamed_becomes_an_alias(self) -> None:
        first = apply_retention(build(self.others() + [self.row("old")], T0 - timedelta(hours=6)), build(self.others()))[0]
        self.assertEqual(first[RETAINED_KEY][0]["id"], "old")
        moment = T0 + timedelta(hours=6)
        rebuilt, report = apply_retention(first, build(self.others() + [self.row("renamed", lat=40.40003)], moment))
        self.assertNotIn(RETAINED_KEY, rebuilt)
        self.assertEqual(report["keyless_renames"], {"old": "renamed"})
        code, result = gate(first, rebuilt)
        self.assertEqual(code, 0, result["failures"])

    def test_neighbour_stops_automatic_retirement_and_asks_for_review(self) -> None:
        neighbour = self.row("neighbour", lat=40.40003)
        rows_now = self.others() + [neighbour]
        first = apply_retention(build(rows_now + [self.row("old")], T0 - timedelta(hours=6)), build(rows_now))[0]
        almost = clean_steps(first, T0, T0 + timedelta(days=GRACE_DAYS - 1), rows_now)
        final, report = apply_retention(almost, build(rows_now, T0 + timedelta(days=GRACE_DAYS)))
        self.assertEqual(report["auto_retired"], [])
        self.assertEqual(report["needs_review"], ["old"])
        self.assertEqual(final[RETAINED_KEY][0]["id"], "old")
        self.assertEqual(gate(almost, final)[0], 0)
        moment = T0 + timedelta(days=GRACE_DAYS)
        forged = {**build(rows_now, moment), RETIRED_KEY: [
            {**almost[RETAINED_KEY][0][MARKER], "id": "old", "retired_at": stamp(moment)}]}
        self.assertEqual(gate(almost, forged)[0], 1)

    def test_lone_keyless_row_retires_on_time(self) -> None:
        first = apply_retention(build(self.others() + [self.row("old")], T0 - timedelta(hours=6)), build(self.others()))[0]
        almost = clean_steps(first, T0, T0 + timedelta(days=GRACE_DAYS - 1), self.others())
        final, report = apply_retention(almost, build(self.others(), T0 + timedelta(days=GRACE_DAYS)))
        self.assertEqual(report["auto_retired"], ["old"])
        self.assertEqual(gate(almost, final)[0], 0)


class CapTests(unittest.TestCase):
    def test_small_sources_keep_a_percentage_limit(self) -> None:
        self.assertEqual([per_run_cap(n) for n in (3, 16, 50, 100, 1000, 1666)], [1, 2, 5, 5, 10, 17])
        self.assertEqual([retained_cap(n) for n in (3, 16, 50, 100, 1666)], [1, 4, 5, 10, 167])

    def test_a_third_of_a_small_source_cannot_leave_at_once(self) -> None:
        zaragoza = "Zaragoza municipal open data"
        baseline = build(rows(16, zaragoza), T0 - timedelta(hours=6))
        rebuilt = apply_retention(baseline, build(rows(11, zaragoza)))[0]
        code, result = gate(baseline, rebuilt)
        self.assertEqual(code, 1)
        self.assertTrue(any("above the cap 2" in failure for failure in result["failures"]))
        ok = apply_retention(baseline, build(rows(14, zaragoza)))[0]
        self.assertEqual(gate(baseline, ok)[0], 0)


class ServingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.row = {**station("gone", is_free=True, is_cheap=True, status="AVAILABLE", available=2, merged_ids=["alias"],
                              current_price_min=0.1, price_min=0.0, price_label="Free", free_condition="Free for all",
                              charge_claim={"summary": "Free", "source": "x", "url": "https://x.example"}),
                    MARKER: {"source": SOURCE, "last_listed_at": "2026-11-04T12:37:55Z",
                             "first_absent_at": "2026-11-04T18:34:24Z", "clock_since": "2026-11-04T18:34:24Z"}}
        self.meta = {"generated_at": stamp(T0), "records": [station("listed")], RETAINED_KEY: [deepcopy(self.row)],
                     RETIRED_KEY: [{"id": "old", "source": SOURCE, "last_listed_at": "2026-10-01T00:00:00Z",
                                    "first_absent_at": "2026-10-01T06:00:00Z", "retired_at": "2026-10-31T06:00:00Z",
                                    "aliases": ["old-alias"]}]}
        patcher = mock.patch.object(server, "load_intelligence_meta", return_value=self.meta)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lookup_finds_by_id_and_alias_and_serves_no_current_claim(self) -> None:
        for identifier in ("gone", "alias"):
            found = server.retained_station(identifier)
            self.assertEqual(found["id"], "gone")
            self.assertEqual((found["status"], found["available"], found["is_free"], found["is_cheap"]),
                             ("VERIFY", 0, False, False))
            self.assertNotIn("current_price_min", found)
        self.assertIsNone(server.retained_station("listed"))
        self.assertEqual(self.meta[RETAINED_KEY][0], self.row)

    def test_decision_is_a_dated_lead(self) -> None:
        decision = server.decision_for(server.retained_station("gone"), live=False, vehicle_neutral=True)
        self.assertEqual(decision["level"], "lead")
        self.assertEqual(decision["label"], "No longer listed")
        self.assertIn("2026-11-04", decision["reason"])
        self.assertIn(SOURCE, decision["reason"])

    def test_policy_excluded_row_is_not_served(self) -> None:
        with mock.patch.object(server, "partition_records", return_value=([], [self.row])):
            self.assertIsNone(server.retained_station("gone"))

    def test_community_may_still_post_to_a_retained_row(self) -> None:
        server._KNOWN_IDS_CACHE.update(ts=0.0, ids=set())
        self.addCleanup(server._KNOWN_IDS_CACHE.update, ts=0.0, ids=set())
        ids = server.known_station_ids()
        self.assertIn("gone", ids)
        self.assertIn("listed", ids)

    def test_listing_endpoint_answers_only_for_stations_a_source_dropped(self) -> None:
        status, payload = server.charger_listing_response({"ids": ["gone,alias,old,old-alias,listed,unknown"]})
        self.assertEqual(status, 200)
        self.assertEqual(set(payload["chargers"]), {"gone", "alias", "old", "old-alias"})
        self.assertEqual(payload["chargers"]["gone"], {"source": SOURCE, "last_listed_at": "2026-11-04T12:37:55Z"})
        self.assertEqual(payload["chargers"]["old"]["retired_at"], "2026-10-31T06:00:00Z")

    def test_listing_endpoint_bounds_its_input(self) -> None:
        self.assertEqual(server.charger_listing_response({})[0], 400)
        ids = ",".join(f"s{index}" for index in range(server.CHARGER_LISTING_MAX_IDS + 1))
        self.assertEqual(server.charger_listing_response({"ids": [ids]})[0], 400)

    def test_get_charger_returns_the_marked_row_without_a_live_stand_in(self) -> None:
        with mock.patch.object(server, "live_response", side_effect=AssertionError("no live lookup")):
            result = agent_mcp.get_charger({"id": "gone"}, server)
        self.assertEqual(result["structured"]["id"], "gone")
        self.assertEqual(result["structured"]["decision"]["level"], "lead")
        self.assertEqual(result["structured"]["no_longer_listed"]["last_listed_at"], "2026-11-04T12:37:55Z")

    def test_get_charger_gives_an_agent_no_price_to_quote(self) -> None:
        price = agent_mcp.get_charger({"id": "gone"}, server)["structured"]["price"]
        self.assertFalse(price["is_free"])
        self.assertIsNone(price["lowest_published_eur_kwh"])
        self.assertIsNone(price["price_now_eur_kwh"])
        self.assertIsNone(price["note"])
        self.assertIsNone(price["source"])
        self.assertNotIn("free", str(price["label"]).lower())

    def test_get_charger_echoes_no_old_source_claim(self) -> None:
        self.meta[RETAINED_KEY][0]["sources"][0]["evidence"] = "Free to use at all hours"
        result = agent_mcp.get_charger({"id": "gone"}, server)
        self.assertNotIn("free to use", json.dumps(result).lower())

    def test_intel_response_leaves_out_retained_rows_and_retirements(self) -> None:
        result = server.intelligence_response({"limit": ["1"]})
        self.assertEqual([row["id"] for row in result["records"]], ["listed"])
        self.assertNotIn(RETAINED_KEY, result)
        self.assertNotIn(RETIRED_KEY, result)

    def test_retained_row_is_not_on_the_map_or_in_the_matrix(self) -> None:
        from scripts import source_matrix
        bbox = {"latitude_sw": 42.5, "longitude_sw": -5.7, "latitude_ne": 42.7, "longitude_ne": -5.5}
        matrix = source_matrix.build_matrix(self.meta, bbox)
        self.assertEqual([location["id"] for location in matrix["locations"]], ["listed"])


if __name__ == "__main__":
    unittest.main()
