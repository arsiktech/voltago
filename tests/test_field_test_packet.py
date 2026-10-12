import importlib.util
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server  # noqa: E402
SPEC = importlib.util.spec_from_file_location("qa_field_test_packet", ROOT / "scripts" / "qa_field_test_packet.py")
qa_field_test_packet = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qa_field_test_packet)

FRESHNESS_SPEC = importlib.util.spec_from_file_location("qa_field_packet_freshness", ROOT / "scripts" / "qa_field_packet_freshness.py")
qa_field_packet_freshness = importlib.util.module_from_spec(FRESHNESS_SPEC)
assert FRESHNESS_SPEC.loader is not None
FRESHNESS_SPEC.loader.exec_module(qa_field_packet_freshness)


def station(name, *, station_id=None, lead=False, decision="verify", available=0, power=11, source_url=None, parking_kind="street_or_municipal"):
    station_id = station_id or name.lower().replace(" ", "-")
    return {
        "id": station_id,
        "name": name,
        "city": "Valencia",
        "lat": 39.5,
        "lon": -0.35,
        "lead": lead,
        "available": available,
        "tesla_ok": True,
        "max_power_kw": power,
        "price_label": "Now Free",
        "price_min": 0,
        "is_free": True,
        "decision": {"level": decision, "label": "Go now" if decision == "go" else "Verify first", "reason": "fixture"},
        "parking": {"kind": parking_kind, "label": "Street/municipal", "paid": False, "note": "fixture parking"},
        "primary_source": {
            "url": source_url or f"https://example.com/{station_id}",
            "action": "Check source",
            "label": "Station page",
            "quality": "exact",
        },
        "sources": [{"name": "Backup source", "url": f"https://source.example/{station_id}"}],
        "navigation": {"google": f"https://maps.example/{station_id}", "apple": f"https://apple.example/{station_id}"},
        "charge_claim": {"summary": "free fixture"},
        "parking_claim": {"summary": "parking fixture"},
        "connector_summary": {"details": ["CCS2 50 kW available"]},
    }


class FieldTestPacketTests(unittest.TestCase):
    def test_recordset_fingerprint_is_order_independent_and_packet_marks_revision_state(self):
        first = [{"id": "b", "price_min": 0.25}, {"id": "a", "price_min": 0.0}]
        second = list(reversed(first))

        self.assertEqual(
            qa_field_test_packet.recordset_sha256(first),
            qa_field_test_packet.recordset_sha256(second),
        )
        packet = qa_field_test_packet.render_packet(
            "https://voltago.app",
            {"stations": [], "leads": []},
            {"stations": [], "leads": []},
            [],
            [],
            [],
            {},
            {
                "expected_recordset_sha256": "expected",
                "deployed_recordset_sha256": "deployed",
                "match": False,
            },
        )
        self.assertIn("Candidate revision match: **no**", packet)
        self.assertIn("do not use for TC-051 departure", packet)
        self.assertIn("Candidate rows and source links are withheld", packet)
        self.assertNotIn("https://source.example", packet)

    def test_candidate_revision_matches_same_checkout_raw_recordset_not_enriched_api_rows(self):
        deployed = server.load_intelligence()
        raw = json.loads((ROOT / "data" / "free_intelligence.json").read_text(encoding="utf-8"))

        with mock.patch.object(qa_field_test_packet, "fetch_json", return_value=deployed):
            revision = qa_field_test_packet.candidate_revision(
                "http://local",
                1,
                ROOT / "data" / "free_intelligence.json",
            )

        self.assertTrue(revision["match"])
        self.assertEqual(revision["expected_recordset_sha256"], server.recordset_sha256(raw["records"]))
        self.assertEqual(revision["deployed_recordset_sha256"], revision["expected_recordset_sha256"])
        self.assertNotEqual(
            qa_field_test_packet.recordset_sha256(deployed["records"]),
            revision["deployed_recordset_sha256"],
            "The contract must not regress to hashing enriched/time-derived /api/intel rows",
        )

    def test_candidate_revision_rejects_stale_stored_canonical_fingerprint(self):
        records = [{"id": "current", "price_min": 0.0}]
        with tempfile.TemporaryDirectory() as tmp:
            canonical = pathlib.Path(tmp) / "canonical.json"
            canonical.write_text(
                json.dumps({"recordset_sha256": "stale", "records": records}),
                encoding="utf-8",
            )
            with mock.patch.object(
                qa_field_test_packet,
                "fetch_json",
                return_value={"recordset_sha256": "stale", "records": records},
            ):
                revision = qa_field_test_packet.candidate_revision(
                    "http://local",
                    1,
                    canonical,
                )

        self.assertFalse(revision["match"])
        self.assertFalse(revision["canonical_recordset_sha256_valid"])
        self.assertEqual(
            revision["expected_recordset_sha256"],
            qa_field_test_packet.recordset_sha256(records),
        )

    def test_api_fetch_uses_named_volta_user_agent(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"stations":[],"leads":[]}'

        with mock.patch("urllib.request.urlopen", return_value=Response()) as opener:
            payload = qa_field_test_packet.fetch_json("https://voltago.app", "/api/chargers", 1)

        request = opener.call_args.args[0]
        self.assertIsInstance(request, urllib.request.Request)
        self.assertTrue(request.get_header("User-agent").startswith("Volta-Go-QA/"))
        self.assertEqual(payload["stations"], [])

    def test_link_check_retries_get_after_transient_head_failure(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        head_error = urllib.error.HTTPError(
            "https://www.openstreetmap.org/node/1",
            503,
            "Service Unavailable",
            {},
            None,
        )
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[head_error, Response()],
        ) as opener:
            result = qa_field_test_packet.check_url(
                "https://www.openstreetmap.org/node/1",
                timeout=1,
            )

        self.assertEqual(opener.call_count, 2)
        self.assertTrue(result["reachable"])
        self.assertEqual(result["method"], "GET")
        self.assertEqual(result["status"], 200)

    def test_route_ready_and_easy_parking_classification(self):
        live = station("Live Gaia", decision="go", available=1, lead=False)
        lead = station("Lead Lidl", decision="verify", available=0, lead=True)
        paid = station("Paid Lead", decision="verify", available=0, lead=True, parking_kind="paid_or_metered")
        paid["parking"]["paid"] = True

        self.assertTrue(qa_field_test_packet.is_route_ready(live))
        self.assertFalse(qa_field_test_packet.is_route_ready(lead))
        self.assertTrue(qa_field_test_packet.is_easy_parking(lead))
        self.assertFalse(qa_field_test_packet.is_easy_parking(paid))

    def test_packet_renders_link_checks_and_stop_conditions(self):
        live = station("Live Gaia", decision="go", available=1, source_url="https://gaia.example/live")
        backup = station("Backup Electromaps", lead=True, power=150, source_url="https://electromaps.example/backup")
        default_payload = {"stations": [live], "leads": [backup], "warning": "low live coverage"}
        open_now_payload = {"stations": [live], "leads": [], "warning": "open warning"}
        link_checks = {
            "https://gaia.example/live": {"reachable": True, "status": 200, "method": "HEAD"},
            "https://electromaps.example/backup": {"reachable": False, "status": 404, "method": "GET", "error": "not found"},
        }

        packet = qa_field_test_packet.render_packet(
            "http://local",
            default_payload,
            open_now_payload,
            [live],
            [backup],
            [],
            link_checks,
        )

        self.assertIn("Source links checked: 2; failed: 1", packet)
        self.assertIn("Generated local:", packet)
        self.assertIn("Europe/Madrid", packet)
        self.assertIn("Generated UTC:", packet)
        self.assertIn("Live Route-Ready Candidates", packet)
        self.assertIn("link ok 200 via HEAD", packet)
        self.assertIn("LINK CHECK FAILED 404", packet)
        self.assertIn("Field-Test Result Table", packet)
        self.assertIn("Volta disclosed price/parking/source/live?", packet)
        self.assertIn("Source opened from Volta card?", packet)
        self.assertIn("capture the Volta card price, parking, source quality, live/lead status", packet)
        self.assertIn("Stop Conditions", packet)

    def test_packet_renders_no_live_route_ready_when_open_now_is_empty(self):
        stale_live = station("Stale Gaia", decision="check", available=2, source_url="https://gaia.example/stale")
        backup = station("Backup Lidl", lead=True, power=50, source_url="https://electromaps.example/lidl")
        default_payload = {"stations": [stale_live], "leads": [backup], "warning": "low live coverage 0/3"}
        open_now_payload = {"stations": [], "leads": [], "warning": "no route-ready"}

        packet = qa_field_test_packet.render_packet(
            "http://local",
            default_payload,
            open_now_payload,
            [],
            [backup],
            [],
            {},
        )

        self.assertIn("Open now response: 0 live stations, 0 leads.", packet)
        self.assertIn("No live route-ready candidate is confirmed right now", packet)
        self.assertNotIn("### 1. Stale Gaia", packet)
        self.assertIn("### 1. Backup Lidl", packet)

    def test_unofficial_free_supercharger_is_not_trusted_backup(self):
        supercharger = station(
            "Supercharger Torrent North, Spain",
            station_id="supercharger",
            lead=True,
            power=250,
            source_url="https://map.electromaps.com/en/p/174606",
        )
        supercharger["connector_summary"] = {"details": ["Tesla 250 kW"]}
        supercharger["connector_labels"] = ["Tesla"]
        supercharger["sources"] = [{
            "name": "Electromaps web-map API",
            "url": "https://map.electromaps.com/en/p/174606",
            "evidence": "Electromaps web-map API charge_price='FREE'; operator=Tesla",
        }]
        lidl = station("Backup Lidl", station_id="lidl", lead=True, power=50, source_url="https://map.electromaps.com/en/p/754727")
        default_payload = {"stations": [], "leads": [supercharger, lidl], "warning": "low live"}
        open_payload = {"stations": [], "leads": []}

        self.assertTrue(qa_field_test_packet.has_unofficial_supercharger_tariff_risk(supercharger))
        self.assertFalse(qa_field_test_packet.is_trusted_backup(supercharger))
        self.assertTrue(qa_field_test_packet.is_trusted_backup(lidl))

        def fake_fetch(base_url, path, timeout):
            return open_payload if "include_leads=false" in path else default_payload

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(qa_field_test_packet, "fetch_json", side_effect=fake_fetch), \
                mock.patch.object(qa_field_test_packet, "candidate_revision", return_value={
                    "match": True,
                    "expected_recordset_sha256": "same",
                    "deployed_recordset_sha256": "same",
                }):
            out = pathlib.Path(tmp) / "packet.md"
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = qa_field_test_packet.main([
                    "--base-url", "http://local",
                    "--out", str(out),
                    "--backups", "2",
                    "--watchlist", "3",
                ])
            summary = stdout.getvalue()
            text = out.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertIn('"backup_candidates": 1', summary)
        self.assertIn("Tesla/Supercharger tariff-risk leads excluded from trusted backups: 1", text)
        trusted_section = text.split("## Fast Or Strategic Watchlist", 1)[0]
        watchlist_section = text.split("## Fast Or Strategic Watchlist", 1)[1]
        self.assertIn("### 1. Backup Lidl", trusted_section)
        self.assertNotIn("Supercharger Torrent North", trusted_section)
        self.assertIn("Supercharger Torrent North", watchlist_section)
        self.assertIn("Tesla tariff warning", watchlist_section)

    def test_main_derives_open_now_when_available_endpoint_fails(self):
        stale_live = station("Stale Gaia", station_id="stale", decision="check", available=2, lead=False)
        backup = station("Backup Lidl", station_id="lidl", lead=True, power=50)
        default_payload = {"stations": [stale_live], "leads": [backup], "warning": "low live"}

        def fake_fetch(base_url, path, timeout):
            if "include_leads=false" in path:
                raise urllib.error.HTTPError(path, 502, "Bad Gateway", hdrs=None, fp=None)
            return default_payload

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(qa_field_test_packet, "fetch_json", side_effect=fake_fetch), \
                mock.patch.object(qa_field_test_packet, "candidate_revision", return_value={
                    "match": True,
                    "expected_recordset_sha256": "same",
                    "deployed_recordset_sha256": "same",
                }):
            out = pathlib.Path(tmp) / "packet.md"
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = qa_field_test_packet.main([
                    "--base-url", "http://local",
                    "--out", str(out),
                    "--backups", "2",
                ])
            summary = stdout.getvalue()
            text = out.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertIn('"open_now_stations": 0', summary)
        self.assertIn("Open now endpoint failed; derived from default payload", text)
        self.assertIn("No live route-ready candidate is confirmed right now", text)
        self.assertNotIn("### 1. Stale Gaia", text)
        self.assertIn("### 1. Backup Lidl", text)

    def test_main_dedupes_watchlist_and_fails_on_link_failure(self):
        live = station("Live Gaia", station_id="live", decision="go", available=1, power=11, source_url="https://gaia.example/live")
        backup = station("Backup Fast", station_id="backup", lead=True, power=250, source_url="https://source.example/fail")
        other_fast = station("Other Fast", station_id="other", lead=True, power=150, source_url="https://source.example/ok")
        default_payload = {"stations": [live], "leads": [backup, other_fast], "warning": "low live"}
        open_payload = {"stations": [live], "leads": []}

        def fake_fetch(base_url, path, timeout):
            return open_payload if "include_leads=false" in path else default_payload

        def fake_check(url, timeout):
            return {"url": url, "method": "HEAD", "status": 404 if "fail" in url else 200, "reachable": "fail" not in url, "error": None}

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(qa_field_test_packet, "fetch_json", side_effect=fake_fetch), \
                mock.patch.object(qa_field_test_packet, "candidate_revision", return_value={
                    "match": True,
                    "expected_recordset_sha256": "same",
                    "deployed_recordset_sha256": "same",
                }), \
                mock.patch.object(qa_field_test_packet, "check_url", side_effect=fake_check):
            out = pathlib.Path(tmp) / "packet.md"
            with contextlib.redirect_stdout(io.StringIO()):
                code = qa_field_test_packet.main([
                    "--base-url", "http://local",
                    "--out", str(out),
                    "--check-links",
                    "--backups", "1",
                    "--watchlist", "3",
                ])
            text = out.read_text(encoding="utf-8")

        self.assertEqual(code, 1)
        self.assertEqual(text.count("### 1. Backup Fast"), 1)
        self.assertIn("### 1. Other Fast", text)
        self.assertIn("LINK CHECK FAILED 404", text)

    def test_main_records_but_tolerates_transient_link_failure(self):
        live = station("Live Gaia", station_id="live", decision="go", available=1)
        payload = {"stations": [live], "leads": []}

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(qa_field_test_packet, "fetch_json", return_value=payload), \
                mock.patch.object(qa_field_test_packet, "candidate_revision", return_value={
                    "match": True,
                    "expected_recordset_sha256": "same",
                    "deployed_recordset_sha256": "same",
                }), \
                mock.patch.object(qa_field_test_packet, "check_url", return_value={
                    "url": "https://source.example/transient",
                    "method": "GET",
                    "status": 503,
                    "reachable": False,
                    "transient": True,
                    "error": "temporary outage",
                }):
            out = pathlib.Path(tmp) / "packet.md"
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = qa_field_test_packet.main([
                    "--base-url", "http://local",
                    "--out", str(out),
                    "--check-links",
                ])

        self.assertEqual(code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertGreaterEqual(summary["failed_links"], 1)
        self.assertEqual(summary["transient_link_failures"], summary["failed_links"])
        self.assertEqual(summary["hard_failed_links"], 0)

    def test_main_withholds_mismatched_candidate_rows_and_never_checks_their_links(self):
        blocked = station(
            "Blocked Electromaps Candidate",
            station_id="blocked",
            lead=True,
            power=150,
            source_url="https://map.electromaps.com/en/p/42",
        )
        default_payload = {"stations": [], "leads": [blocked]}
        open_payload = {"stations": [], "leads": []}

        def fake_fetch(_base_url, path, _timeout):
            return open_payload if "include_leads=false" in path else default_payload

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(qa_field_test_packet, "fetch_json", side_effect=fake_fetch), \
                mock.patch.object(qa_field_test_packet, "candidate_revision", return_value={
                    "match": False,
                    "expected_recordset_sha256": "local",
                    "deployed_recordset_sha256": "deployed",
                }), \
                mock.patch.object(qa_field_test_packet, "check_url") as check_url:
            out = pathlib.Path(tmp) / "packet.md"
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = qa_field_test_packet.main([
                    "--base-url", "http://local",
                    "--out", str(out),
                    "--check-links",
                ])
            summary = json.loads(stdout.getvalue())
            text = out.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertTrue(summary["candidate_rows_withheld"])
        self.assertEqual(summary["checked_links"], 0)
        check_url.assert_not_called()
        self.assertIn("Candidate rows and source links are withheld", text)
        self.assertNotIn("Blocked Electromaps Candidate", text)
        self.assertNotIn("https://map.electromaps.com/en/p/42", text)

    def test_packet_freshness_fails_when_open_now_warning_is_stale(self):
        packet_text = """
# Volta Go Field-Test Candidate Packet

Generated local: 2026-06-23T02:53:30+02:00 (Europe/Madrid)
Generated UTC: 2026-06-23T00:53:30+00:00

Base URL: `http://local`
Expected local recordset SHA-256: `expected`
Deployed recordset SHA-256: `deployed`
Candidate revision match: **yes**
Candidate status: **departure-ready revision match**

## Current API State

- Default response: 1 live stations, 394 source-backed leads.
- Open now response: 0 live stations, 0 leads.
- Default warning: Low live coverage: 0/3 live stations under cap. Leads need provider-source verification before driving.
- Open now warning: Open now endpoint failed; derived from default payload. HTTP Error 502: Bad Gateway
- Total default rows inspected: 395
- Tesla/Supercharger tariff-risk leads excluded from trusted backups: 8
- Source links checked: 8; failed: 0

## Live Route-Ready Candidates

_No live route-ready candidate is confirmed right now. Use the backup section only after provider/source verification._
"""
        parsed = qa_field_packet_freshness.parse_packet(packet_text)
        current = {
            "default": {"stations": 1, "leads": 394},
            "open_now": {"stations": 0, "leads": 0},
            "default_warning": "Low live coverage: 0/3 live stations under cap. Leads need provider-source verification before driving.",
            "open_warning": "Low live coverage: 0/3 live stations under cap. Refresh or widen filters before routing.",
            "rows": 395,
            "supercharger_risk": 8,
            "open_now_derived": False,
        }

        failures = qa_field_packet_freshness.compare(parsed, current)

        self.assertTrue(any("open_warning stale" in failure for failure in failures))

    def test_packet_freshness_tolerates_additive_partial_data_warning_when_route_state_matches(self):
        packet_text = """
# Volta Go Field-Test Candidate Packet

Generated local: 2026-06-23T03:20:00+02:00 (Europe/Madrid)
Generated UTC: 2026-06-23T01:20:00+00:00

Base URL: `http://local`
Expected local recordset SHA-256: `expected`
Deployed recordset SHA-256: `deployed`
Candidate revision match: **yes**
Candidate status: **departure-ready revision match**

## Current API State

- Default response: 1 live stations, 394 source-backed leads.
- Open now response: 0 live stations, 0 leads.
- Default warning: Low live coverage: 0/3 live stations under cap. Leads need provider-source verification before driving.
- Open now warning: Low live coverage: 0/3 live stations under cap. Refresh or widen filters before routing.
- Total default rows inspected: 395
- Tesla/Supercharger tariff-risk leads excluded from trusted backups: 8
- Source links checked: 8; failed: 0

## Live Route-Ready Candidates

_No live route-ready candidate is confirmed right now. Use the backup section only after provider/source verification._
"""
        parsed = qa_field_packet_freshness.parse_packet(packet_text)
        current = {
            "default": {"stations": 1, "leads": 394},
            "open_now": {"stations": 0, "leads": 0},
            "default_warning": "Low live coverage: 0/3 live stations under cap. Leads need provider-source verification before driving.",
            "open_warning": "MapaREVE refresh returned partial data; verify live source before routing. Low live coverage: 0/3 live stations under cap. Refresh or widen filters before routing.",
            "rows": 395,
            "supercharger_risk": 8,
            "open_now_derived": False,
        }

        self.assertEqual(qa_field_packet_freshness.compare(parsed, current), [])
        self.assertTrue(parsed["_warning_drifts"])

    def test_packet_freshness_report_sanitizes_raw_upstream_warning_copy(self):
        raw_warning = (
            "Live refresh failed; serving stale cache. HTTPError: HTTP Error 429: Too Many Requests "
            "Low live coverage: 0/3 live stations under cap. Leads need provider-source verification before driving."
        )

        safe_warning = qa_field_packet_freshness.warning_for_report(raw_warning)

        self.assertNotIn("HTTPError", safe_warning)
        self.assertNotIn("HTTP Error", safe_warning)
        self.assertNotIn("Too Many Requests", safe_warning)
        self.assertIn("Live refresh limited", safe_warning)
        self.assertIn("Low live coverage", safe_warning)
        self.assertTrue(qa_field_packet_freshness.warning_has_raw_technical_failure(raw_warning))

    def test_field_packet_render_sanitizes_raw_upstream_warning_copy(self):
        raw_warning = (
            "Live refresh failed; serving stale cache. HTTPError: HTTP Error 429: Too Many Requests "
            "Low live coverage: 0/3 live stations under cap. Leads need provider-source verification before driving."
        )
        open_warning = (
            "Live refresh failed; serving stale cache from a broader filter and reapplying current route filters. "
            "HTTPError: HTTP Error 429: Too Many Requests Low live coverage: 0/3 live stations under cap. "
            "Refresh or widen filters before routing."
        )

        packet = qa_field_test_packet.render_packet(
            "http://local",
            {"stations": [], "leads": [], "warning": raw_warning},
            {"stations": [], "leads": [], "warning": open_warning},
            [],
            [],
            [],
            {},
        )

        self.assertIn("Live refresh limited", packet)
        self.assertIn("Low live coverage", packet)
        self.assertNotIn("HTTPError", packet)
        self.assertNotIn("HTTP Error", packet)
        self.assertNotIn("Too Many Requests", packet)

    def test_packet_freshness_compare_uses_sanitized_warning_with_technical_flag(self):
        packet_text = """
# Volta Go Field-Test Candidate Packet

Generated local: 2026-06-23T03:20:00+02:00 (Europe/Madrid)
Generated UTC: 2026-06-23T01:20:00+00:00

Base URL: `http://local`
Expected local recordset SHA-256: `expected`
Deployed recordset SHA-256: `deployed`
Candidate revision match: **yes**
Candidate status: **departure-ready revision match**

## Current API State

- Default response: 1 live stations, 394 source-backed leads.
- Open now response: 0 live stations, 0 leads.
- Default warning: Low live coverage: 0/3 live stations under cap. Leads need provider-source verification before driving.
- Open now warning: Low live coverage: 0/3 live stations under cap. Refresh or widen filters before routing.
- Total default rows inspected: 395
- Tesla/Supercharger tariff-risk leads excluded from trusted backups: 8
- Source links checked: 8; failed: 0

## Live Route-Ready Candidates

_No live route-ready candidate is confirmed right now. Use the backup section only after provider/source verification._
"""
        parsed = qa_field_packet_freshness.parse_packet(packet_text)
        current = {
            "default": {"stations": 1, "leads": 394},
            "open_now": {"stations": 0, "leads": 0},
            "default_warning": qa_field_packet_freshness.warning_for_report(
                "Live refresh failed; serving stale cache. HTTPError: HTTP Error 429: Too Many Requests "
                "Low live coverage: 0/3 live stations under cap. Leads need provider-source verification before driving."
            ),
            "open_warning": qa_field_packet_freshness.warning_for_report(
                "Live refresh failed; serving stale cache from a broader filter and reapplying current route filters. "
                "HTTPError: HTTP Error 429: Too Many Requests Low live coverage: 0/3 live stations under cap. "
                "Refresh or widen filters before routing."
            ),
            "default_warning_had_technical_failure": True,
            "open_warning_had_technical_failure": True,
            "rows": 395,
            "supercharger_risk": 8,
            "open_now_derived": False,
        }

        self.assertEqual(qa_field_packet_freshness.compare(parsed, current), [])
        self.assertTrue(parsed["_warning_drifts"])
        self.assertFalse(any("HTTP" in drift or "Too Many Requests" in drift for drift in parsed["_warning_drifts"]))

    def test_packet_freshness_passes_current_zero_route_ready_packet(self):
        default_payload = {"stations": [station("Stale Gaia", decision="check", available=2)], "leads": [], "warning": "low live"}
        open_payload = {"stations": [], "leads": [], "warning": "refresh or widen"}
        packet_text = qa_field_test_packet.render_packet(
            "http://local",
            default_payload,
            open_payload,
            [],
            [],
            [],
            {},
        )
        parsed = qa_field_packet_freshness.parse_packet(packet_text)
        current = {
            "default": {"stations": 1, "leads": 0},
            "open_now": {"stations": 0, "leads": 0},
            "default_warning": "low live",
            "open_warning": "refresh or widen",
            "rows": 1,
            "supercharger_risk": 0,
            "open_now_derived": False,
        }

        self.assertEqual(qa_field_packet_freshness.compare(parsed, current), [])

    def test_packet_freshness_rejects_matching_packet_from_prior_canonical_revision(self):
        packet = {
            "expected_recordset_sha256": "prior",
            "deployed_recordset_sha256": "prior",
            "candidate_revision_match": True,
            "default": {"stations": 1, "leads": 0},
            "open_now": {"stations": 0, "leads": 0},
            "default_warning": "verify",
            "open_warning": "verify",
            "rows": 1,
            "supercharger_risk": 0,
            "has_no_live_route_ready_message": True,
        }
        current = {
            "deployed_recordset_sha256": "prior",
            "default": {"stations": 1, "leads": 0},
            "open_now": {"stations": 0, "leads": 0},
            "default_warning": "verify",
            "open_warning": "verify",
            "rows": 1,
            "supercharger_risk": 0,
            "open_now_derived": False,
        }

        failures = qa_field_packet_freshness.compare(packet, current, "current")

        self.assertTrue(any("expected recordset is stale" in failure for failure in failures))
        self.assertTrue(any("deployed recordset does not match" in failure for failure in failures))

    def test_packet_freshness_rejects_changed_deployment_when_counts_are_identical(self):
        packet = {
            "expected_recordset_sha256": "canonical",
            "deployed_recordset_sha256": "canonical",
            "candidate_revision_match": True,
            "default": {"stations": 1, "leads": 0},
            "open_now": {"stations": 0, "leads": 0},
            "default_warning": "verify",
            "open_warning": "verify",
            "rows": 1,
            "supercharger_risk": 0,
            "has_no_live_route_ready_message": True,
        }
        current = {
            "deployed_recordset_sha256": "production-moved",
            "default": {"stations": 1, "leads": 0},
            "open_now": {"stations": 0, "leads": 0},
            "default_warning": "verify",
            "open_warning": "verify",
            "rows": 1,
            "supercharger_risk": 0,
            "open_now_derived": False,
        }

        failures = qa_field_packet_freshness.compare(packet, current, "canonical")

        self.assertTrue(any("current deployed recordset" in failure for failure in failures))
        self.assertTrue(any("stale relative to current /api/intel" in failure for failure in failures))

    def test_freshness_main_emits_complete_fail_closed_report_when_deployment_lacks_hash(self):
        records = [{"id": "current", "price_min": 0.0}]
        canonical_sha = qa_field_test_packet.recordset_sha256(records)
        packet_text = qa_field_test_packet.render_packet(
            "https://voltago.app",
            {"stations": [], "leads": []},
            {"stations": [], "leads": []},
            [],
            [],
            [],
            {},
            {
                "expected_recordset_sha256": canonical_sha,
                "deployed_recordset_sha256": None,
                "match": False,
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            packet = root / "packet.md"
            canonical = root / "canonical.json"
            report = root / "freshness.json"
            packet.write_text(packet_text, encoding="utf-8")
            canonical.write_text(
                json.dumps({"records": records, "recordset_sha256": canonical_sha}),
                encoding="utf-8",
            )
            with mock.patch.object(
                qa_field_packet_freshness.qa_field_test_packet,
                "fetch_json",
                return_value={"records": records},
            ), contextlib.redirect_stdout(io.StringIO()):
                code = qa_field_packet_freshness.main([
                    "--packet", str(packet),
                    "--canonical", str(canonical),
                    "--base-url", "https://voltago.app",
                    "--out", str(report),
                ])
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["packet"], str(packet.resolve()))
        self.assertEqual(payload["canonical_data"], str(canonical.resolve()))
        for key in {
            "packet_sha256",
            "canonical_recordset_sha256",
            "packet_state",
            "current_state",
            "current_state_error",
        }:
            self.assertIn(key, payload)
        self.assertIsNone(payload["current_state"]["deployed_recordset_sha256"])
        self.assertEqual(payload["current_state_error"], "current deployment does not expose recordset_sha256")
        self.assertFalse(any("ValueError" in failure for failure in payload["failures"]))

    def test_freshness_receipt_paths_survive_checkout_relocation(self):
        records = [{"id": "current", "price_min": 0.0}]
        canonical_sha = qa_field_test_packet.recordset_sha256(records)
        packet_text = qa_field_test_packet.render_packet(
            "http://local", {"stations": [], "leads": []},
            {"stations": [], "leads": []}, [], [], [], {},
            {"expected_recordset_sha256": canonical_sha,
             "deployed_recordset_sha256": canonical_sha, "match": True},
        )
        current = qa_field_packet_freshness.parse_packet(packet_text)
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("developer-checkout", "linux-runner-checkout"):
                with self.subTest(checkout=name):
                    root = pathlib.Path(tmp) / name
                    packet = root / "qa" / "field-test-candidates.md"
                    canonical = root / "data" / "free_intelligence.json"
                    packet.parent.mkdir(parents=True)
                    canonical.parent.mkdir(parents=True)
                    packet.write_text(packet_text, encoding="utf-8")
                    canonical.write_text(json.dumps({"records": records}), encoding="utf-8")
                    output = io.StringIO()
                    with mock.patch.object(qa_field_packet_freshness, "ROOT", root), \
                         mock.patch.object(qa_field_packet_freshness, "current_state", return_value=current), \
                         contextlib.redirect_stdout(output):
                        code = qa_field_packet_freshness.main([
                            "--packet", str(packet), "--canonical", str(canonical),
                        ])
                    report = json.loads(output.getvalue())
                    self.assertEqual(code, 0, report["failures"])
                    self.assertEqual(report["packet"], "qa/field-test-candidates.md")
                    self.assertEqual(report["canonical_data"], "data/free_intelligence.json")
                    self.assertEqual(report["canonical_recordset_sha256"], canonical_sha)


if __name__ == "__main__":
    unittest.main()
