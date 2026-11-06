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

SOURCE = "Etecnic public charger map"
T0 = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


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


def build(records: list, moment: datetime = T0, **extra) -> dict:
    return {"generated_at": stamp(moment), "records": records, **extra}


def fallback(cache_name: str = "etecnic_public_chargers.json") -> dict:
    return {"fetch_evidence": {"artifacts": [{"cache_name": cache_name, "cache": {"fallback": True}}]}}


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
                                        "first_absent_at": rebuilt["generated_at"]})
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
        early, report = apply_retention(first, build(rows(2), T0 + timedelta(days=GRACE_DAYS, seconds=-1)))
        self.assertEqual(report["auto_retired"], [])
        self.assertEqual(len(early[RETAINED_KEY]), 1)
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
        final, _ = apply_retention(first, build(rows(2), T0 + timedelta(days=GRACE_DAYS)))
        later, _ = apply_retention(final, build(rows(2), T0 + timedelta(days=GRACE_DAYS + 1)))
        self.assertEqual(later[RETIRED_KEY], final[RETIRED_KEY])
        self.assertEqual(gate(final, later)[0], 0)
        back, _ = apply_retention(later, build(rows(3), T0 + timedelta(days=GRACE_DAYS + 2)))
        self.assertNotIn(RETIRED_KEY, back)
        self.assertEqual(gate(later, back)[0], 0)
        dropped = {key: value for key, value in later.items() if key != RETIRED_KEY}
        self.assertEqual(gate(later, dropped)[0], 1)

    def test_retirements_in_one_build_stop_at_the_cap_and_the_rest_wait(self) -> None:
        kept = rows(10)
        first, _ = apply_retention(build(rows(16), T0 - timedelta(hours=6)), build(kept))
        final, report = apply_retention(first, build(kept, T0 + timedelta(days=GRACE_DAYS)))
        self.assertEqual(len(report["auto_retired"]), per_run_cap(16))
        self.assertEqual(len(final[RETAINED_KEY]), 6 - per_run_cap(16))
        self.assertEqual(gate(first, final)[0], 0)

    def test_source_failure_stops_retirement_and_the_gate_rejects_the_run(self) -> None:
        first, _ = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))
        bad = build(rows(2), T0 + timedelta(days=GRACE_DAYS), errors=[{"source": "source_etecnic", "error": "x"}])
        rebuilt, report = apply_retention(first, bad)
        self.assertEqual(report["auto_retired"], [])
        self.assertEqual(rebuilt[RETAINED_KEY], first[RETAINED_KEY])
        self.assertEqual(gate(first, rebuilt)[0], 1)

    def test_cached_fallback_proves_nothing_so_the_row_stays_missing(self) -> None:
        baseline = build(rows(3), T0 - timedelta(hours=6))
        rebuilt, report = apply_retention(baseline, build(rows(2), **fallback()))
        self.assertNotIn(RETAINED_KEY, rebuilt)
        self.assertEqual(report["unproven_sources"], ["etecnic"])
        result = gate(baseline, rebuilt)[1]
        self.assertEqual(result["delta"]["missing_ids"], 1)  # the refresh requires exactly zero

    def test_another_source_falling_back_does_not_stop_retention(self) -> None:
        baseline = build(rows(3), T0 - timedelta(hours=6))
        rebuilt, _ = apply_retention(baseline, build(rows(2), **fallback("zunder_map.html")))
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
        second[RETAINED_KEY][0][MARKER]["first_absent_at"] = stamp(T0 - timedelta(days=99))
        self.assert_rejected(first, second, "changed since the baseline")

    def test_row_that_was_never_in_the_baseline_is_rejected(self) -> None:
        baseline, rebuilt = self.good()
        rebuilt[RETAINED_KEY].append({**station("invented"), MARKER: rebuilt[RETAINED_KEY][0][MARKER]})
        self.assert_rejected(baseline, rebuilt, "not in the baseline")

    def test_row_also_listed_in_records_is_rejected(self) -> None:
        baseline, rebuilt = self.good()
        rebuilt["records"].append(station("s2", post="2"))
        self.assert_rejected(baseline, rebuilt, "duplicates a station")

    def test_backdated_marker_is_rejected(self) -> None:
        baseline, rebuilt = self.good()
        rebuilt[RETAINED_KEY][0][MARKER]["first_absent_at"] = stamp(T0 - timedelta(days=99))
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
        marker = {"source": SOURCE, "last_listed_at": stamp(T0), "first_absent_at": stamp(T0)}
        held = [{**station(f"h{index}", post=f"h{index}"), MARKER: marker} for index in range(11)]
        baseline = build(rows(90), T0 - timedelta(hours=6), **{RETAINED_KEY: held})
        self.assertEqual(retained_cap(101), 11)
        self.assertEqual(gate(baseline, apply_retention(baseline, build(rows(90)))[0])[0], 0)
        rebuilt = apply_retention(baseline, build(rows(89)))[0]
        self.assert_rejected(baseline, rebuilt, "retained stations, above the cap")

    def test_early_retirement_is_rejected(self) -> None:
        first = apply_retention(build(rows(3), T0 - timedelta(hours=6)), build(rows(2)))[0]
        moment = T0 + timedelta(days=GRACE_DAYS - 1)
        forged = build(rows(2), moment, **{RETIRED_KEY: [
            {**first[RETAINED_KEY][0][MARKER], "id": "s2", "retired_at": stamp(moment)}]})
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
        marker = {"source": SOURCE, "last_listed_at": stamp(T0), "first_absent_at": stamp(T0)}
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
        final = apply_retention(first, build(rows(2), T0 + timedelta(days=GRACE_DAYS)))[0]
        code, result = gate(first, final)
        self.assertEqual(code, 0, result["failures"])
        self.assertEqual(result["delta"]["missing_aliases"], 0)


class ServingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.row = {**station("gone", is_free=True, is_cheap=True, status="AVAILABLE", available=2, merged_ids=["alias"],
                              current_price_min=0.1),
                    MARKER: {"source": SOURCE, "last_listed_at": "2026-11-04T12:37:55Z",
                             "first_absent_at": "2026-11-04T18:34:24Z"}}
        self.meta = {"generated_at": stamp(T0), "records": [station("listed")], RETAINED_KEY: [deepcopy(self.row)],
                     RETIRED_KEY: [{"id": "old", "source": SOURCE, "last_listed_at": "2026-10-01T00:00:00Z",
                                    "first_absent_at": "2026-10-01T06:00:00Z", "retired_at": "2026-10-31T06:00:00Z"}]}
        patcher = mock.patch.object(server, "load_intelligence_meta", return_value=self.meta)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lookup_finds_by_id_and_alias_and_serves_no_current_claim(self) -> None:
        for identifier in ("gone", "alias"):
            found = server.retained_station(identifier)
            self.assertEqual(found["id"], "gone")
            self.assertEqual((found["status"], found["available"], found["is_free"], found["is_cheap"]),
                             ("VERIFY", 0, False, False))
            self.assertIsNone(found["current_price_min"])
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
        status, payload = server.charger_listing_response({"ids": ["gone,alias,old,listed,unknown"]})
        self.assertEqual(status, 200)
        self.assertEqual(set(payload["chargers"]), {"gone", "alias", "old"})
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

    def test_retained_row_is_not_on_the_map_or_in_the_matrix(self) -> None:
        from scripts import source_matrix
        bbox = {"latitude_sw": 42.5, "longitude_sw": -5.7, "latitude_ne": 42.7, "longitude_ne": -5.5}
        matrix = source_matrix.build_matrix(self.meta, bbox)
        self.assertEqual([location["id"] for location in matrix["locations"]], ["listed"])


if __name__ == "__main__":
    unittest.main()
