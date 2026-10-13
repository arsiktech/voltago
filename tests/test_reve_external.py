"""Unit tests for the (inactive, key-gated) Mapa REVE external client helpers."""

import io
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reve_external as reve


def tariff(components):
    return {"currency": "EUR", "elements": [{"price_components": components}]}


class ReveFreeVerdictTests(unittest.TestCase):
    def test_all_zero_components_is_free(self) -> None:
        self.assertTrue(reve.tariff_is_free(tariff([
            {"type": "ENERGY", "price": 0}, {"type": "FLAT", "price": 0.0},
        ])))

    def test_any_positive_component_is_paid(self) -> None:
        self.assertFalse(reve.tariff_is_free(tariff([
            {"type": "ENERGY", "price": 0}, {"type": "FLAT", "price": 1.0},
        ])))

    def test_missing_components_is_unknown_never_free(self) -> None:
        self.assertIsNone(reve.tariff_is_free({"elements": []}))
        self.assertIsNone(reve.tariff_is_free({}))

    def test_malformed_price_is_not_free(self) -> None:
        self.assertIsNone(reve.tariff_is_free(tariff([{"type": "ENERGY", "price": "gratis"}])))
        self.assertIsNone(reve.tariff_is_free(tariff([{"type": "ENERGY"}])))

    def test_bad_numbers_unknown_currency_and_conditions_never_become_free(self) -> None:
        for value in (-1, True, float("nan"), float("inf"), "-0.2", "NaN", None):
            with self.subTest(price=value):
                self.assertIsNone(reve.tariff_is_free(tariff([{"type": "ENERGY", "price": value}])))
        for currency in (None, "", "USD", "CAD"):
            value = tariff([{"type": "ENERGY", "price": 0}])
            value["currency"] = currency
            self.assertIsNone(reve.tariff_is_free(value))
        conditional = tariff([{"type": "ENERGY", "price": 0}])
        conditional["elements"][0]["restrictions"] = {"end_time": "06:00"}
        self.assertIsNone(reve.tariff_is_free(conditional))
        conditional = tariff([{"type": "ENERGY", "price": 0}])
        conditional["end_date_time"] = "2020-01-01T00:00:00Z"
        self.assertIsNone(reve.tariff_is_free(conditional))

    def test_vat_minimum_charges_and_malformed_components(self) -> None:
        value = tariff([{"type": "ENERGY", "price": 0, "vat": 21, "step_size": 1}])
        self.assertTrue(reve.tariff_is_free(value))
        value["min_price"] = {"incl_vat": 2, "excl_vat": 1.65}
        self.assertFalse(reve.tariff_is_free(value))
        for component in ({"type": "ENERGY", "price": 0, "vat": -1},
                          {"type": "ENERGY", "price": 0, "vat": "NaN"},
                          {"type": "ENERGY", "price": 0, "step_size": False},
                          {"type": "UNREVIEWED_FEE", "price": 0}, None):
            self.assertIsNone(reve.tariff_is_free(tariff([{"type": "ENERGY", "price": 0}, component])))
        value = tariff([{"type": "ENERGY", "price": 0}])
        value["elements"].append({"price_components": []})
        self.assertIsNone(reve.tariff_is_free(value))
        self.assertIsNone(reve.connector_free_verdict({"tariffs": [tariff([{"type": "ENERGY", "price": 0}]), None]}))
        value = tariff([{"type": "ENERGY", "price": 0}])
        value["elements"][0]["restrictions"] = []
        self.assertIsNone(reve.tariff_is_free(value))

    def test_connector_verdict_requires_every_tariff_free(self) -> None:
        free_t = tariff([{"type": "ENERGY", "price": 0}])
        paid_t = tariff([{"type": "ENERGY", "price": 0.3}])
        self.assertTrue(reve.connector_free_verdict({"tariffs": [free_t, free_t]}))
        self.assertFalse(reve.connector_free_verdict({"tariffs": [free_t, paid_t]}))
        self.assertIsNone(reve.connector_free_verdict({"tariffs": []}))
        self.assertIsNone(reve.connector_free_verdict({}))

    def test_rows_from_payload_shapes(self) -> None:
        rows = [{"evse_id": "ES*X*1"}]
        self.assertEqual(reve.rows_from_payload(rows), rows)
        self.assertEqual(reve.rows_from_payload({"data": rows}), rows)
        self.assertEqual(reve.rows_from_payload({"items": rows}), rows)
        self.assertEqual(reve.rows_from_payload("nonsense"), [])

    def test_sync_without_key_exits_inactive(self) -> None:
        original = dict(reve.os.environ)
        reve.os.environ.pop("VOLTA_REVE_API_KEY", None)
        try:
            with self.assertRaises(SystemExit):
                reve.sync_tariffs()
        finally:
            reve.os.environ.clear()
            reve.os.environ.update(original)


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, url):
        super().__init__(body)
        self.url = url
        self.headers = {"Content-Length": str(len(body))}

    def geturl(self):
        return self.url


class ReveTransportAndStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "mirror.json"
        self.patch_state = patch.object(reve, "STATE_PATH", self.path)
        self.patch_state.start()
        self.addCleanup(self.patch_state.stop)
        self.patch_key = patch.dict(reve.os.environ, {"VOLTA_REVE_API_KEY": "test-key-never-persist"})
        self.patch_key.start()
        self.addCleanup(self.patch_key.stop)

    def row(self, connector="1", price=0):
        return {"evse_id": "ES*TEST*E1", "connector_id": connector,
                "tariffs": [tariff([{"type": "ENERGY", "price": price}])]}

    def test_durable_rolling_quota_includes_concurrent_callers_and_clock_rollback(self):
        def reserve(_):
            try:
                reve.reserve_request(now=10000)
                return True
            except reve.SourcePolicyBlocked:
                return False
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(reserve, range(12))), 5)
        with self.assertRaises(reve.SourcePolicyBlocked):
            reve.reserve_request(now=9999)
        with self.assertRaises(reve.SourcePolicyBlocked):
            reve.reserve_request(now=13599)
        reve.reserve_request(now=13600)

    def test_request_uses_exact_get_and_unredirected_header_not_url(self):
        opened = []
        def open_request(request, timeout):
            opened.append(request)
            return Response(b'{"data": []}', request.full_url)
        opener = Mock()
        opener.open.side_effect = open_request
        with patch.object(reve.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(reve.fetch_page(reve.TARIFFS_ENDPOINT, page=1), {"data": []})
        request = opened[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertNotIn("test-key-never-persist", request.full_url)
        self.assertNotIn("X-api-key", request.headers)
        self.assertEqual(request.unredirected_hdrs["X-api-key"], "test-key-never-persist")
        self.assertFalse(self.path.exists())

    def test_unreviewed_endpoint_and_redirect_fail_before_credentials_leave_origin(self):
        with patch.object(reve.urllib.request, "build_opener") as opener:
            for endpoint in ("https://evil.example/api/external/v1/connectors/tariffs",
                             reve.TARIFFS_ENDPOINT + "?redirect=1", reve.TARIFFS_ENDPOINT.replace("https:", "http:"),
                             "https://www.mapareve.es/api/external/v1/start_session"):
                with self.assertRaises(reve.SourcePolicyBlocked):
                    reve.fetch_page(endpoint, page=1)
            opener.assert_not_called()
        with self.assertRaises(reve.SourcePolicyBlocked):
            reve._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example")
        self.assertFalse(self.path.with_suffix(".quota.sqlite3").exists())

    def test_failed_requests_consume_quota_and_bad_payloads_are_bounded(self):
        opener = Mock()
        opener.open.side_effect = OSError("upstream unavailable")
        with patch.object(reve.urllib.request, "build_opener", return_value=opener):
            for _ in range(5):
                with self.assertRaises(OSError):
                    reve.fetch_page(reve.TARIFFS_ENDPOINT, page=1)
            with self.assertRaises(reve.SourcePolicyBlocked):
                reve.fetch_page(reve.TARIFFS_ENDPOINT, page=1)
        self.assertEqual(opener.open.call_count, 5)

    def test_response_byte_limit_and_nonfinite_json(self):
        def open_request(request, timeout):
            return Response(b'{"data": [{"price": NaN}]}', request.full_url)
        opener = Mock()
        opener.open.side_effect = open_request
        with patch.object(reve.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                reve.fetch_page(reve.TARIFFS_ENDPOINT, page=1)
            with patch.object(reve, "MAX_RESPONSE_BYTES", 4):
                with self.assertRaises(ValueError):
                    reve.fetch_page(reve.TARIFFS_ENDPOINT, page=1)

    def test_response_echoed_credential_is_discarded_before_caching(self):
        opener = Mock()
        opener.open.side_effect = lambda request, timeout: Response(
            b'{"data": [], "debug": "test-key-never-persist"}', request.full_url)
        with patch.object(reve.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                reve.sync_tariffs()
        self.assertNotIn("test-key-never-persist", self.path.read_text())
        self.assertEqual(reve.load_tariff_evidence(), {})

    def test_sync_preserves_distinct_connectors_raw_tariff_and_no_secret(self):
        with patch.object(reve, "fetch_page", return_value={"data": [self.row("1"), self.row("2", .4)]}):
            result = reve.sync_tariffs()
        self.assertEqual(result["connectors_tracked"], 2)
        self.assertEqual(result["free_connectors"], 1)
        self.assertFalse(result["live_status_integrated"])
        evidence = reve.load_tariff_evidence()
        self.assertEqual(len(evidence), 2)
        self.assertEqual({value["currency"] for value in evidence.values()}, {"EUR"})
        self.assertNotIn("test-key-never-persist", self.path.read_text())

    def test_multi_run_backfill_keeps_original_delta_window_start(self):
        first_time = "2026-10-01T00:00:00+00:00"
        with patch.object(reve, "now_iso", return_value=first_time), patch.object(reve, "PAGE_LIMIT", 1), patch.object(reve, "fetch_page", return_value={"data": [self.row()]}):
            reve.sync_tariffs(budget=1)
        self.assertEqual(reve.load_state()["tariffs"]["page"], 2)
        with patch.object(reve, "now_iso", return_value="2026-10-02T00:00:00+00:00"), patch.object(reve, "fetch_page", return_value={"data": []}):
            reve.sync_tariffs(budget=1)
        state = reve.load_state()["tariffs"]
        self.assertEqual(state["date_from"], "2026-09-30T23:59:00+00:00")
        self.assertIsNone(state["window_started_at"])

    def test_upstream_error_does_not_advance_cursor(self):
        for payload in ({"error": "rate limited", "data": []}, {"status_code": 3000, "data": []},
                        {"data": [{"connector_id": "1", "tariffs": []}]}):
            with patch.object(reve, "fetch_page", return_value=payload):
                with self.assertRaises(ValueError):
                    reve.sync_tariffs()
        state = reve.load_state()["tariffs"]
        self.assertEqual(state["page"], 1)
        self.assertIsNone(state["date_from"])

    def test_stale_future_and_tampered_free_flags_never_reused(self):
        with patch.object(reve, "fetch_page", return_value=[self.row("1", .4), self.row("2"), self.row("3")]):
            reve.sync_tariffs()
        state = reve.load_state()
        values = list(state["tariffs"]["connectors"].values())
        values[0]["free"] = True
        values[1]["fetched_at"] = "2020-01-01T00:00:00Z"
        values[2]["fetched_at"] = "2100-01-01T00:00:00Z"
        reve.save_state(state)
        evidence = reve.load_tariff_evidence()
        self.assertEqual(len(evidence), 1)
        self.assertFalse(next(iter(evidence.values()))["free"])

    def test_legacy_state_rebackfills_and_corrupt_state_is_not_overwritten(self):
        self.path.write_text(json.dumps({"tariffs": {"page": 5, "connectors": {"legacy": {"free": True}}}}))
        self.assertEqual(reve.load_tariff_evidence(), {})
        self.assertEqual(reve.load_state()["tariffs"]["page"], 1)
        self.path.write_text("incomplete JSON")
        with self.assertRaises(ValueError):
            reve.load_state()
        self.assertEqual(self.path.read_text(), "incomplete JSON")

    def test_status_is_unconfigured_and_never_fetches_without_key(self):
        with patch.dict(reve.os.environ, {"VOLTA_REVE_API_KEY": ""}), patch.object(reve.urllib.request, "build_opener") as opener:
            status = reve.configuration_status()
            self.assertEqual(status["status"], "unconfigured")
            self.assertFalse(status["production_ingestion_enabled"])
            with self.assertRaises(SystemExit):
                reve.probe()
            opener.assert_not_called()

    def test_candidate_exact_join_preserves_tariff_evidence_without_price_or_status_promotion(self):
        row = self.row("1", .45)
        row["tariffs"][0]["elements"][0]["restrictions"] = {"start_time": "00:00", "end_time": "06:00"}
        row["last_tariff_updated"] = "2026-10-01T09:00:00Z"
        with patch.object(reve, "fetch_page", return_value=[row]):
            reve.sync_tariffs()
        source = self.path.parent / "baseline.json"
        output = self.path.parent / "joined.candidate.json"
        record = {"id": "one", "country_code": "ES", "is_free": False, "price_min": None,
                  "status": "VERIFY", "available": 0, "connectors": [
                      {"evse_id": "ES*TEST*E1", "connector_id": "1", "status": "UNKNOWN"},
                      {"connector_id": "ES*TEST*E1:1", "status": "UNKNOWN"}]}
        source.write_text(json.dumps({"records": [record], "meta_after_records": {"keep": True}}))
        before = source.read_bytes()
        with patch.object(reve, "fetch_page") as fetch:
            report = reve.join_candidate(source, output)
            fetch.assert_not_called()
        result = json.loads(output.read_text())
        annotated = result["records"][0]
        evidence = annotated["connectors"][0].pop("reve_tariff_evidence")
        self.assertEqual(annotated, record)
        self.assertEqual(evidence["currency"], "EUR")
        self.assertEqual(evidence["tariffs"], row["tariffs"])
        self.assertEqual(evidence["last_tariff_updated"], row["last_tariff_updated"])
        self.assertIsNone(evidence["free"])
        self.assertFalse(evidence["price_promotion_enabled"])
        self.assertEqual(report["connectors_matched"], 1)
        self.assertEqual(report["connectors_missing_identity"], 1)
        self.assertEqual(result["meta_after_records"], {"keep": True})
        self.assertFalse(result["promotion_allowed"])
        self.assertEqual(source.read_bytes(), before)

    def test_candidate_duplicate_country_conflict_and_expired_annotations_fail_closed(self):
        with patch.object(reve, "fetch_page", return_value=[self.row("1"), self.row("2")]):
            reve.sync_tariffs()
        source = self.path.parent / "baseline.json"
        output = self.path.parent / "joined.candidate.json"
        records = [
            {"id": "duplicate1", "evse_id": "ES*TEST*E1", "connectors": [{"connector_id": "1"}]},
            {"id": "duplicate2", "connectors": [{"evse_id": "ES*TEST*E1", "connector_id": "1"}]},
            {"id": "wrong-country", "country_code": "FR", "evse_id": "ES*TEST*E1", "connectors": [{"connector_id": "2"}]},
            {"id": "evse-conflict", "evse_id": "ES*TEST*E2", "connectors": [{"evse_id": "ES*TEST*E1", "connector_id": "2"}]},
            {"id": "expired", "connectors": [{"connector_id": "synthetic", "reve_tariff_evidence": {"free": True}}]},
        ]
        source.write_text(json.dumps({"records": records}))
        report = reve.join_candidate(source, output)
        self.assertEqual(report["connectors_matched"], 0)
        self.assertEqual(report["ambiguous_identities"], 1)
        self.assertEqual(report["connectors_missing_identity"], 3)
        for row in json.loads(output.read_text())["records"]:
            self.assertNotIn("reve_tariff_evidence", row["connectors"][0])

    def test_candidate_rejects_canonical_output_and_streams_two_passes(self):
        source = self.path.parent / "baseline.json"
        source.write_text('{"records":[{"id":"sample","connectors":[{"connector_id":"synthetic"}]}]}')
        output = self.path.parent / "joined.candidate.json"
        with patch.object(reve, "load_json_recordset", wraps=reve.load_json_recordset) as loader:
            report = reve.join_candidate(source, output)
            self.assertEqual(loader.call_count, 2)
            self.assertTrue(all(call.kwargs["record_transform"] for call in loader.call_args_list))
        self.assertEqual(report["status"], "unmatched_missing_identities")
        self.assertEqual(report["connectors_missing_identity"], 1)
        for forbidden in (source, self.path, self.path.parent / "free_intelligence.json"):
            with self.assertRaises(ValueError):
                reve.join_candidate(source, forbidden)
        original_output = output.read_bytes()
        source.write_text('{"records": ["invalid"]}')
        with self.assertRaises(ValueError):
            reve.join_candidate(source, output)
        self.assertEqual(output.read_bytes(), original_output)


if __name__ == "__main__":
    unittest.main()
