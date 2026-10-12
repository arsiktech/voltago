#!/usr/bin/env python3
"""Bounded no-redirect health and intelligence-freshness probe for Volta."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any, Dict, Optional, Sequence
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "free_intelligence.json"
MAX_RESPONSE_BYTES = 1024 * 1024
INVALID_ORIGIN_MESSAGE = "base URL must be a valid absolute http(s) origin"


class ProbeFailure(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str):
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def health_url(base_url: str) -> str:
    origin = base_url.strip()
    if any(
        char.isspace() or ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F
        for char in origin
    ):
        raise ProbeFailure(INVALID_ORIGIN_MESSAGE)
    try:
        parsed = urllib.parse.urlsplit(origin)
        hostname = parsed.hostname
        parsed.port  # Force urllib's numeric/range validation at this trust boundary.
    except ValueError as exc:
        raise ProbeFailure(INVALID_ORIGIN_MESSAGE) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ProbeFailure(INVALID_ORIGIN_MESSAGE)
    if parsed.username is not None or parsed.password is not None:
        raise ProbeFailure("base URL must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ProbeFailure("base URL must be an origin without path, query, or fragment")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/api/health", "", ""))


def fetch_health(base_url: str, *, timeout: float) -> Dict[str, Any]:
    endpoint = health_url(base_url)
    try:
        request = urllib.request.Request(
            endpoint,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": "Volta-health-probe/1"},
        )
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            status = response.getcode()
            final_url = response.geturl()
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ProbeFailure(f"health endpoint redirect rejected ({exc.code})") from exc
        raise ProbeFailure(f"health endpoint returned HTTP {exc.code}") from exc
    except http.client.InvalidURL as exc:
        raise ProbeFailure(INVALID_ORIGIN_MESSAGE) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeFailure(f"health request failed: {type(exc).__name__}") from exc
    if status != 200:
        raise ProbeFailure(f"health endpoint returned HTTP {status}")
    if final_url != endpoint:
        raise ProbeFailure("health endpoint redirected")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ProbeFailure("health response exceeded size limit")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeFailure("health response was not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ProbeFailure("health response ok was not true")
    return payload


def parse_timestamp(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ProbeFailure("database generated_at is missing")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ProbeFailure("database generated_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProbeFailure("database generated_at must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def validate_database(
    path: Path,
    *,
    max_age_days: float,
    now: Optional[dt.datetime] = None,
    max_future_skew_minutes: float = 5.0,
) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProbeFailure("local intelligence database is missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeFailure(f"local intelligence database is unreadable: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ProbeFailure("local intelligence database root is not an object")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ProbeFailure("local intelligence database records must be a non-empty list")
    total_records = payload.get("total_records")
    if isinstance(total_records, bool) or not isinstance(total_records, int) or total_records != len(records):
        raise ProbeFailure("local intelligence database total_records does not match records")
    errors = payload.get("errors")
    if not isinstance(errors, list) or errors:
        raise ProbeFailure("local intelligence database errors must be an empty list")
    generated_at = parse_timestamp(payload.get("generated_at"))
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    age = current - generated_at
    if age.total_seconds() < -(max_future_skew_minutes * 60):
        raise ProbeFailure("local intelligence database generated_at is implausibly in the future")
    age_days = age.total_seconds() / 86400
    if age_days > max_age_days:
        raise ProbeFailure(f"local intelligence database is stale ({age_days:.2f} days)")
    return {
        "path": str(path),
        "generated_at": generated_at.isoformat(),
        "records": len(records),
        "age_days": age_days,
        "sha256": sha256_file(path),
    }


def validate_receipt(path: Path, database: Path) -> Dict[str, Any]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProbeFailure("auto-refresh receipt is missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeFailure(f"auto-refresh receipt is unreadable: {type(exc).__name__}") from exc
    if not isinstance(receipt, dict) or receipt.get("ok") is not True:
        raise ProbeFailure("auto-refresh receipt does not report success")
    if receipt.get("mode") != "real" or receipt.get("stage") != "complete":
        raise ProbeFailure("auto-refresh receipt is not a completed real run")
    promotion = receipt.get("promotion")
    if not isinstance(promotion, dict) or promotion.get("performed") is not True:
        raise ProbeFailure("auto-refresh receipt does not prove a promotion")
    commit = receipt.get("commit")
    commit_created = (
        isinstance(commit, dict)
        and commit.get("created") is True
        and isinstance(commit.get("sha"), str)
        and bool(commit.get("sha"))
    )
    no_changes = isinstance(commit, dict) and commit.get("no_changes") is True
    if not commit_created and not no_changes:
        raise ProbeFailure("auto-refresh receipt does not prove a commit or verified no-op")
    expected = receipt.get("canonical_sha256")
    actual = sha256_file(database)
    if not isinstance(expected, str) or expected != actual:
        raise ProbeFailure("auto-refresh receipt hash does not match the current database")
    return {
        "path": str(path),
        "run_id": receipt.get("run_id"),
        "finished_at": receipt.get("finished_at"),
        "canonical_sha256": actual,
    }


def notify(command_text: str, message: str, *, root: Path, timeout: float = 30.0) -> bool:
    if not command_text.strip():
        return False
    try:
        command = shlex.split(command_text)
    except ValueError:
        return False
    if not command:
        return False
    try:
        proc = subprocess.run(
            [*command, message],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=max(1.0, min(timeout, 60.0)),
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Volta API health and local intelligence freshness.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("VOLTA_HEALTH_BASE_URL", "http://127.0.0.1:8773"),
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--receipt", type=Path, help="Optionally require a successful receipt whose hash matches --db.")
    parser.add_argument("--max-db-age-days", type=float, default=10.0)
    parser.add_argument("--max-future-skew-minutes", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    if not (0 < args.timeout <= 60):
        parser.error("--timeout must be greater than 0 and at most 60 seconds")
    if args.max_db_age_days < 0:
        parser.error("--max-db-age-days must be non-negative")
    if args.max_future_skew_minutes < 0:
        parser.error("--max-future-skew-minutes must be non-negative")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    evidence: Dict[str, Any] = {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "ok": False,
        # Never persist an unvalidated operator-supplied URL: credentials or query
        # tokens in a malformed value would otherwise land in scheduled stderr logs.
        "base_url": "[unvalidated origin redacted]",
    }
    try:
        endpoint = health_url(args.base_url)
        parsed_endpoint = urllib.parse.urlsplit(endpoint)
        evidence["base_url"] = urllib.parse.urlunsplit(
            (parsed_endpoint.scheme, parsed_endpoint.netloc, "", "", "")
        )
        health = fetch_health(args.base_url, timeout=args.timeout)
        database = validate_database(
            args.db.expanduser(),
            max_age_days=args.max_db_age_days,
            max_future_skew_minutes=args.max_future_skew_minutes,
        )
        evidence["api"] = {"ok": health.get("ok") is True, "time": health.get("time")}
        evidence["database"] = database
        if args.receipt is not None:
            evidence["receipt"] = validate_receipt(args.receipt.expanduser(), args.db.expanduser())
        evidence["ok"] = True
        print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except ProbeFailure as exc:
        evidence["failure"] = str(exc)
        evidence["notification_sent"] = notify(
            os.environ.get("VOLTA_NOTIFY_CMD", ""),
            f"Volta health probe failed: {exc}",
            root=ROOT,
            timeout=args.timeout,
        )
        print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
