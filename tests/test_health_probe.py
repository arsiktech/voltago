#!/usr/bin/env python3
"""Safety contracts for the unattended health/staleness probe."""
from __future__ import annotations

import datetime as dt
import http.client
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "health_probe.py"


def load_module():
    spec = importlib.util.spec_from_file_location("health_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProbeHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.base_url = "http://127.0.0.1:8773"

    def test_fetches_exact_health_path_without_query(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.getcode.return_value = 200
        response.geturl.return_value = f"{self.base_url}/api/health"
        response.read.return_value = b'{"ok": true}'
        opener = MagicMock()
        opener.open.return_value = response

        with patch.object(self.module.urllib.request, "build_opener", return_value=opener):
            payload = self.module.fetch_health(self.base_url, timeout=2)

        self.assertTrue(payload["ok"])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, f"{self.base_url}/api/health")
        self.assertEqual(request.get_method(), "GET")

    def test_redirect_is_rejected(self) -> None:
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(
            f"{self.base_url}/api/health",
            302,
            "Found",
            {"Location": "/final"},
            io.BytesIO(),
        )

        with patch.object(self.module.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(self.module.ProbeFailure) as raised:
                self.module.fetch_health(self.base_url, timeout=2)

        self.assertIn("redirect", str(raised.exception).lower())

    def test_invalid_origin_never_echoes_credentials_or_query_tokens(self) -> None:
        for unsafe in (
            "https://user:secret@example.com",
            "https://example.com?token=private-value",
        ):
            with self.subTest(unsafe=unsafe):
                stderr = io.StringIO()
                with patch.object(self.module, "notify", return_value=False), patch.object(
                    self.module.sys, "stderr", stderr
                ):
                    returncode = self.module.main(["--base-url", unsafe])

                self.assertEqual(returncode, 1)
                output = stderr.getvalue()
                self.assertNotIn("user", output)
                self.assertNotIn("secret", output)
                self.assertNotIn("token", output)
                self.assertNotIn("private-value", output)
                evidence = json.loads(output)
                self.assertEqual(evidence["base_url"], "[unvalidated origin redacted]")

    def test_malformed_authority_uses_redacted_failure_and_notification_path(self) -> None:
        cases = (
            ("http://[bad", "[bad"),
            ("http://127.0.0.1:bad", ":bad"),
            ("http://bad host", "bad host"),
            ("http://bad\thost", "bad"),
            ("http://bad\x00host", "bad"),
            ("http://:8773", ":8773"),
        )
        for unsafe, secret_fragment in cases:
            with self.subTest(unsafe=repr(unsafe)):
                stderr = io.StringIO()
                with patch.object(self.module, "notify", return_value=False) as notify_mock, patch.object(
                    self.module.sys, "stderr", stderr
                ):
                    returncode = self.module.main(["--base-url", unsafe])

                self.assertEqual(returncode, 1)
                output = stderr.getvalue()
                self.assertNotIn("Traceback", output)
                self.assertNotIn(secret_fragment, output)
                evidence = json.loads(output)
                self.assertEqual(evidence["base_url"], "[unvalidated origin redacted]")
                self.assertEqual(
                    evidence["failure"],
                    "base URL must be a valid absolute http(s) origin",
                )
                notify_mock.assert_called_once()
                notification = notify_mock.call_args.args[1]
                self.assertNotIn(secret_fragment, notification)

    def test_http_stack_invalid_url_becomes_redacted_notifiable_failure(self) -> None:
        opener = MagicMock()
        opener.open.side_effect = http.client.InvalidURL("invalid authority private-value")
        stderr = io.StringIO()

        with (
            patch.object(self.module.urllib.request, "build_opener", return_value=opener),
            patch.object(self.module, "notify", return_value=False) as notify_mock,
            patch.object(self.module.sys, "stderr", stderr),
        ):
            returncode = self.module.main(["--base-url", self.base_url])

        self.assertEqual(returncode, 1)
        output = stderr.getvalue()
        self.assertNotIn("Traceback", output)
        self.assertNotIn("private-value", output)
        evidence = json.loads(output)
        self.assertEqual(
            evidence["failure"],
            "base URL must be a valid absolute http(s) origin",
        )
        notify_mock.assert_called_once()
        self.assertNotIn("private-value", notify_mock.call_args.args[1])


class LocalDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.now = dt.datetime(2026, 7, 13, 12, 0, tzinfo=dt.timezone.utc)

    def write_db(self, root: Path, **updates: object) -> Path:
        payload: dict[str, object] = {
            "generated_at": "2026-07-13T11:00:00+00:00",
            "total_records": 1,
            "raw_records": 1,
            "errors": [],
            "records": [{"id": "one"}],
        }
        payload.update(updates)
        path = root / "db.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_validates_generated_time_count_errors_and_nonempty_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_db(Path(tmp))

            evidence = self.module.validate_database(path, max_age_days=10, now=self.now)

            self.assertEqual(evidence["records"], 1)
            self.assertAlmostEqual(evidence["age_days"], 1 / 24, places=4)

    def test_rejects_stale_future_empty_mismatched_and_error_payloads(self) -> None:
        cases = {
            "stale": {"generated_at": "2026-06-01T00:00:00+00:00"},
            "future": {"generated_at": "2026-07-14T00:00:00+00:00"},
            "empty": {"records": [], "total_records": 0},
            "count": {"total_records": 2},
            "errors": {"errors": ["upstream failed"]},
        }
        for label, updates in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                path = self.write_db(Path(tmp), **updates)
                with self.assertRaises(self.module.ProbeFailure):
                    self.module.validate_database(path, max_age_days=10, now=self.now)

    def test_optional_receipt_must_match_current_database_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.write_db(root)
            receipt = root / "last.json"
            receipt.write_text(json.dumps({
                "ok": True,
                "canonical_sha256": "0" * 64,
            }), encoding="utf-8")

            with self.assertRaises(self.module.ProbeFailure):
                self.module.validate_receipt(receipt, db)

            receipt.write_text(json.dumps({
                "ok": True,
                "mode": "real",
                "stage": "complete",
                "promotion": {"performed": True},
                "commit": {"created": True, "sha": "abc", "no_changes": False},
                "canonical_sha256": self.module.sha256_file(db),
            }), encoding="utf-8")
            evidence = self.module.validate_receipt(receipt, db)
            self.assertEqual(evidence["canonical_sha256"], self.module.sha256_file(db))

    def test_receipt_rejects_dry_run_or_incomplete_real_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.write_db(root)
            digest = self.module.sha256_file(db)
            invalid_receipts = [
                {
                    "ok": True,
                    "mode": "dry-run",
                    "stage": "complete",
                    "promotion": {"performed": False},
                    "commit": {"created": False, "no_changes": True},
                    "canonical_sha256": digest,
                },
                {
                    "ok": True,
                    "mode": "real",
                    "stage": "push",
                    "promotion": {"performed": True},
                    "commit": {"created": True, "sha": "abc"},
                    "canonical_sha256": digest,
                },
                {
                    "ok": True,
                    "mode": "real",
                    "stage": "complete",
                    "promotion": {"performed": False},
                    "commit": {"created": True, "sha": "abc"},
                    "canonical_sha256": digest,
                },
            ]
            for index, payload in enumerate(invalid_receipts):
                with self.subTest(index=index):
                    receipt = root / f"invalid-{index}.json"
                    receipt.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(self.module.ProbeFailure):
                        self.module.validate_receipt(receipt, db)


class NotificationSafetyTests(unittest.TestCase):
    def test_notification_uses_shlex_argv_without_a_shell(self) -> None:
        module = load_module()
        with patch.object(module.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            sent = module.notify('notify-bin --channel "Volta Ops"', "probe failed", root=ROOT, timeout=3)

        self.assertTrue(sent)
        command = run_mock.call_args.args[0]
        self.assertEqual(command, ["notify-bin", "--channel", "Volta Ops", "probe failed"])
        self.assertIs(run_mock.call_args.kwargs["shell"], False)
        self.assertLessEqual(run_mock.call_args.kwargs["timeout"], 60)


if __name__ == "__main__":
    unittest.main()
