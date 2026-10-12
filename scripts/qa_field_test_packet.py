#!/usr/bin/env python3
"""Generate a current field-test candidate packet from the live Volta API."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo


DEFAULT_BASE_URL = "http://127.0.0.1:8773"
ROOT = Path(__file__).resolve().parents[1]
CANONICAL_INTEL = ROOT / "data" / "free_intelligence.json"
LOCAL_ZONE = ZoneInfo("Europe/Madrid")
API_USER_AGENT = "Volta-Go-QA/1.0 (+https://voltago.app)"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.source_policy import recordset_sha256  # noqa: E402


def candidate_revision(base_url: str, timeout: float, canonical_path: Path = CANONICAL_INTEL) -> Dict[str, Any]:
    expected_payload = json.loads(canonical_path.read_text(encoding="utf-8"))
    deployed_payload = fetch_json(base_url, "/api/intel", timeout)
    expected_records = expected_payload.get("records") or []
    expected = recordset_sha256(expected_records)
    stored_expected = expected_payload.get("recordset_sha256")
    canonical_hash_valid = not stored_expected or stored_expected == expected
    deployed = deployed_payload.get("recordset_sha256")
    return {
        "expected_recordset_sha256": expected,
        "stored_expected_recordset_sha256": stored_expected,
        "canonical_recordset_sha256_valid": canonical_hash_valid,
        "deployed_recordset_sha256": deployed,
        "match": canonical_hash_valid and bool(deployed) and expected == deployed,
        "expected_records": len(expected_records),
        "deployed_records": deployed_payload.get("recordset_records") or len(deployed_payload.get("records") or []),
    }


def fetch_json(base_url: str, path: str, timeout: float) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": API_USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_json_with_fallback(base_url: str, force_path: str, fallback_path: str, timeout: float) -> Dict[str, Any]:
    try:
        return fetch_json(base_url, force_path, timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return fetch_json(base_url, fallback_path, timeout)


def synthesize_open_now_payload(default_payload: Dict[str, Any], error: Exception) -> Dict[str, Any]:
    rows = collect_rows(default_payload)
    route_ready = [row for row in rows if is_route_ready(row)]
    return {
        "stations": [row for row in route_ready if not row.get("lead")],
        "leads": [],
        "warning": f"Open now endpoint failed; derived from default payload. {compact(error)}",
        "meta": {
            "coverage": {
                "mode": "degraded",
                "is_degraded": True,
                "warning": f"Open now endpoint failed; derived from default payload. {compact(error)}",
            }
        },
    }


def check_url(url: str, timeout: float) -> Dict[str, Any]:
    # Defense in depth: upstream feeds occasionally leak scheme-less values ("WWW.CARREFOUR.ES");
    # urlopen raises ValueError on those and would kill the whole packet run.
    if not str(url or "").lower().startswith(("http://", "https://")):
        return {"url": url, "method": None, "status": None, "reachable": False, "error": f"not an http(s) url: {url!r}"}
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method, headers={"User-Agent": "Volta-Go-QA/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(response.status)
                return {
                    "url": url,
                    "method": method,
                    "status": status,
                    "reachable": 200 <= status < 400 or status in {401, 403},
                    "error": None,
                }
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            # Some valid public pages (notably OSM) intermittently reject/rate-limit HEAD
            # while a normal read-only GET succeeds. Verify the page with GET before
            # declaring the field packet's source link dead.
            if method == "HEAD" and status in {405, 429, 500, 501, 502, 503, 504}:
                continue
            return {
                "url": url,
                "method": method,
                "status": status,
                "reachable": status in {401, 403},
                "transient": status in {429, 500, 502, 503, 504},
                "error": str(exc),
            }
        except urllib.error.URLError as exc:
            if method == "HEAD":
                continue
            return {
                "url": url,
                "method": method,
                "status": None,
                "reachable": False,
                "error": str(exc),
            }
    return {"url": url, "method": None, "status": None, "reachable": False, "error": "not checked"}


def compact(value: Any) -> str:
    return " ".join(str(value or "").split())


RAW_TECHNICAL_WARNING_RE = re.compile(
    r"\b(?:HTTPError|HTTP Error|Traceback|TypeError|Exception|API \d{3}|Bad Gateway|Too Many Requests)\b",
    re.IGNORECASE,
)


def warning_for_packet(value: Any) -> str:
    """Keep field packets driver-readable when upstream refreshes fail."""
    text = compact(value)
    had_raw_failure = bool(RAW_TECHNICAL_WARNING_RE.search(text))
    replacements = (
        (
            "Live refresh failed; serving stale cache from a broader filter and reapplying current route filters.",
            "Live refresh limited; stale cached data from a broader filter was rechecked against current route filters.",
        ),
        (
            "Live refresh failed; serving stale cache.",
            "Live refresh limited; stale cached data used.",
        ),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(r"HTTPError:\s*HTTP Error \d{3}:\s*(?:Too Many Requests|Bad Gateway)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"HTTP Error \d{3}:\s*(?:Too Many Requests|Bad Gateway)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:Traceback|TypeError|Exception|Bad Gateway|Too Many Requests)\b:?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bAPI \d{3}\b", "live source failure", text, flags=re.IGNORECASE)
    text = compact(text.strip(" ;."))
    if had_raw_failure and not any(token in text.lower() for token in ("low live coverage", "refresh or widen", "provider-source verification", "verify")):
        text = compact(f"{text}. Verify source before driving." if text else "Live refresh limited; verify source before driving.")
    return text


def is_route_ready(station: Dict[str, Any]) -> bool:
    decision = station.get("decision") or {}
    return (
        not station.get("lead")
        and decision.get("level") == "go"
        and int(station.get("available") or 0) > 0
        and bool(station.get("tesla_ok"))
    )


def is_easy_parking(station: Dict[str, Any]) -> bool:
    parking = station.get("parking") or {}
    kind = parking.get("kind") or "unknown"
    return not parking.get("paid") and kind not in {"paid_or_metered", "time_limited_or_validated", "unknown"}


def station_blob(station: Dict[str, Any]) -> str:
    pieces = [
        station.get("name"),
        station.get("source"),
        station.get("source_label"),
        compact(", ".join(station.get("connector_labels") or [])),
        (station.get("owner") or {}).get("name") if isinstance(station.get("owner"), dict) else None,
        (station.get("charge_claim") or {}).get("source"),
        (station.get("charge_claim") or {}).get("summary"),
    ]
    for source in station.get("sources") or []:
        pieces.extend([source.get("name"), source.get("url"), source.get("evidence")])
    primary = station.get("primary_source") or {}
    pieces.extend([primary.get("name"), primary.get("url"), primary.get("label")])
    return " ".join(compact(part).lower() for part in pieces if part)


def is_supercharger_like(station: Dict[str, Any]) -> bool:
    blob = station_blob(station)
    return (
        "supercharger" in blob
        or "supercargador" in blob
        or "operator=tesla" in blob
        or "operator tesla" in blob
    )


def has_official_tesla_tariff_evidence(station: Dict[str, Any]) -> bool:
    if not is_supercharger_like(station):
        return False
    price = station.get("price_min")
    low_price = isinstance(price, (int, float)) and price <= 0.25
    if not (station.get("is_free") or low_price):
        return False
    official_sources = []
    primary = station.get("primary_source") or {}
    if primary:
        official_sources.append(primary)
    official_sources.extend(station.get("sources") or [])
    for source in official_sources:
        text = f"{source.get('name') or ''} {source.get('url') or ''} {source.get('evidence') or ''}".lower()
        if "tesla.com" in text and ("price" in text or "tariff" in text or "free" in text or "€" in text):
            return True
    return False


def has_unofficial_supercharger_tariff_risk(station: Dict[str, Any]) -> bool:
    return is_supercharger_like(station) and not has_official_tesla_tariff_evidence(station)


def is_trusted_backup(station: Dict[str, Any]) -> bool:
    return (
        bool(station.get("lead"))
        and bool(station.get("tesla_ok"))
        and is_easy_parking(station)
        and not has_unofficial_supercharger_tariff_risk(station)
    )


def station_score(station: Dict[str, Any]) -> Tuple[int, float, str]:
    decision = station.get("decision") or {}
    source = station.get("primary_source") or {}
    parking = station.get("parking") or {}
    live_bonus = 1000 if is_route_ready(station) else 0
    exact_bonus = 160 if source.get("quality") == "exact" else 0
    parking_bonus = 120 if is_easy_parking(station) else 0
    free_bonus = 80 if station.get("is_free") or (station.get("price_min") == 0) else 0
    power = float(station.get("max_power_kw") or 0)
    decision_bonus = {"go": 240, "check": 80, "verify": 30}.get(decision.get("level"), 0)
    parking_penalty = 120 if parking.get("paid") else 0
    tariff_risk_penalty = 220 if has_unofficial_supercharger_tariff_risk(station) else 0
    return (live_bonus + exact_bonus + parking_bonus + free_bonus + decision_bonus - parking_penalty - tariff_risk_penalty, power, compact(station.get("name")))


def collect_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in payload.get("stations") or []:
        row = dict(row)
        row.setdefault("lead", False)
        rows.append(row)
    for row in payload.get("leads") or []:
        row = dict(row)
        row["lead"] = True
        rows.append(row)
    return rows


def format_link(label: str, url: str | None) -> str:
    if not url:
        return "missing"
    safe = str(url).replace(")", "%29")
    return f"[{label}]({safe})"


def source_options(station: Dict[str, Any], limit: int = 4) -> List[Tuple[str, str]]:
    seen = set()
    options: List[Tuple[str, str]] = []
    primary = station.get("primary_source") or {}
    if primary.get("url"):
        options.append((primary.get("action") or primary.get("label") or primary.get("name") or "Primary source", primary["url"]))
        seen.add(primary["url"])
    for source in station.get("sources") or []:
        url = source.get("url")
        if url and url not in seen:
            options.append((source.get("name") or "Source", url))
            seen.add(url)
        if len(options) >= limit:
            break
    return options


def field_row(station: Dict[str, Any], index: int, link_checks: Dict[str, Dict[str, Any]] | None = None) -> str:
    parking = station.get("parking") or {}
    decision = station.get("decision") or {}
    source = station.get("primary_source") or {}
    nav = station.get("navigation") or {}
    connectors = station.get("connector_summary") or {}
    charge_claim = station.get("charge_claim") or {}
    parking_claim = station.get("parking_claim") or {}
    options = source_options(station)
    source_lines = "\n".join(
        f"  - {format_link(label, url)}{link_check_text(link_checks or {}, url)}"
        for label, url in options
    ) or "  - missing"
    connector_details = connectors.get("details") or []
    connector_text = "; ".join(connector_details[:3]) or compact(", ".join(station.get("connector_labels") or [])) or "unknown"
    tariff_warning = ""
    if has_unofficial_supercharger_tariff_risk(station):
        tariff_warning = "- Tesla tariff warning: **Do not treat this as a free/cheap backup until the Tesla app or official Tesla source confirms the active price for this vehicle/account.**"
    return "\n".join(
        [line for line in [
            f"### {index}. {compact(station.get('name'))}",
            "",
            f"- Decision: **{compact(decision.get('label') or decision.get('level') or 'Verify')}** - {compact(decision.get('reason'))}",
            f"- Location: {compact(station.get('city')) or 'unknown'} - `{station.get('lat')}, {station.get('lon')}`",
            f"- Charging price: **{compact(station.get('price_label') or station.get('price_min') or 'verify')}** - claim: {compact(charge_claim.get('summary')) or 'verify in source'}",
            f"- Parking: **{compact(parking.get('label') or parking.get('kind') or 'unknown')}** - paid={bool(parking.get('paid'))} - {compact(parking.get('note'))}",
            f"- Parking claim: {compact(parking_claim.get('summary')) or 'verify onsite'}",
            f"- Connectors: {connector_text}",
            f"- Power: {station.get('max_power_kw') or 'unknown'} kW - Tesla OK: {bool(station.get('tesla_ok'))} - live available: {station.get('available') or 0}",
            tariff_warning,
            f"- Source quality: {compact(source.get('label') or source.get('kind'))} - {compact(source.get('quality')) or 'unknown'} - action: {compact(source.get('action')) or 'Check source'}",
            "- Source links:",
            source_lines,
            f"- Navigation: {format_link('Google Maps', nav.get('google'))} - {format_link('Apple Maps', nav.get('apple'))}",
            "- On-site checks: parking signs/gates/time limits, same provider/source station, Tesla connector fit, actual tariff before start, charge-start success.",
        ] if line]
    )


def link_check_text(link_checks: Dict[str, Dict[str, Any]], url: str) -> str:
    result = link_checks.get(url)
    if not result:
        return ""
    status = result.get("status")
    if result.get("reachable"):
        return f" - link ok {status or 'reachable'} via {result.get('method')}"
    return f" - LINK CHECK FAILED {status or result.get('error') or 'unknown'}"


def render_packet(
    base_url: str,
    default_payload: Dict[str, Any],
    open_now_payload: Dict[str, Any],
    live_candidates: List[Dict[str, Any]],
    backup_candidates: List[Dict[str, Any]],
    broad_watchlist: List[Dict[str, Any]],
    link_checks: Dict[str, Dict[str, Any]] | None = None,
    revision: Dict[str, Any] | None = None,
) -> str:
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(LOCAL_ZONE)
    default_rows = collect_rows(default_payload)
    supercharger_tariff_risk_count = sum(1 for row in default_rows if has_unofficial_supercharger_tariff_risk(row))
    warning = warning_for_packet(default_payload.get("warning") or (default_payload.get("meta") or {}).get("coverage", {}).get("warning"))
    open_warning = warning_for_packet(open_now_payload.get("warning") or (open_now_payload.get("meta") or {}).get("coverage", {}).get("warning"))
    link_checks = link_checks or {}
    checked_count = len(link_checks)
    failed_count = sum(1 for row in link_checks.values() if not row.get("reachable"))
    revision_provided = revision is not None
    revision = revision or {}
    expected_sha = revision.get("expected_recordset_sha256") or "unknown"
    deployed_sha = revision.get("deployed_recordset_sha256") or "unknown"
    revision_match = revision.get("match") is True
    rows_withheld = revision_provided and not revision_match
    if rows_withheld:
        link_checks = {}
        checked_count = 0
        failed_count = 0
        withheld = (
            "_Candidate rows and source links are withheld because the deployed recordset does not match "
            "the local canonical candidate. Deploy the matching revision, regenerate this packet, and only then depart for TC-051._"
        )
        live_block = backup_block = watch_block = withheld
    else:
        live_block = "\n\n".join(field_row(station, i + 1, link_checks) for i, station in enumerate(live_candidates)) or "_No live route-ready candidate is confirmed right now. Use the backup section only after provider/source verification._"
        backup_block = "\n\n".join(field_row(station, i + 1, link_checks) for i, station in enumerate(backup_candidates)) or "_No trusted backup leads were available from the current API response._"
        watch_block = "\n\n".join(field_row(station, i + 1, link_checks) for i, station in enumerate(broad_watchlist)) or "_No additional high-power watchlist candidates selected._"
    return "\n".join(
        [
            "# Volta Go Field-Test Candidate Packet",
            "",
            f"Generated local: {now_local.isoformat(timespec='seconds')} (Europe/Madrid)",
            f"Generated UTC: {now_utc.isoformat(timespec='seconds')}",
            "",
            f"Base URL: `{base_url}`",
            f"Expected local recordset SHA-256: `{expected_sha}`",
            f"Deployed recordset SHA-256: `{deployed_sha}`",
            f"Candidate revision match: **{'yes' if revision_match else 'no'}**",
            (
                "Candidate status: **departure-ready revision match**"
                if revision_match
                else "Candidate status: **pre-publication or mismatched deployment — do not use for TC-051 departure**"
            ),
            "",
            "## Current API State",
            "",
            f"- Default response: {len(default_payload.get('stations') or [])} live stations, {len(default_payload.get('leads') or [])} source-backed leads.",
            f"- Open now response: {len(open_now_payload.get('stations') or [])} live stations, {len(open_now_payload.get('leads') or [])} leads.",
            f"- Default warning: {warning or 'none'}",
            f"- Open now warning: {open_warning or 'none'}",
            f"- Total default rows inspected: {len(default_rows)}",
            f"- Tesla/Supercharger tariff-risk leads excluded from trusted backups: {supercharger_tariff_risk_count}",
            f"- Source links checked: {checked_count}; failed: {failed_count}",
            "",
            "## How To Use This Packet",
            "",
            "1. Start with **Live Route-Ready Candidates**. These are the only entries Volta currently treats as direct drive candidates.",
            "2. Open the provider/source link from the Volta card before driving. Provider app/page and onsite signage override this packet.",
            "3. Use **Trusted Backup Leads** only when Open now is empty/unusable or the live candidate is occupied.",
            "4. Before leaving, capture the Volta card price, parking, source quality, live/lead status, and Source details disclosure.",
            "5. Capture the remaining evidence listed in `qa/field-test-checklist.md`.",
            "",
            "## Live Route-Ready Candidates",
            "",
            live_block,
            "",
            "## Trusted Backup Leads",
            "",
            backup_block,
            "",
            "## Fast Or Strategic Watchlist",
            "",
            watch_block,
            "",
            "## Field-Test Result Table",
            "",
            "| Candidate | Volta disclosed price/parking/source/live? | Source opened from Volta card? | Provider page matched? | Parking free/acceptable? | Tesla connector fit? | Tariff <= EUR0.25/kWh? | Charge started? | Mismatch/defect |",
            "|---|---|---|---|---|---|---|---|---|",
            "|  |  |  |  |  |  |  |  |  |",
            "",
            "## Stop Conditions",
            "",
            "- Provider/source price is above EUR0.25/kWh and no free window is active.",
            "- Parking requires paid access that was not already disclosed.",
            "- Station location/source page does not match the physical charger.",
            "- Connector is occupied, incompatible, broken, blocked, or requires unavailable account/payment setup.",
            "",
        ]
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--out", default="qa/field-test-candidates.md")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--link-timeout", type=float, default=8)
    parser.add_argument("--check-links", action="store_true", help="HEAD/GET-check provider/source links for selected candidates")
    parser.add_argument(
        "--require-candidate-match",
        action="store_true",
        help="Fail unless /api/intel matches the current local canonical recordset.",
    )
    parser.add_argument("--backups", type=int, default=5)
    parser.add_argument("--watchlist", type=int, default=3)
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        default_payload = fetch_json_with_fallback(
            args.base_url,
            "/api/chargers?max_price=0.25&include_leads=true&force=true",
            "/api/chargers?max_price=0.25&include_leads=true",
            args.timeout,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "base_url": args.base_url}, indent=2), file=sys.stderr)
        return 1

    default_rows = collect_rows(default_payload)
    try:
        open_now_payload = fetch_json_with_fallback(
            args.base_url,
            "/api/chargers?max_price=0.25&include_leads=false&available=true&fast=false&force=true",
            "/api/chargers?max_price=0.25&include_leads=false&available=true&fast=false",
            args.timeout,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        open_now_payload = synthesize_open_now_payload(default_payload, exc)
    open_rows = collect_rows(open_now_payload)
    live_candidates = sorted([row for row in open_rows if is_route_ready(row)], key=station_score, reverse=True)
    trusted_backups = sorted(
        [
            row
            for row in default_rows
            if is_trusted_backup(row)
        ],
        key=station_score,
        reverse=True,
    )[: args.backups]
    selected_ids = {row.get("id") for row in live_candidates + trusted_backups}
    watchlist = sorted(
        [
            row
            for row in default_rows
            if row.get("id") not in selected_ids and bool(row.get("tesla_ok")) and float(row.get("max_power_kw") or 0) >= 50
        ],
        key=station_score,
        reverse=True,
    )[: args.watchlist]

    try:
        revision = candidate_revision(args.base_url, args.timeout)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        revision = {
            "match": False,
            "error": f"{type(exc).__name__}",
        }

    # Never persist or probe candidate/source details from a deployment that is not
    # the exact canonical recordset under test. The packet remains a useful revision
    # receipt, but not a stale route plan.
    rows_withheld = revision.get("match") is not True
    if rows_withheld:
        live_candidates = []
        trusted_backups = []
        watchlist = []

    link_checks: Dict[str, Dict[str, Any]] = {}
    if args.check_links and not rows_withheld:
        urls: List[str] = []
        for station in live_candidates + trusted_backups + watchlist:
            for _, url in source_options(station):
                if url not in urls:
                    urls.append(url)
        for url in urls:
            link_checks[url] = check_url(url, args.link_timeout)

    packet = render_packet(
        args.base_url,
        default_payload,
        open_now_payload,
        live_candidates,
        trusted_backups,
        watchlist,
        link_checks,
        revision,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(packet, encoding="utf-8")

    summary = {
        "ok": True,
        "out": str(out_path),
        "base_url": args.base_url,
        "live_candidates": len(live_candidates),
        "backup_candidates": len(trusted_backups),
        "watchlist_candidates": len(watchlist),
        "default_stations": len(default_payload.get("stations") or []),
        "default_leads": len(default_payload.get("leads") or []),
        "open_now_stations": len(open_now_payload.get("stations") or []),
        "open_now_leads": len(open_now_payload.get("leads") or []),
        "warning": warning_for_packet(default_payload.get("warning")),
        "checked_links": len(link_checks),
        "failed_links": sum(1 for row in link_checks.values() if not row.get("reachable")),
        "candidate_revision_match": revision.get("match") is True,
        "candidate_rows_withheld": rows_withheld,
        "expected_recordset_sha256": revision.get("expected_recordset_sha256"),
        "deployed_recordset_sha256": revision.get("deployed_recordset_sha256"),
        "transient_link_failures": sum(
            1 for row in link_checks.values()
            if not row.get("reachable") and row.get("transient")
        ),
        "hard_failed_links": sum(
            1 for row in link_checks.values()
            if not row.get("reachable") and not row.get("transient")
        ),
    }
    if summary["failed_links"]:
        summary["link_failures"] = [row for row in link_checks.values() if not row.get("reachable")]
    if summary["hard_failed_links"]:
        summary["ok"] = False
    if not rows_withheld and not live_candidates and not trusted_backups:
        summary["ok"] = False
        summary["failure"] = "no live route-ready candidates and no trusted backup leads"
    if args.require_candidate_match and not summary["candidate_revision_match"]:
        summary["ok"] = False
        summary["failure"] = "deployed /api/intel recordset does not match the local canonical candidate"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
