#!/usr/bin/env python3
"""Run a bounded Volta Go regression suite and emit a compact evidence report.

Requires a running local server, for example:
  PORT=8773 PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -u server.py
  python3 scripts/qa_regression_suite.py --base-url http://127.0.0.1:8773
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from qa_revision import current_regression_revision  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "qa" / "regression-suite-latest.json"
MAX_OUTPUT_CHARS = 12000
JS_FILES = [
    "scripts/qa_mobile_ui.js",
    "scripts/qa_driver_readiness.js",
    "scripts/qa_first_render_budget.js",
    "scripts/qa_preview_to_live_refresh.js",
    "scripts/qa_realtime_refresh.js",
    "scripts/qa_link_handoff.js",
    "scripts/qa_cdn_blocked_ui.js",
    "scripts/qa_failure_ui.js",
    "scripts/qa_focus_walk.js",
    "scripts/qa_geolocation_distance.js",
    "scripts/qa_near_home_first_use.js",
    "scripts/qa_ax_tree_contract.js",
    "scripts/qa_screen_reader_contract.js",
    "scripts/qa_source_detail_transition_a11y.js",
    "scripts/qa_empty_state_keyboard.js",
]
BOOTSTRAP_CHECK_IDS = {
    "python-compile",
    "js-compile",
    "spa-accessibility",
    "pwa-shell",
    "source-strategy",
}
JS_CHECK_SNIPPET = """
import subprocess
import sys

node = sys.argv[1]
files = sys.argv[2:]
failures = []
for path in files:
    proc = subprocess.run([node, "--check", path], text=True, capture_output=True)
    if proc.returncode:
        failures.append((path, proc.stdout, proc.stderr))
    else:
        print(f"ok {path}")
if failures:
    for path, stdout, stderr in failures:
        print(f"FAIL {path}", file=sys.stderr)
        if stdout:
            print(stdout, file=sys.stderr)
        if stderr:
            print(stderr, file=sys.stderr)
    sys.exit(1)
"""

FIELD_PACKET_REFRESH_SNIPPET = """
import subprocess
import sys
import tempfile
from pathlib import Path

base_url = sys.argv[1]
check_links = sys.argv[2] == "true"
with tempfile.TemporaryDirectory(prefix="volta-local-field-packet-") as tmp:
    packet = Path(tmp) / "field-test-candidates.md"
    freshness = Path(tmp) / "field-packet-freshness.json"
    packet_cmd = [
        sys.executable,
        "scripts/qa_field_test_packet.py",
        "--base-url",
        base_url,
        "--out",
        str(packet),
        "--require-candidate-match",
    ]
    if check_links:
        packet_cmd.append("--check-links")
    freshness_cmd = [
        sys.executable,
        "scripts/qa_field_packet_freshness.py",
        "--base-url",
        base_url,
        "--packet",
        str(packet),
        "--out",
        str(freshness),
    ]
    for cmd in (packet_cmd, freshness_cmd):
        proc = subprocess.run(cmd, text=True, capture_output=True)
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        if proc.returncode:
            sys.exit(proc.returncode)
"""


@dataclass(frozen=True)
class Check:
    check_id: str
    description: str
    command: Sequence[str]
    timeout: int = 120
    browser: bool = False
    external: bool = False
    env: Dict[str, str] | None = None


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = max(1000, limit // 2)
    return f"{text[:half]}\n... [truncated {len(text) - limit} chars] ...\n{text[-half:]}"


def resolve_node(explicit: str | None) -> str:
    """Resolve the node binary: --node arg / VOLTA_QA_NODE env (both folded into
    `explicit` by the caller) -> PATH lookup -> bare "node" as a last resort."""
    if explicit:
        return explicit
    found = shutil.which("node")
    if found:
        return found
    return "node"


def suite_checks(base_url: str, node: str, include_browser: bool, include_external: bool) -> List[Check]:
    py = sys.executable
    checks: List[Check] = [
        Check(
            "python-compile",
            "Python syntax compile for app, ingestion, QA scripts, and tests",
            [
                py,
                "-m",
                "py_compile",
                "server.py",
                "scripts/ingest_sources.py",
                "scripts/qa_smoke.py",
                "scripts/qa_source_links.py",
                "scripts/qa_live_source_canary.py",
                "scripts/qa_field_packet_freshness.py",
                "scripts/qa_accessibility_spa.py",
                "scripts/qa_pwa_shell.py",
                "scripts/qa_regression_suite.py",
                "scripts/qa_revision.py",
                "scripts/qa_manual_proof_status.py",
                "scripts/qa_artifact_integrity.py",
                "scripts/qa_completion_audit.py",
                "tests/test_live_coverage.py",
                "tests/test_field_test_packet.py",
                "tests/test_manual_proof_status.py",
                "tests/test_artifact_integrity.py",
                "tests/test_regression_suite.py",
                "tests/test_completion_audit.py",
            ],
            timeout=60,
        ),
        Check(
            "js-compile",
            "JavaScript syntax compile for app and browser QA contracts",
            [py, "-c", JS_CHECK_SNIPPET, node, *JS_FILES],
            timeout=60,
        ),
        Check(
            "unit-tests",
            "Full dependency-free unittest suite",
            [py, "-m", "unittest", "discover", "-s", "tests", "-v"],
            # The full discover run measures ~180 s on this checkout, so the old 180 s cap
            # produced spurious timeouts (failures that were clock, not coverage).
            timeout=420,
        ),
        Check(
            "spa-accessibility",
            "SPA design-token WCAG contrast (light+dark), focus-visible, and reduced-motion guards",
            [py, "scripts/qa_accessibility_spa.py"],
            timeout=60,
        ),
        Check(
            "api-smoke",
            "Server-backed API/static smoke",
            [py, "scripts/qa_smoke.py"],
            timeout=90,
            env={"VOLTA_QA_BASE_URL": base_url},
        ),
        Check(
            "pwa-shell",
            "PWA/static shell metadata and asset responses",
            [py, "scripts/qa_pwa_shell.py"],
            timeout=60,
            env={"VOLTA_QA_BASE_URL": base_url},
        ),
        Check(
            "source-strategy",
            "Named-provider source strategy contract",
            [py, "scripts/qa_source_strategy_contract.py", base_url],
            timeout=60,
        ),
        Check(
            "manual-proof-status",
            "Manual field-test and VoiceOver proof-gate status audit",
            [py, "scripts/qa_manual_proof_status.py", "--out", "qa/manual-proof-status.json"],
            timeout=30,
        ),
        Check(
            "field-packet-freshness",
            "Ephemeral field-test candidate packet is rebuilt and checked against current API state",
            [py, "-c", FIELD_PACKET_REFRESH_SNIPPET, base_url, str(include_external).lower()],
            timeout=180,
            env={"VOLTA_QA_BASE_URL": base_url},
        ),
        Check(
            "qa-artifact-integrity",
            "QA artifact CSV/JSON/schema/count integrity audit",
            [py, "scripts/qa_artifact_integrity.py", "--out", "qa/artifact-integrity-latest.json"],
            timeout=30,
        ),
        Check(
            "completion-audit",
            "Full-goal completion audit stays incomplete until manual gates pass",
            [py, "scripts/qa_completion_audit.py", "--out", "qa/completion-audit-latest.json"],
            timeout=30,
        ),
    ]

    if include_browser:
        checks.extend(
            [
                Check(
                    "first-render",
                    "Mobile first-useful-render budget",
                    [node, "scripts/qa_first_render_budget.js", base_url],
                    timeout=45,
                    browser=True,
                ),
                Check(
                    "preview-to-live",
                    "Preview cards are replaced/augmented by live refresh",
                    [node, "scripts/qa_preview_to_live_refresh.js", base_url],
                    # The check's liveBudgetMs default is 60s (cold-cache refresh margin),
                    # plus the preview budget and settle time; the subprocess cap covers it.
                    timeout=120,
                    browser=True,
                ),
                Check(
                    "mobile-ui",
                    "Mobile route-card, source, parking, charge, trip, and broad-mode contract",
                    [node, "scripts/qa_mobile_ui.js", base_url],
                    timeout=120,
                    browser=True,
                ),
                Check(
                    "driver-readiness",
                    "Default/Open-now driver-readiness and recovery flow",
                    [node, "scripts/qa_driver_readiness.js", base_url],
                    timeout=180,
                    browser=True,
                ),
                Check(
                    "link-handoff",
                    "External source/map handoff safety",
                    [node, "scripts/qa_link_handoff.js", base_url],
                    timeout=75,
                    browser=True,
                ),
                Check(
                    "failure-ui",
                    "Live failure hides stale route cards and preserves list-safe error state",
                    [node, "scripts/qa_failure_ui.js", base_url],
                    timeout=75,
                    browser=True,
                ),
                Check(
                    "cdn-blocked",
                    "List-first fallback when map CDN is unavailable",
                    [node, "scripts/qa_cdn_blocked_ui.js", base_url],
                    timeout=75,
                    browser=True,
                ),
                Check(
                    "focus-walk",
                    "Browser-rendered focus order reaches route controls before map artifacts",
                    [node, "scripts/qa_focus_walk.js", base_url],
                    timeout=75,
                    browser=True,
                ),
                Check(
                    "screen-reader",
                    "Dynamic station-card screen-reader labels expose station-specific route facts",
                    [node, "scripts/qa_screen_reader_contract.js", base_url],
                    timeout=75,
                    browser=True,
                ),
                Check(
                    "ax-tree",
                    "Chrome accessibility-tree contract for the mobile charging flow",
                    [node, "scripts/qa_ax_tree_contract.js", base_url],
                    timeout=90,
                    browser=True,
                ),
                Check(
                    "source-detail-a11y",
                    "Source detail disclosure and live-region transition accessibility",
                    [node, "scripts/qa_source_detail_transition_a11y.js", base_url],
                    timeout=90,
                    browser=True,
                ),
                Check(
                    "empty-state-keyboard",
                    "Keyboard-only Open now empty-state recovery flow",
                    [node, "scripts/qa_empty_state_keyboard.js", base_url],
                    timeout=120,
                    browser=True,
                ),
                Check(
                    "geolocation-distance",
                    "Locate flow adds distance, drive ETA, and Trip timing to route cards",
                    [node, "scripts/qa_geolocation_distance.js", base_url],
                    timeout=90,
                    browser=True,
                ),
                Check(
                    "near-home-first-use",
                    "Cold-install near-home search, safe nearest ordering, mobile handoff, and recovery",
                    [node, "scripts/qa_near_home_first_use.js", base_url],
                    timeout=180,
                    browser=True,
                ),
                Check(
                    "price-slider",
                    "Selected charging-price caps, keyboard controls, cache separation and reset",
                    [node, "scripts/qa_price_slider.js", base_url],
                    timeout=120,
                    browser=True,
                ),
                Check(
                    "realtime-refresh",
                    "Realtime auto-refresh updates source status and charger API",
                    [node, "scripts/qa_realtime_refresh.js", base_url],
                    # The check's own script budget is 240s (60s interval + bounded
                    # live-refresh margins); the subprocess cap must cover it.
                    timeout=280,
                    browser=True,
                ),
            ]
        )

    if include_external:
        checks.extend(
            [
                Check(
                    "live-source-canary",
                    "Live/provider coverage canary with bounded JSONL history",
                    [
                        py,
                        "scripts/qa_live_source_canary.py",
                        "--base-url",
                        base_url,
                        "--history-out",
                        "qa/live-source-canary-history.jsonl",
                        "--history-keep",
                        "30",
                    ],
                    timeout=90,
                    external=True,
                ),
                Check(
                    "ingestion-canary",
                    "Non-destructive public-source intelligence rebuild and delta canary",
                    [
                        py,
                        "scripts/qa_ingestion_canary.py",
                        "--out",
                        "/tmp/volta-ingestion-canary-regression.json",
                        "--history-out",
                        "qa/ingestion-canary-history.jsonl",
                        "--history-keep",
                        "30",
                    ],
                    timeout=180,
                    external=True,
                ),
                Check(
                    "source-link-sample",
                    "Provider-capped source-link liveness sample",
                    [
                        py,
                        "scripts/qa_source_links.py",
                        "--path",
                        "/api/chargers?max_price=0.25&include_leads=true&force=true",
                        "--max-urls",
                        "14",
                        "--per-host",
                        "2",
                        "--per-provider",
                        "3",
                        "--history-out",
                        "qa/source-link-history.jsonl",
                    ],
                    timeout=180,
                    external=True,
                    env={"VOLTA_QA_BASE_URL": base_url},
                ),
                Check(
                    "source-link-history",
                    "Source-link history regression analysis",
                    [py, "scripts/qa_source_history.py", "--history", "qa/source-link-history.jsonl", "--min-entries", "3"],
                    timeout=60,
                    external=True,
                ),
            ]
        )

    return checks


def run_check(check: Check, base_env: Dict[str, str]) -> Dict[str, object]:
    env = dict(base_env)
    if check.env:
        env.update(check.env)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(check.command),
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=check.timeout,
            check=False,
        )
        duration_ms = round((time.monotonic() - started) * 1000)
        return {
            "id": check.check_id,
            "description": check.description,
            "command": list(check.command),
            "timeout_s": check.timeout,
            "duration_ms": duration_ms,
            "returncode": proc.returncode,
            "ok": proc.returncode == 0,
            "stdout": truncate(proc.stdout),
            "stderr": truncate(proc.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.monotonic() - started) * 1000)
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return {
            "id": check.check_id,
            "description": check.description,
            "command": list(check.command),
            "timeout_s": check.timeout,
            "duration_ms": duration_ms,
            "returncode": None,
            "ok": False,
            "timed_out": True,
            "stdout": truncate(stdout),
            "stderr": truncate(stderr),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("VOLTA_QA_BASE_URL", "http://127.0.0.1:8773"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--node", default=os.environ.get("VOLTA_QA_NODE"))
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright/Chrome checks.")
    parser.add_argument("--include-external", action="store_true", help="Include live upstream/source-link checks.")
    parser.add_argument(
        "--bootstrap-artifacts",
        action="store_true",
        help="Run a small explicit partial suite so stale self-referential artifacts can converge before one full run.",
    )
    parser.add_argument("--list", action="store_true", help="List checks without running them.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    node = resolve_node(args.node)
    checks = suite_checks(base_url, node, include_browser=not args.skip_browser, include_external=args.include_external)
    if args.bootstrap_artifacts:
        if not args.skip_browser or args.include_external:
            parser.error("--bootstrap-artifacts requires --skip-browser and cannot include external checks")
        checks = [check for check in checks if check.check_id in BOOTSTRAP_CHECK_IDS]

    if args.list:
        print(json.dumps([{"id": c.check_id, "description": c.description, "browser": c.browser, "external": c.external} for c in checks], indent=2))
        return 0

    env = dict(os.environ)
    env.setdefault("PYTHONPYCACHEPREFIX", "/tmp/volta-pycache")
    env["VOLTA_QA_BASE_URL"] = base_url
    root_node_modules = ROOT / "node_modules"
    if root_node_modules.exists():
        existing_node_path = env.get("NODE_PATH")
        env["NODE_PATH"] = (
            f"{root_node_modules}{os.pathsep}{existing_node_path}"
            if existing_node_path
            else str(root_node_modules)
        )

    revision_before = current_regression_revision()
    results = [run_check(check, env) for check in checks]
    revision_after = current_regression_revision()
    if revision_before != revision_after:
        results.append({
            "id": "workspace-input-stability",
            "description": "Regression inputs remain unchanged while the suite runs",
            "command": [],
            "timeout_s": 0,
            "duration_ms": 0,
            "returncode": None,
            "ok": False,
            "stdout": "",
            "stderr": "Regression source/data/QA inputs changed while checks were running; rerun from a stable workspace.",
        })
    failed = [row for row in results if not row.get("ok")]
    report = {
        "ok": not failed,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        **revision_after,
        "bootstrap_artifacts": args.bootstrap_artifacts,
        "node": node,
        "include_browser": not args.skip_browser,
        "include_external": args.include_external,
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failed_ids": [str(row["id"]) for row in failed],
        "results": results,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["ok", "base_url", "total", "passed", "failed", "failed_ids"]}, ensure_ascii=False, indent=2))
    print(f"report={out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
