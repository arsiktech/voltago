#!/usr/bin/env python3
"""Systemd ExecStopPost hook: alert once after repeated refresh failures.

The host supplies a verified Hermes CLI and an explicit owner delivery target.
No credentials are stored here. Successful runs reset the failure streak silently.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from auto_refresh import atomic_json

MESSAGE = "Volta's charger data failed to refresh three times in a row. The app may be serving outdated data; the collectors need attention."


def check(state_dir: Path, target: str, hermes: str, *, service_result: str | None = None) -> int:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state_dir / "alert-state.json"
    with (state_dir / "alert.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {}
        receipt = json.loads((state_dir / "last.json").read_text()) if (state_dir / "last.json").exists() else {}
        ok = service_result == "success" if service_result is not None else receipt.get("ok") is True
        event = (receipt.get("run_id"), service_result)
        if ok:
            atomic_json(path, {"streak": 0, "alerted": False, "last_event": event, "last_ok": time.time()})
            return 0
        if state.get("last_event") == list(event) and service_result is None:
            return 0
        state["last_event"] = event
        state["streak"] = state.get("streak", 0) + 1
        if state["streak"] < 3 or state.get("alerted"):
            atomic_json(path, state)
            return 0
        # Persist the attempt before sending. An ambiguous timeout must not cause
        # another copy of the same alert on the next timer invocation.
        state.update(alerted=True, attempted_at=time.time(), delivery="attempted")
        atomic_json(path, state)
        try:
            result = subprocess.run([hermes, "send", "--to", target, "--json", MESSAGE],
                                    capture_output=True, text=True, timeout=45, check=False)
            output = json.loads(result.stdout)
            delivered = result.returncode == 0 and output.get("success") is True
            state["delivery"] = "confirmed" if delivered else "unconfirmed"
            state["message_id"] = output.get("message_id")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            delivered = False
            state["delivery"] = "unknown"
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
