#!/usr/bin/env python3
"""Prepared MapaREVE external GET client, key-gated and inactive in production.

Tariff snapshots require an exact EVSE/connector join; they are never live
availability. Keys stay out of URLs/state, redirects are refused, and every
request (including a probe or failure) consumes a durable five/hour quota.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .source_transport import SourcePolicyBlocked
    from .json_recordset import load_json_recordset
except ImportError:
    from source_transport import SourcePolicyBlocked
    from json_recordset import load_json_recordset

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "cache" / "reve_external_state.json"
BASE_URL = "https://www.mapareve.es/api/external/v1"
TARIFFS_ENDPOINT = f"{BASE_URL}/connectors/tariffs"
STATUS_ENDPOINT = f"{BASE_URL}/evses/operational_status"
LOCATIONS_ENDPOINT = f"{BASE_URL}/locations"
ALLOWED_ENDPOINTS = frozenset((TARIFFS_ENDPOINT, STATUS_ENDPOINT, LOCATIONS_ENDPOINT))
PAGE_LIMIT = 100
MAX_REQUESTS_PER_HOUR = 5
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
EVIDENCE_MAX_AGE_SECONDS = 24 * 60 * 60
DELTA_OVERLAP_SECONDS = 60
PRICE_DIMENSIONS = frozenset(("ENERGY", "FLAT", "TIME", "PARKING_TIME"))
try:
    PAGE_BUDGET = max(1, min(4, int(os.environ.get("VOLTA_REVE_PAGE_BUDGET", "4"))))
except ValueError:
    PAGE_BUDGET = 4


def api_key() -> str:
    value = os.environ.get("VOLTA_REVE_API_KEY", "")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SourcePolicyBlocked("REVE API key contains invalid control characters")
    return value.strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _timestamp(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _nonnegative(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def tariff_is_free(tariff: Dict[str, Any]) -> Optional[bool]:
    """True = unconditional zero-cost EUR; False = paid; None = not established.

    Restrictions, missing components, unknown dimensions and invalid numbers
    cannot establish free charging. Zero remains zero with or without VAT;
    invalid VAT/step sizes invalidate the tariff instead of granting free access.
    """
    if not isinstance(tariff, dict) or tariff.get("currency") != "EUR":
        return None
    if tariff.get("restrictions") not in (None, {}) or any(tariff.get(key) for key in ("start_date_time", "end_date_time")):
        return None
    if tariff.get("type") not in (None, "", "REGULAR", "AD_HOC_PAYMENT"):
        return None
    elements = tariff.get("elements")
    if isinstance(elements, dict):
        elements = [elements]
    if not isinstance(elements, list) or not elements:
        return None
    positive = False
    for element in elements:
        if not isinstance(element, dict) or element.get("restrictions") not in (None, {}):
            return None
        components = element.get("price_components")
        if not isinstance(components, list) or not components:
            return None
        for component in components:
            if not isinstance(component, dict) or component.get("type") not in PRICE_DIMENSIONS:
                return None
            price = _nonnegative(component.get("price"))
            if price is None:
                return None
            if "vat" in component and component["vat"] is not None:
                vat = _nonnegative(component["vat"])
                if vat is None or vat > 100:
                    return None
            if "step_size" in component:
                step = component["step_size"]
                if isinstance(step, bool) or not isinstance(step, int) or step <= 0:
                    return None
            positive = positive or price > 0
    for key in ("min_price", "max_price"):
        bound = tariff.get(key)
        if bound is None:
            continue
        if not isinstance(bound, dict) or not bound or set(bound) - {"excl_vat", "incl_vat"}:
            return None
        amounts = [_nonnegative(value) for value in bound.values()]
        if any(amount is None for amount in amounts):
            return None
        positive = positive or any(amount > 0 for amount in amounts)
    return not positive


def connector_free_verdict(row: Dict[str, Any]) -> Optional[bool]:
    if not isinstance(row, dict) or row.get("restrictions") not in (None, {}):
        return None
    tariffs = row.get("tariffs")
    if not isinstance(tariffs, list) or not tariffs:
        return None
    verdicts = [tariff_is_free(tariff) for tariff in tariffs]
    if any(verdict is False for verdict in verdicts):
        return False
    return True if all(verdict is True for verdict in verdicts) else None


def _empty_state() -> Dict[str, Any]:
    return {"schema_version": 2, "tariffs": {"page": 1, "date_from": None, "window_started_at": None, "connectors": {}}, "updated_at": None}


def load_state(state_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(state_path or STATE_PATH)
    if not path.exists():
        return _empty_state()
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("tariffs"), dict):
        raise ValueError("Invalid REVE mirror state; preserve it for inspection")
    if state.get("schema_version") != 2:
        # Legacy snapshots discarded raw tariffs/currency and conflated EVSE
        # and connector ids. Their positive decisions require a fresh backfill.
        return _empty_state()
    tariff_state = state["tariffs"]
    page = tariff_state.get("page")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 1_000_000:
        raise ValueError("Invalid REVE mirror page")
    if not isinstance(tariff_state.get("connectors"), dict):
        raise ValueError("Invalid REVE connector mirror")
    for key in ("date_from", "window_started_at"):
        if tariff_state.get(key) is not None and _timestamp(tariff_state[key]) is None:
            raise ValueError("Invalid REVE mirror timestamp")
    if page > 1 and tariff_state.get("window_started_at") is None:
        raise ValueError("REVE mirror pagination has no original window timestamp")
    return state


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now_iso()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=STATE_PATH.parent, prefix=".reve-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(state, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, STATE_PATH)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def reserve_request(*, now: Optional[float] = None) -> None:
    """Atomic rolling quota across processes and restarts; failures still count."""
    current = time.time() if now is None else now
    if not math.isfinite(current) or current < 0:
        raise SourcePolicyBlocked("REVE quota clock is invalid")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    quota_path = STATE_PATH.with_suffix(".quota.sqlite3")
    with sqlite3.connect(quota_path, timeout=5) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS requests (reserved_at REAL NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM requests WHERE reserved_at <= ?", (current - 3600,))
        # Future events remain charged when the local clock moves backward.
        count = connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        if count >= MAX_REQUESTS_PER_HOUR:
            raise SourcePolicyBlocked("REVE external API quota exhausted (5 requests/hour); retry later")
        connection.execute("INSERT INTO requests (reserved_at) VALUES (?)", (current,))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourcePolicyBlocked("REVE credential-bearing requests do not follow redirects")


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite REVE JSON number")


def fetch_page(endpoint: str, *, page: int, date_from: Optional[str] = None, timeout: int = 45) -> Any:
    if endpoint not in ALLOWED_ENDPOINTS:
        raise SourcePolicyBlocked("REVE request is outside the reviewed HTTPS GET endpoints")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 1_000_000:
        raise ValueError("REVE page must be a positive bounded integer")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 45:
        raise ValueError("REVE timeout must be between 0 and 45 seconds")
    key = api_key()
    if not key:
        raise SystemExit("VOLTA_REVE_API_KEY is not set; official REVE client is unconfigured")
    params: Dict[str, Any] = {"page": page, "limit": PAGE_LIMIT}
    if date_from is not None:
        parsed = _timestamp(date_from)
        if parsed is None or parsed > time.time() + 300:
            raise ValueError("REVE date_from must be a timezone-aware timestamp, not in the future")
        params["date_from"] = date_from
    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Volta official REVE read-only client"}, method="GET")
    request.add_unredirected_header("x-api-key", key)
    reserve_request()
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        if response.geturl() != url:
            raise SourcePolicyBlocked("REVE response URL changed unexpectedly")
        if response.status != 200:
            raise ValueError("REVE did not return a successful data response")
        length = response.headers.get("Content-Length")
        if length is not None and (not str(length).isdigit() or int(length) > MAX_RESPONSE_BYTES):
            raise ValueError("REVE response exceeds the byte limit")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES or (length is not None and len(body) != int(length)):
            raise ValueError("REVE response is oversized or truncated")
        if key.encode("utf-8") in body:
            raise ValueError("REVE response echoed credentials; response discarded")
        return json.loads(body.decode("utf-8"), parse_constant=_reject_constant)


def rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
    return []


def _validated_rows(payload: Any) -> List[Dict[str, Any]]:
    rows = payload if isinstance(payload, list) else None
    if isinstance(payload, dict):
        if payload.get("success") is False or payload.get("error") or payload.get("errors"):
            raise ValueError("REVE returned an error payload")
        if payload.get("status_code") is not None and payload["status_code"] not in (1000, 200):
            raise ValueError("REVE returned an unsuccessful API status")
        for key in ("data", "items", "results"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("REVE response has no valid row list; cursor not advanced")
    if len(rows) > PAGE_LIMIT:
        raise ValueError("REVE page exceeds the documented limit")
    return rows


def _connector_key(row: Dict[str, Any]) -> Optional[str]:
    identifiers = []
    for field in ("evse_id", "connector_id"):
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
            return None
        identifiers.append(str(value).strip())
    return json.dumps(identifiers, ensure_ascii=False, separators=(",", ":"))


def sync_tariffs(budget: int = PAGE_BUDGET) -> Dict[str, Any]:
    if not api_key():
        raise SystemExit("VOLTA_REVE_API_KEY is not set; request the official key via https://www.mapareve.es/api-contacto")
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= MAX_REQUESTS_PER_HOUR:
        raise ValueError("REVE per-run budget must be between 1 and 5")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourcePolicyBlocked("Another REVE mirror sync is running") from exc
        return _sync_tariffs_locked(budget)


def _sync_tariffs_locked(budget: int) -> Dict[str, Any]:
    state = load_state()
    tariff_state = state["tariffs"]
    connectors = tariff_state["connectors"]
    requests_done, new_rows, rejected_rows = 0, 0, 0
    if not tariff_state.get("window_started_at"):
        tariff_state["window_started_at"] = now_iso()
        save_state(state)
    while requests_done < budget:
        page = tariff_state["page"]
        payload = fetch_page(TARIFFS_ENDPOINT, page=page, date_from=tariff_state.get("date_from"))
        requests_done += 1
        rows = _validated_rows(payload)
        if rows and not any(_connector_key(row) is not None for row in rows):
            raise ValueError("REVE tariff rows lack exact EVSE/connector identities; cursor not advanced")
        for row in rows:
            key = _connector_key(row)
            if key is None:
                rejected_rows += 1
                continue
            tariffs = row.get("tariffs")
            currencies = sorted({str(tariff.get("currency")) for tariff in tariffs if isinstance(tariff, dict) and tariff.get("currency")}) if isinstance(tariffs, list) else []
            connectors[key] = {
                "evidence_version": 2, "evse_id": str(row["evse_id"]).strip(), "connector_id": str(row["connector_id"]).strip(),
                "free": connector_free_verdict(row), "currency": currencies[0] if len(currencies) == 1 else None,
                "last_tariff_updated": row.get("last_tariff_updated"), "fetched_at": now_iso(),
                "source_url": TARIFFS_ENDPOINT, "tariffs": tariffs, "restrictions": row.get("restrictions"),
            }
            new_rows += 1
        if len(rows) < PAGE_LIMIT:
            # The first window timestamp persists across pages AND runs: taking
            # the final page time would miss changes made during a long backfill.
            since = _timestamp(tariff_state["window_started_at"]) - DELTA_OVERLAP_SECONDS
            tariff_state["page"] = 1
            tariff_state["date_from"] = datetime.fromtimestamp(since, timezone.utc).isoformat(timespec="seconds")
            tariff_state["window_started_at"] = None
        else:
            tariff_state["page"] = page + 1
        save_state(state)
        if len(rows) < PAGE_LIMIT:
            break
    return {"status": "cached_tariff_evidence", "live_status_integrated": False,
            "requests": requests_done, "rows_seen": new_rows, "rows_rejected_missing_identity": rejected_rows,
            "connectors_tracked": len(connectors),
            "free_connectors": sum(value.get("free") is True for value in load_tariff_evidence().values()),
            "mode": "delta" if tariff_state.get("date_from") else f"backfill@page{tariff_state['page']}"}


def load_tariff_evidence(state_path: Optional[Path] = None) -> Dict[str, Any]:
    """Fresh exact-identity evidence; never trust saved free booleans or stale rows.

    Consumers must join BOTH EVSE and connector ids, never proximity.
    The optional local join annotates candidates without promoting price/status.
    """
    state = load_state(state_path)
    evidence = {}
    current = time.time()
    for value in state["tariffs"]["connectors"].values():
        if not isinstance(value, dict) or value.get("evidence_version") != 2 or value.get("source_url") != TARIFFS_ENDPOINT:
            continue
        key = _connector_key(value)
        fetched_at = _timestamp(value.get("fetched_at"))
        if key is None or fetched_at is None or not 0 <= current - fetched_at <= EVIDENCE_MAX_AGE_SECONDS:
            continue
        evidence[key] = {**value, "free": connector_free_verdict(value)}
    return evidence


def _candidate_identity(row: Dict[str, Any], connector: Dict[str, Any]) -> Optional[str]:
    """Use explicit source identities only; never split a synthetic connector id."""
    country = row.get("country_code") or row.get("country")
    if country and country != "ES":
        return None
    evse = connector.get("evse_id") or row.get("evse_id")
    if connector.get("evse_id") and row.get("evse_id") and connector["evse_id"] != row["evse_id"]:
        return None
    return _connector_key({"evse_id": evse, "connector_id": connector.get("connector_id")})


def _identity_counts(row: Dict[str, Any], counts: Counter, report: Dict[str, Any]) -> None:
    report["stations_input"] += 1
    connectors = row.get("connectors") or []
    if not isinstance(connectors, list) or any(not isinstance(item, dict) for item in connectors):
        raise ValueError("Candidate connectors must be a list of objects")
    for connector in connectors:
        report["connectors_input"] += 1
        identity = _candidate_identity(row, connector)
        if identity is None:
            report["connectors_missing_identity"] += 1
        else:
            counts[identity] += 1


def _join_report(evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {"source": "mapareve_external", "status": "pending", "generated_at": now_iso(),
            "join_rule": "exact explicit evse_id and connector_id; duplicate identities rejected",
            "fresh_evidence_connectors": len(evidence), "stations_input": 0, "connectors_input": 0,
            "connectors_missing_identity": 0, "eligible_unique_identities": 0, "ambiguous_identities": 0,
            "stations_matched": 0, "connectors_matched": 0, "evidence_unmatched": len(evidence),
            "live_status_integrated": False, "price_promotion_enabled": False, "promotion_allowed": False}


def _join_record(row: Dict[str, Any], counts: Counter, evidence: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    # Copy only modified containers; callers' original rows remain unchanged.
    result = dict(row)
    if "connectors" not in row:
        return result
    connectors, matched = [], False
    for original in row.get("connectors") or []:
        connector = dict(original)
        # A repeat join must remove expired annotations from a prior candidate.
        connector.pop("reve_tariff_evidence", None)
        identity = _candidate_identity(row, connector)
        if identity is not None and counts[identity] == 1 and identity in evidence:
            connector["reve_tariff_evidence"] = copy.deepcopy(evidence[identity])
            connector["reve_tariff_evidence"].update(
                join_method="exact_evse_and_connector", live_status_integrated=False,
                price_promotion_enabled=False,
            )
            matched = True
            report["connectors_matched"] += 1
        connectors.append(connector)
    result["connectors"] = connectors
    report["stations_matched"] += int(matched)
    return result


def _finish_join_report(report: Dict[str, Any], counts: Counter) -> None:
    report["eligible_unique_identities"] = sum(count == 1 for count in counts.values())
    report["ambiguous_identities"] = sum(count > 1 for count in counts.values())
    report["evidence_unmatched"] = report["fresh_evidence_connectors"] - report["connectors_matched"]
    report["status"] = ("annotated_candidate" if report["connectors_matched"] else
                        "unmatched_missing_identities" if not counts else
                        "no_fresh_evidence" if not report["fresh_evidence_connectors"] else "unmatched")


def join_candidate(input_path: Path, output_path: Path, state_path: Optional[Path] = None) -> Dict[str, Any]:
    """Two-pass streaming evidence annotation; no requests or canonical writes.

    The first pass detects ambiguous identity pairs across all rows. The second
    pass writes fresh annotations one row at a time and atomically replaces only
    an explicitly named candidate. Tariff/price/free/status fields are untouched.
    """
    source, output = Path(input_path).resolve(), Path(output_path).resolve()
    state = Path(state_path or STATE_PATH).resolve()
    if output in {source, state, ROOT / "data" / "free_intelligence.json"} or "candidate" not in output.name.lower():
        raise ValueError("REVE output must be a separate file with 'candidate' in its filename")
    evidence = load_tariff_evidence(state)
    report, counts = _join_report(evidence), Counter()
    original_stat = source.stat()

    def count_row(row):
        _identity_counts(row, counts, report)
        return None

    metadata = load_json_recordset(source, record_transform=count_row)
    if "records" not in metadata:
        raise ValueError("REVE input requires a records array")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, prefix=".reve-candidate-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write('{"records":[')
            first = True

            def write_row(row):
                nonlocal first
                if not first:
                    handle.write(",")
                json.dump(_join_record(row, counts, evidence, report), handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                first = False
                return None

            metadata = load_json_recordset(source, record_transform=write_row)
            current_stat = source.stat()
            if (original_stat.st_size, original_stat.st_mtime_ns) != (current_stat.st_size, current_stat.st_mtime_ns):
                raise ValueError("REVE input changed during the join; output discarded")
            _finish_join_report(report, counts)
            metadata.pop("records", None)
            metadata.update(artifact_role="candidate", promotion_allowed=False, reve_tariff_join=report)
            handle.write("]")
            for key, value in metadata.items():
                handle.write("," + json.dumps(key) + ":")
                json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.write("}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return report


def probe() -> Dict[str, Any]:
    rows = _validated_rows(fetch_page(TARIFFS_ENDPOINT, page=1))
    return {"rows_on_page_1": len(rows), "sample_free_verdicts": {_connector_key(row) or f"missing-identity-{index}": connector_free_verdict(row) for index, row in enumerate(rows[:3])}, "fetched_at": now_iso(), "live_status_integrated": False}


def configuration_status() -> Dict[str, Any]:
    return {"source": "mapareve_external", "status": "configured_not_verified" if api_key() else "unconfigured",
            "credential_env": "VOLTA_REVE_API_KEY", "key_request_url": "https://www.mapareve.es/api-contacto",
            "rate_limit_per_hour": MAX_REQUESTS_PER_HOUR, "live_status_integrated": False, "production_ingestion_enabled": False,
            "candidate_tariff_join_available": True, "candidate_join_requires": ["explicit evse_id", "explicit connector_id", "fresh tariff mirror"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Official REVE GET client; key-gated, durably limited to 5 requests/hour.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--probe", action="store_true", help="One bounded request to prove access.")
    modes.add_argument("--sync", action="store_true", help="Advance the tariff evidence mirror.")
    modes.add_argument("--status", action="store_true", help="Configuration only; no network request.")
    modes.add_argument("--join-input", type=Path, help="Locally annotate this recordset using exact EVSE/connector identities; no network.")
    parser.add_argument("--join-output", type=Path, help="Separate *.candidate.json output for --join-input.")
    parser.add_argument("--state-path", type=Path, help="Mirror path, primarily for a local candidate join.")
    args = parser.parse_args()
    if bool(args.join_input) != bool(args.join_output):
        parser.error("--join-input and --join-output must be used together")
    global STATE_PATH
    if args.state_path:
        STATE_PATH = args.state_path
    if not (args.probe or args.sync or args.status or args.join_input):
        parser.print_help()
        return
    try:
        result = (join_candidate(args.join_input, args.join_output, args.state_path) if args.join_input else
                  probe() if args.probe else sync_tariffs() if args.sync else configuration_status())
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Upstream HTTP exception text/body can contain credentials; expose the
        # type only, never request objects, headers or server-controlled text.
        print(json.dumps({"source": "mapareve_external", "status": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
