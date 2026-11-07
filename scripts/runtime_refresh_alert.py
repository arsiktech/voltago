#!/usr/bin/env python3
"""Systemd ExecStopPost hook: tell a person when the refresh needs one.

Alerts after three failed runs in a row, and again about once a day while failures continue,
because a cap rejection does not clear by itself. Each alert names the failed stage and reason.
Also alerts when the retained stations that need a reviewed retirement change, on any run.

The host supplies a verified Hermes CLI and an explicit owner delivery target.
No credentials are stored here. Successful runs reset the failure streak silently.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

from auto_refresh import atomic_json

MESSAGE = "Volta's charger data failed to refresh {streak} times in a row. The app may be serving outdated data; the collectors need attention."
# The timer runs every 6 hours with up to 5 minutes of jitter, so this means every fourth run.
REPEAT_SECONDS = 23 * 3600
REASON_CHARS = 300
REVIEW_IDS_SHOWN = 5
_URL = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.I)
_SECRET = re.compile(r"(token|key|secret|password|passwd|auth\w*)(\s*[=:]\s*)\S+", re.I)
_PATH = re.compile(r"(?<![\w.])/(?:[\w.-]+/)+[\w.-]*")


def _safe(text: object) -> str:
    """Short, single-line text with no URLs, secrets or host paths."""
    clean = _SECRET.sub(r"\1\2<hidden>", _URL.sub("<url>", str(text)))
    clean = " ".join(_PATH.sub("<path>", clean).split())
    return clean if len(clean) <= REASON_CHARS else clean[:REASON_CHARS - 1] + "…"


def failure_message(streak: int, receipt: dict, service_result: str | None) -> str:
    failures = receipt.get("gate_failures") if isinstance(receipt.get("gate_failures"), list) else []
    reason = "; ".join(str(item) for item in failures) or receipt.get("error") or "no reason recorded"
    # A killed run may not have replaced the receipt, so the receipt can describe an earlier run.
    ended = service_result not in (None, "success", "exit-code")
    stage = f"the service ended with {_safe(service_result)}; last recorded stage {_safe(receipt.get('stage') or 'unknown')}" \
        if ended else f"failed at {_safe(receipt.get('stage') or 'unknown')}"
    message = f"{MESSAGE.format(streak=streak)} Run {_safe(receipt.get('run_id') or 'unknown')} {stage}: {_safe(reason)}."
    if any("above the cap" in str(item) for item in failures):
        message += " This does not clear by itself: restore the stations or add reviewed retirements."
    return message


def review_message(ids: list[str]) -> str:
    shown = ", ".join(_safe(item) for item in ids[:REVIEW_IDS_SHOWN])
    more = f" and {len(ids) - REVIEW_IDS_SHOWN} more" if len(ids) > REVIEW_IDS_SHOWN else ""
    return (f"Volta: {len(ids)} retained charger(s) need a reviewed retirement, because another row of the same source "
            f"is within 50 m and they will not retire automatically: {shown}{more}.")


def _review_ids(receipt: dict) -> list[str] | None:
    """The stations awaiting review, or None when this run did not reach that step."""
    retention = receipt.get("station_retention")
    ids = retention.get("needs_review") if isinstance(retention, dict) else None
    return sorted(str(item) for item in ids) if isinstance(ids, list) else None


def _review_due(state: dict, ids: list[str] | None, now: float) -> bool:
    if not ids:
        return False
    if ids != state.get("review_ids"):
        return True
    # An unconfirmed alert is tried again at the daily pace, never within one attempt.
    return state.get("review_delivery") != "confirmed" and now - state.get("review_attempted_at", 0) >= REPEAT_SECONDS


def check(state_dir: Path, target: str, hermes: str, *, service_result: str | None = None) -> int:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state_dir / "alert-state.json"
    with (state_dir / "alert.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {}
        receipt = json.loads((state_dir / "last.json").read_text()) if (state_dir / "last.json").exists() else {}
        ok = service_result == "success" if service_result is not None else receipt.get("ok") is True
        event = (receipt.get("run_id"), service_result)
        now = time.time()
        if not ok and state.get("last_event") == list(event) and service_result is None:
            return 0
        failure_due = False
        if ok:
            review = {key: value for key, value in state.items() if key.startswith("review_")}
            state = {"streak": 0, "alerted": False, "last_event": event, "last_ok": now, **review}
        else:
            state["last_event"] = event
            state["streak"] = state.get("streak", 0) + 1
            failure_due = state["streak"] >= 3 and (
                not state.get("alerted") or now - state.get("attempted_at", 0) >= REPEAT_SECONDS)
        ids = _review_ids(receipt)
        review_due = _review_due(state, ids, now)
        if ids == []:
            for key in ("review_ids", "review_attempted_at", "review_delivery"):
                state.pop(key, None)
        if not failure_due and not review_due:
            atomic_json(path, state)
            return 0
        # One send: the stop hook has 60 seconds, which two 45-second attempts could exceed.
        parts = []
        if failure_due:
            parts.append(failure_message(state["streak"], receipt, service_result))
            state.update(alerted=True, attempted_at=now, delivery="attempted")
        if review_due:
            parts.append(review_message(ids))
            state.update(review_ids=ids, review_attempted_at=now, review_delivery="attempted")
        # Persist the attempt before sending. An ambiguous timeout must not cause
        # another copy of the same alert on the next timer invocation.
        atomic_json(path, state)
        message_id = None
        try:
            result = subprocess.run([hermes, "send", "--to", target, "--json", "\n\n".join(parts)],
                                    capture_output=True, text=True, timeout=45, check=False)
            output = json.loads(result.stdout)
            delivered = result.returncode == 0 and output.get("success") is True
            delivery = "confirmed" if delivered else "unconfirmed"
            message_id = output.get("message_id")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            delivered = False
            delivery = "unknown"
        if failure_due:
            state.update(delivery=delivery, message_id=message_id)
        if review_due:
            state.update(review_delivery=delivery, review_message_id=message_id)
        atomic_json(path, state)
        return 0 if delivered else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--hermes", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    return check(args.state_dir, args.target, args.hermes, service_result=os.environ.get("SERVICE_RESULT"))


if __name__ == "__main__":
    raise SystemExit(main())
