#!/usr/bin/env python3
"""Bounded liveness sampler for Volta Go source/provider links.

This intentionally samples a small, diverse set of source URLs instead of
hammering every third-party provider page.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple


BASE_URL = os.environ.get("VOLTA_QA_BASE_URL", "http://127.0.0.1:8773").rstrip("/")
UA = "Mozilla/5.0 (Volta Go QA source-link sampler; bounded liveness check)"
OK_STATUSES = set(range(200, 400)) | {401, 403, 405}


def fetch_json(path: str, timeout: int = 45) -> Dict[str, Any]:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=timeout) as response:
        if response.status != 200:
            raise AssertionError(f"{path} returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def hostname(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def provider_name(source: str, url: str) -> str:
    text = f"{source} {url}".lower()
    if "gaiacharge" in text:
        return "Gaia"
    if "electromaps" in text:
        return "Electromaps"
    if "openchargemap" in text:
        return "OpenChargeMap"
    if "chargemap" in text:
        return "Chargemap"
    if "openstreetmap" in text:
        return "OpenStreetMap"
    if "mapareve" in text:
        return "MapaREVE"
    if "consum" in text:
        return "Consum"
    if "lidl" in text:
        return "Lidl"
    if "tesla" in text:
        return "Tesla"
    if "valencia" in text or "arcgis" in text:
        return "Municipal/official"
    return source.strip() or "Unknown"


def collect_candidates(rows: List[Dict[str, Any]], max_urls: int, per_host: int = 1, per_provider: int = 3) -> List[Dict[str, str]]:
    host_counts: Dict[str, int] = {}
    provider_counts: Dict[str, int] = {}
    seen_urls: set[str] = set()
    candidates: List[Dict[str, str]] = []
    for row in rows:
        source_rows = list(row.get("sources") or [])
        if row.get("source_url"):
            source_rows.insert(0, {"name": row.get("source_label") or row.get("source") or "Source", "url": row.get("source_url")})
        for src in source_rows:
            url = str(src.get("url") or "").strip()
            if not url.startswith(("https://", "http://")):
                continue
            host = hostname(url)
            if not host or url in seen_urls:
                continue
            source = str(src.get("name") or row.get("source") or "Source")
            provider = provider_name(source, url)
            if host_counts.get(host, 0) >= per_host:
                continue
            if provider_counts.get(provider, 0) >= per_provider:
                continue
            seen_urls.add(url)
            host_counts[host] = host_counts.get(host, 0) + 1
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            candidates.append({
                "host": host,
                "provider": provider,
                "url": url,
                "source": source,
                "station": str(row.get("name") or row.get("id") or "station"),
            })
            if len(candidates) >= max_urls:
                return candidates
    return candidates


def request_url(url: str, method: str, timeout: int) -> Tuple[str, int | None, str | None]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "text/html,application/json,*/*"})
    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=context) as response:
            return "ok", response.status, None
    except urllib.error.HTTPError as exc:
        if exc.code in OK_STATUSES:
            return "ok", exc.code, None
        return "bad_status", exc.code, str(exc)
    except Exception as exc:  # noqa: BLE001 - keep QA evidence explicit.
        return "error", None, f"{type(exc).__name__}: {exc}"


def probe(candidate: Dict[str, str], timeout: int) -> Dict[str, Any]:
    status, code, error = request_url(candidate["url"], "HEAD", timeout)
    if status == "ok":
        return {**candidate, "reachable": True, "status": code, "method": "HEAD", "error": None}
    if code == 405 or status == "error":
        status, code, error = request_url(candidate["url"], "GET", timeout)
    reachable = status == "ok"
    return {**candidate, "reachable": reachable, "status": code, "method": "GET", "error": error if not reachable else None}


def summarize(results: List[Dict[str, Any]], rows: int, base_url: str, path: str) -> Dict[str, Any]:
    failures = [r for r in results if not r.get("reachable")]
    by_provider: Dict[str, Dict[str, int]] = {}
    by_host: Dict[str, Dict[str, int]] = {}
    for result in results:
        provider = result.get("provider") or "Unknown"
        host = result.get("host") or "unknown"
        for bucket, key in ((by_provider, provider), (by_host, host)):
            bucket.setdefault(key, {"sampled": 0, "reachable": 0, "failed": 0})
            bucket[key]["sampled"] += 1
            if result.get("reachable"):
                bucket[key]["reachable"] += 1
            else:
                bucket[key]["failed"] += 1
    return {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "base_url": base_url,
        "path": path,
        "rows": rows,
        "sampled": len(results),
        "reachable": sum(1 for r in results if r.get("reachable")),
        "failed": len(failures),
        "by_provider": by_provider,
        "by_host": by_host,
    }


def trim_history(path: Path, keep: int) -> None:
    if keep <= 0 or not path.exists():
        return
    rows = path.read_text(encoding="utf-8").splitlines()
    if len(rows) <= keep:
        return
    path.write_text("\n".join(rows[-keep:]) + "\n", encoding="utf-8")


def append_history(path: str, summary: Dict[str, Any], results: List[Dict[str, Any]], keep: int = 30) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    entry = {**summary, "results": results}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    trim_history(target, keep)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-urls", type=int, default=int(os.environ.get("VOLTA_SOURCE_LINK_SAMPLE", "12")))
    parser.add_argument("--per-host", type=int, default=int(os.environ.get("VOLTA_SOURCE_LINK_PER_HOST", "1")))
    parser.add_argument("--per-provider", type=int, default=int(os.environ.get("VOLTA_SOURCE_LINK_PER_PROVIDER", "3")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("VOLTA_SOURCE_LINK_TIMEOUT", "8")))
    parser.add_argument("--path", default="/api/chargers?max_price=0.25&include_leads=true")
    parser.add_argument("--history-out", default=os.environ.get("VOLTA_SOURCE_LINK_HISTORY", ""))
    parser.add_argument("--history-keep", type=int, default=int(os.environ.get("VOLTA_SOURCE_LINK_HISTORY_KEEP", "30")))
    args = parser.parse_args()

    failures: List[str] = []
    data = fetch_json(args.path)
    rows = (data.get("stations") or []) + (data.get("leads") or [])
    candidates = collect_candidates(rows, max(1, args.max_urls), max(1, args.per_host), max(1, args.per_provider))
    if not candidates:
        failures.append("no source URLs available to sample")
    results = [probe(candidate, args.timeout) for candidate in candidates]
    unreachable = [r for r in results if not r.get("reachable")]
    for row in unreachable:
        failures.append(f"{row['provider']} / {row['host']} unreachable via sampled source URL: {row.get('status')} {row.get('error')}")
    summary = summarize(results, len(rows), BASE_URL, args.path)
    if args.history_out:
        append_history(args.history_out, summary, results, keep=args.history_keep)

    output = {
        "ok": not failures,
        "base_url": BASE_URL,
        "rows": len(rows),
        "sampled": len(results),
        "reachable": sum(1 for r in results if r.get("reachable")),
        "failures": failures,
        "history_out": args.history_out or None,
        "history_keep": args.history_keep if args.history_out else None,
        "summary": summary,
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
