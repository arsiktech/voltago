#!/usr/bin/env python3
"""End-to-end safety contracts for the unattended intelligence refresh."""
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "auto_refresh.py"


def load_auto_refresh():
    spec = importlib.util.spec_from_file_location("auto_refresh", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run(cmd: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class RefreshRepo:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "data" / "cache").mkdir(parents=True)
        (root / "qa").mkdir()
        (root / "scripts").mkdir()
        (root / ".gitignore").write_text(
            ".volta-auto-refresh-dedicated\ndata/cache/\n",
            encoding="utf-8",
        )
        (root / ".volta-auto-refresh-dedicated").write_text("dedicated checkout\n", encoding="utf-8")
        (root / "README.md").write_text("fixture\n", encoding="utf-8")
        (root / "data" / "free_intelligence.json").write_text(json.dumps({
            "generated_at": "2026-07-01T00:00:00+00:00",
            "total_records": 1,
            "raw_records": 1,
            "errors": [],
            "records": [{"id": "one"}],
        }) + "\n", encoding="utf-8")
        (root / "qa" / "ingestion-canary-history.jsonl").write_text(
            json.dumps({"ok": True, "records": 1}) + "\n",
            encoding="utf-8",
        )
        initial_db_sha = hashlib.sha256((root / "data" / "free_intelligence.json").read_bytes()).hexdigest()
        for report_name in ("free-db-completeness-latest.json", "valencia-source-audit-latest.json"):
            (root / "qa" / report_name).write_text(
                json.dumps({
                    "ok": True,
                    "data": "data/free_intelligence.json",
                    "data_sha256": initial_db_sha,
                    "refresh_seq": 0,
                }) + "\n",
                encoding="utf-8",
            )
        (root / "data" / "source_policy_exclusions.json").write_text(
            json.dumps({"version": 1, "exclusions": []}) + "\n",
            encoding="utf-8",
        )
        self._write_fake_scripts()
        init = run(["git", "init", "-b", "main"], root)
        if init.returncode != 0:
            run(["git", "init"], root)
            run(["git", "checkout", "-b", "main"], root)
        run(["git", "config", "user.email", "refresh-tests@example.invalid"], root)
        run(["git", "config", "user.name", "Refresh Tests"], root)
        run(["git", "add", "."], root)
        committed = run(["git", "commit", "-m", "fixture"], root)
        if committed.returncode != 0:
            raise AssertionError(committed.stderr or committed.stdout)
        self.remote = root / "data" / "cache" / "origin.git"
        initialized_remote = run(["git", "init", "--bare", str(self.remote)], root)
        if initialized_remote.returncode != 0:
            raise AssertionError(initialized_remote.stderr or initialized_remote.stdout)
        if run(["git", "remote", "add", "origin", str(self.remote)], root).returncode != 0:
            raise AssertionError("could not add fixture origin")
        seeded = run(["git", "push", "-u", "origin", "main"], root)
        if seeded.returncode != 0:
            raise AssertionError(seeded.stderr or seeded.stdout)

    def _write_fake_scripts(self) -> None:
        shutil.copy2(SCRIPT, self.root / "scripts" / "auto_refresh.py")
        (self.root / "scripts" / "ingest_sources.py").write_text(
            """#!/usr/bin/env python3
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--out', type=Path, required=True); a=p.parse_args()
root=Path.cwd(); counter=root/'data/cache/build-count'
counter.write_text(str(int(counter.read_text() or '0')+1) if counter.exists() else '1')
payload=json.loads((root/'data/free_intelligence.json').read_text())
payload['generated_at']='2026-07-13T12:00:00+00:00'; payload['refresh_seq']=int(payload.get('refresh_seq', 0))+1
a.out.parent.mkdir(parents=True, exist_ok=True); a.out.write_text(json.dumps(payload, sort_keys=True)+'\\n')
""",
            encoding="utf-8",
        )
        (self.root / "scripts" / "qa_ingestion_delta.py").write_text(
            """#!/usr/bin/env python3
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--baseline'); p.add_argument('--rebuilt'); p.add_argument('--policy-exclusions'); a=p.parse_args()
failed=(Path.cwd()/'data/cache/delta-fail').exists()
out={'ok':not failed,'rebuilt':{'records':1,'raw_records':1,'errors':0},'delta':{'missing_ids':0},'failures':['forced delta failure'] if failed else []}
print(json.dumps(out)); raise SystemExit(1 if failed else 0)
""",
            encoding="utf-8",
        )
        (self.root / "scripts" / "qa_ingestion_canary.py").write_text(
            """#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--candidate','--existing-candidate',dest='candidate',type=Path,required=True); p.add_argument('--baseline',type=Path,required=True); p.add_argument('--history-out',type=Path,required=True); p.add_argument('--policy-exclusions'); a=p.parse_args()
root=Path.cwd(); failed=(root/'data/cache/canary-fail').exists()
if (root/'data/cache/race-dirty').exists(): (root/'README.md').write_text('raced\\n')
if (root/'data/cache/race-canonical').exists(): (root/'data/free_intelligence.json').write_text('{"raced":true}\\n')
sha=lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
entry={'ok':not failed,'candidate':str(a.candidate),'baseline':str(a.baseline),'candidate_sha256':sha(a.candidate),'candidate_sha256_after':sha(a.candidate),'baseline_sha256':sha(a.baseline),'candidate_reused':True,'failures':['forced canary failure'] if failed else []}
with a.history_out.open('a') as h: h.write(json.dumps(entry)+'\\n')
print(json.dumps(entry)); raise SystemExit(1 if failed else 0)
""",
            encoding="utf-8",
        )
        for script_name, report_kind in (
            ("qa_free_db_completeness.py", "free-db-completeness"),
            ("qa_valencia_source_audit.py", "valencia-source-audit"),
        ):
            (self.root / "scripts" / script_name).write_text(
                f"""#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--data',type=Path,required=True); p.add_argument('--out',type=Path,required=True); p.add_argument('--data-label'); p.add_argument('--strict',action='store_true'); a=p.parse_args()
root=Path.cwd(); failed=(root/'data/cache/report-fail').exists() or (root/'data/cache/{report_kind}-fail').exists()
if failed: print(json.dumps({{'ok':False,'failures':['forced report failure']}})); raise SystemExit(1)
data=json.loads(a.data.read_text()); sha=hashlib.sha256(a.data.read_bytes()).hexdigest()
out={{'ok':True,'data':a.data_label or str(a.data),'input_path':str(a.data),'data_sha256':sha,'refresh_seq':data.get('refresh_seq',0),'kind':'{report_kind}'}}
a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,sort_keys=True)+'\\n')
print(json.dumps(out))
""",
                encoding="utf-8",
            )
        (self.root / "scripts" / "notify.py").write_text(
            """#!/usr/bin/env python3
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(sys.argv[2]+'\\n')
""",
            encoding="utf-8",
        )

    @property
    def db(self) -> Path:
        return self.root / "data" / "free_intelligence.json"

    @property
    def history(self) -> Path:
        return self.root / "qa" / "ingestion-canary-history.jsonl"

    @property
    def free_report(self) -> Path:
        return self.root / "qa" / "free-db-completeness-latest.json"

    @property
    def valencia_report(self) -> Path:
        return self.root / "qa" / "valencia-source-audit-latest.json"

    @property
    def canonical_artifacts(self) -> tuple[Path, ...]:
        return (self.db, self.history, self.free_report, self.valencia_report)

    @property
    def receipt(self) -> Path:
        return self.root / "data" / "cache" / "auto-refresh" / "last.json"

    @property
    def dry_receipt(self) -> Path:
        return self.root / "data" / "cache" / "auto-refresh" / "dry-run-last.json"

    def refresh(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        merged["PYTHONPYCACHEPREFIX"] = "/tmp/volta-pycache"
        if env:
            merged.update(env)
        return run(
            [sys.executable, str(self.root / "scripts/auto_refresh.py"), "--root", str(self.root), *args],
            self.root,
            env=merged,
        )

    def head(self) -> str:
        return run(["git", "rev-parse", "HEAD"], self.root).stdout.strip()

    def origin_tracking_head(self) -> str:
        return run(["git", "rev-parse", "refs/remotes/origin/main"], self.root).stdout.strip()

    def advance_origin(
        self,
        *,
        filename: str = "REMOTE.md",
        content: str = "remote update\n",
        require_remote_reexec: bool = False,
    ) -> str:
        upstream = Path(tempfile.mkdtemp(prefix="upstream-", dir=self.root / "data/cache"))
        cloned = run(["git", "clone", "--branch", "main", str(self.remote), str(upstream)], self.root)
        if cloned.returncode != 0:
            raise AssertionError(cloned.stderr or cloned.stdout)
        run(["git", "config", "user.email", "upstream@example.invalid"], upstream)
        run(["git", "config", "user.name", "Upstream Fixture"], upstream)
        (upstream / filename).write_text(content, encoding="utf-8")
        if require_remote_reexec:
            auto_refresh = upstream / "scripts/auto_refresh.py"
            auto_text = auto_refresh.read_text(encoding="utf-8")
            root_line = "ROOT = Path(__file__).resolve().parents[1]\n"
            self.assert_text_contains(auto_text, root_line)
            auto_refresh.write_text(
                auto_text.replace(
                    root_line,
                    root_line
                    + '(ROOT / "data/cache/remote-version-ran").write_text("yes\\n", encoding="utf-8")\n',
                    1,
                ),
                encoding="utf-8",
            )
            ingest = upstream / "scripts/ingest_sources.py"
            ingest_text = ingest.read_text(encoding="utf-8")
            ingest.write_text(
                ingest_text.replace(
                    "from pathlib import Path\n",
                    "from pathlib import Path\n"
                    "if not (Path.cwd()/'data/cache/remote-version-ran').is_file(): raise SystemExit(97)\n",
                    1,
                ),
                encoding="utf-8",
            )
        run(["git", "add", filename], upstream)
        if require_remote_reexec:
            run(["git", "add", "scripts/auto_refresh.py", "scripts/ingest_sources.py"], upstream)
        committed = run(["git", "commit", "-m", "upstream update"], upstream)
        if committed.returncode != 0:
            raise AssertionError(committed.stderr or committed.stdout)
        pushed = run(["git", "push", "origin", "main"], upstream)
        if pushed.returncode != 0:
            raise AssertionError(pushed.stderr or pushed.stdout)
        return run(["git", "rev-parse", "HEAD"], upstream).stdout.strip()

    @staticmethod
    def assert_text_contains(text: str, needle: str) -> None:
        if needle not in text:
            raise AssertionError(f"fixture text did not contain {needle!r}")


class AutoRefreshCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = RefreshRepo(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_allows_dirty_shared_checkout_and_never_promotes_or_commits(self) -> None:
        before_db = self.repo.db.read_bytes()
        before_head = self.repo.head()
        (self.repo.root / "README.md").write_text("human work\n", encoding="utf-8")

        proc = self.repo.refresh("--dry-run")

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertEqual(self.repo.db.read_bytes(), before_db)
        self.assertEqual(self.repo.head(), before_head)
        self.assertEqual((self.repo.root / "data/cache/build-count").read_text(), "1")
        receipt = json.loads(self.repo.dry_receipt.read_text())
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["mode"], "dry-run")
        self.assertEqual(stat.S_IMODE(self.repo.dry_receipt.stat().st_mode), 0o600)

    def test_up_to_date_real_run_fetches_origin_before_capturing_initial_head(self) -> None:
        before_head = self.repo.head()

        proc = self.repo.refresh()

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertTrue(receipt["sync"]["fetched"])
        self.assertFalse(receipt["sync"]["fast_forwarded"])
        self.assertFalse(receipt["sync"]["reexec"])
        self.assertEqual(receipt["sync"]["head_before"], before_head)
        self.assertEqual(receipt["sync"]["origin_main"], before_head)
        self.assertEqual(receipt["head_initial"], before_head)

    def test_behind_main_fast_forwards_and_reexecutes_the_fetched_script(self) -> None:
        remote_head = self.repo.advance_origin(require_remote_reexec=True)
        before_head = self.repo.head()

        proc = self.repo.refresh()

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertNotEqual(before_head, remote_head)
        self.assertEqual((self.repo.root / "data/cache/remote-version-ran").read_text(), "yes\n")
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertTrue(receipt["sync"]["fetched"])
        self.assertTrue(receipt["sync"]["fast_forwarded"])
        self.assertTrue(receipt["sync"]["reexec"])
        self.assertEqual(receipt["sync"]["origin_main"], remote_head)
        self.assertEqual(receipt["head_initial"], remote_head)
        parent = run(["git", "rev-parse", "HEAD^"], self.repo.root)
        self.assertEqual(parent.stdout.strip(), remote_head)

    def test_fetch_failure_leaves_head_and_artifacts_untouched_and_notifies(self) -> None:
        before = {path: path.read_bytes() for path in self.repo.canonical_artifacts}
        before_head = self.repo.head()
        missing_remote = self.repo.root / "data/cache/missing-origin.git"
        self.assertEqual(
            run(["git", "remote", "set-url", "origin", str(missing_remote)], self.repo.root).returncode,
            0,
        )
        notify = self.repo.root / "data/cache/sync-notification.txt"
        command = f"{sys.executable} scripts/notify.py {notify}"

        proc = self.repo.refresh(env={"VOLTA_NOTIFY_CMD": command})

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.repo.head(), before_head)
        self.assertEqual({path: path.read_bytes() for path in self.repo.canonical_artifacts}, before)
        self.assertFalse((self.repo.root / "data/cache/build-count").exists())
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertEqual(receipt["stage"], "sync")
        self.assertIn("sync", notify.read_text().lower())

    def test_diverged_main_fails_closed_without_overwriting_local_or_remote_history(self) -> None:
        remote_head = self.repo.advance_origin()
        (self.repo.root / "README.md").write_text("local divergent commit\n", encoding="utf-8")
        self.assertEqual(run(["git", "add", "README.md"], self.repo.root).returncode, 0)
        local_commit = run(["git", "commit", "-m", "local divergent commit"], self.repo.root)
        self.assertEqual(local_commit.returncode, 0, local_commit.stderr)
        local_head = self.repo.head()
        before = {path: path.read_bytes() for path in self.repo.canonical_artifacts}

        proc = self.repo.refresh()

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.repo.head(), local_head)
        self.assertEqual({path: path.read_bytes() for path in self.repo.canonical_artifacts}, before)
        self.assertEqual(
            run(["git", "--git-dir", str(self.repo.remote), "rev-parse", "refs/heads/main"], self.repo.root).stdout.strip(),
            remote_head,
        )
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertEqual(receipt["stage"], "sync")
        self.assertFalse((self.repo.root / "data/cache/build-count").exists())

    def test_unverified_local_ahead_commit_is_not_carried_to_origin(self) -> None:
        remote_head = self.repo.origin_tracking_head()
        (self.repo.root / "README.md").write_text("manual local-only commit\n", encoding="utf-8")
        self.assertEqual(run(["git", "add", "README.md"], self.repo.root).returncode, 0)
        committed = run(["git", "commit", "-m", "manual local-only commit"], self.repo.root)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        local_head = self.repo.head()

        proc = self.repo.refresh("--push")

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.repo.head(), local_head)
        self.assertEqual(
            run(["git", "--git-dir", str(self.repo.remote), "rev-parse", "refs/heads/main"], self.repo.root).stdout.strip(),
            remote_head,
        )
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertEqual(receipt["stage"], "sync")
        self.assertFalse((self.repo.root / "data/cache/build-count").exists())

    def test_remote_rewind_is_rejected_instead_of_republishing_removed_history(self) -> None:
        local_head = self.repo.head()
        advanced = self.repo.advance_origin()
        fetched = run(
            [
                "git",
                "fetch",
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
            ],
            self.repo.root,
        )
        self.assertEqual(fetched.returncode, 0, fetched.stderr)
        self.assertEqual(self.repo.origin_tracking_head(), advanced)
        rewound = run(
            ["git", "--git-dir", str(self.repo.remote), "update-ref", "refs/heads/main", local_head],
            self.repo.root,
        )
        self.assertEqual(rewound.returncode, 0, rewound.stderr)

        proc = self.repo.refresh("--push")

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.repo.head(), local_head)
        self.assertEqual(self.repo.origin_tracking_head(), advanced)
        self.assertEqual(json.loads(self.repo.receipt.read_text())["stage"], "sync")
        self.assertFalse((self.repo.root / "data/cache/build-count").exists())

    def test_dirty_race_immediately_after_fetch_fails_before_fast_forward(self) -> None:
        module = load_auto_refresh()
        remote_head = self.repo.advance_origin()
        before_head = self.repo.head()
        real_run_command = module.run_command

        def dirty_after_fetch(command: list[str], *, root: Path, timeout: float):
            proc = real_run_command(command, root=root, timeout=timeout)
            if command and command[0] == "git" and "fetch" in command:
                (self.repo.root / "README.md").write_text("raced after fetch\n", encoding="utf-8")
            return proc

        args = module.parse_args(["--root", str(self.repo.root)])
        with patch.object(module, "run_command", side_effect=dirty_after_fetch):
            returncode = module.execute(args)

        self.assertNotEqual(returncode, 0)
        self.assertEqual(self.repo.head(), before_head)
        self.assertNotEqual(before_head, remote_head)
        self.assertIn("README.md", run(["git", "status", "--porcelain"], self.repo.root).stdout)
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertEqual(receipt["stage"], "sync")
        self.assertFalse((self.repo.root / "data/cache/build-count").exists())

    def test_dry_run_never_fetches_or_fast_forwards_remote_main(self) -> None:
        remote_head = self.repo.advance_origin()
        before_head = self.repo.head()
        before_tracking = self.repo.origin_tracking_head()

        proc = self.repo.refresh("--dry-run")

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertNotEqual(remote_head, before_head)
        self.assertEqual(self.repo.head(), before_head)
        self.assertEqual(self.repo.origin_tracking_head(), before_tracking)
        receipt = json.loads(self.repo.dry_receipt.read_text())
        self.assertFalse(receipt["sync"]["requested"])
        self.assertFalse(receipt["sync"]["fetched"])

    def test_real_run_promotes_exact_candidate_and_commits_only_allowlisted_paths(self) -> None:
        before_head = self.repo.head()

        proc = self.repo.refresh()

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertNotEqual(self.repo.head(), before_head)
        self.assertEqual(json.loads(self.repo.db.read_text())["refresh_seq"], 1)
        self.assertEqual((self.repo.root / "data/cache/build-count").read_text(), "1")
        changed = run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], self.repo.root)
        self.assertEqual(
            set(changed.stdout.splitlines()),
            {
                "data/free_intelligence.json",
                "qa/ingestion-canary-history.jsonl",
                "qa/free-db-completeness-latest.json",
                "qa/valencia-source-audit-latest.json",
            },
        )
        self.assertEqual(run(["git", "status", "--porcelain"], self.repo.root).stdout, "")
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["candidate_sha256"], receipt["canonical_sha256"])
        canonical_sha = hashlib.sha256(self.repo.db.read_bytes()).hexdigest()
        for report in (self.repo.free_report, self.repo.valencia_report):
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["data"], "data/free_intelligence.json")
            self.assertEqual(payload["input_path"], "data/free_intelligence.json")
            self.assertEqual(payload["data_sha256"], canonical_sha)
            self.assertEqual(payload["refresh_seq"], 1)
        self.assertTrue(receipt["commit"]["created"])
        self.assertFalse(receipt["push"]["requested"])
        self.assertTrue(receipt["promotion"]["backup_cleanup_performed"])
        self.assertEqual(
            list((self.repo.root / "data/cache/auto-refresh").glob("rollback-*")),
            [],
        )

    def test_report_generation_failure_preserves_every_canonical_artifact(self) -> None:
        before = {path: path.read_bytes() for path in self.repo.canonical_artifacts}
        before_head = self.repo.head()
        (self.repo.root / "data/cache/report-fail").touch()

        proc = self.repo.refresh()

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.repo.head(), before_head)
        self.assertEqual(
            {path: path.read_bytes() for path in self.repo.canonical_artifacts},
            before,
        )
        self.assertEqual(run(["git", "status", "--porcelain"], self.repo.root).stdout, "")
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertEqual(receipt["stage"], "reports")
        self.assertFalse(receipt["ok"])
        self.assertTrue(receipt["promotion"]["rollback_performed"])
        self.assertTrue(receipt["promotion"]["backup_cleanup_performed"])
        self.assertEqual(
            list((self.repo.root / "data/cache/auto-refresh").glob("rollback-*")),
            [],
        )

    def test_failed_commit_command_cannot_preserve_a_concurrent_mixed_head(self) -> None:
        module = load_auto_refresh()
        before = {path: path.read_bytes() for path in self.repo.canonical_artifacts}
        before_head = self.repo.head()
        real_run_command = module.run_command
        raced = False

        def concurrent_commit_then_report_failure(command: list[str], *, root: Path, timeout: float):
            nonlocal raced
            if command and command[0] == "git" and "commit" in command and not raced:
                raced = True
                (self.repo.root / "README.md").write_text("concurrent commit\n", encoding="utf-8")
                self.assertEqual(run(["git", "add", "README.md"], self.repo.root).returncode, 0)
                concurrent = run(["git", "commit", "-m", "concurrent mixed commit"], self.repo.root)
                self.assertEqual(concurrent.returncode, 0, concurrent.stderr)
                return subprocess.CompletedProcess(command, 1, "", "forced commit-command failure")
            return real_run_command(command, root=root, timeout=timeout)

        args = module.parse_args(["--root", str(self.repo.root)])
        with patch.object(module, "run_command", side_effect=concurrent_commit_then_report_failure):
            returncode = module.execute(args)

        self.assertNotEqual(returncode, 0)
        self.assertTrue(raced)
        self.assertNotEqual(self.repo.head(), before_head)
        self.assertEqual(
            {path: path.read_bytes() for path in self.repo.canonical_artifacts},
            before,
        )
        status = run(["git", "status", "--porcelain"], self.repo.root).stdout
        for path in (
            "data/free_intelligence.json",
            "qa/ingestion-canary-history.jsonl",
            "qa/free-db-completeness-latest.json",
            "qa/valencia-source-audit-latest.json",
        ):
            self.assertIn(path, status)
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertEqual(receipt["stage"], "commit")
        self.assertFalse(receipt["commit"]["created"])
        self.assertTrue(receipt["promotion"]["rollback_performed"])
        self.assertTrue(receipt["promotion"]["index_reset_performed"])

    def test_gate_failures_leave_database_and_head_untouched_and_notify(self) -> None:
        for gate in ("delta", "canary"):
            with self.subTest(gate=gate):
                if gate == "canary":
                    # The first subtest's failed run wrote only ignored state.
                    (self.repo.root / "data/cache/delta-fail").unlink(missing_ok=True)
                (self.repo.root / f"data/cache/{gate}-fail").touch()
                before_db = self.repo.db.read_bytes()
                before_history = self.repo.history.read_bytes()
                before_head = self.repo.head()
                notify = self.repo.root / "data/cache/notification.txt"
                command = f"{sys.executable} scripts/notify.py {notify}"

                proc = self.repo.refresh(env={"VOLTA_NOTIFY_CMD": command})

                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(self.repo.db.read_bytes(), before_db)
                self.assertEqual(self.repo.history.read_bytes(), before_history)
                self.assertEqual(self.repo.head(), before_head)
                receipt = json.loads(self.repo.receipt.read_text())
                self.assertFalse(receipt["ok"])
                self.assertEqual(receipt["stage"], gate)
                self.assertIn(gate, notify.read_text().lower())
                (self.repo.root / f"data/cache/{gate}-fail").unlink()

    def test_real_run_requires_clean_main_dedicated_checkout(self) -> None:
        before = self.repo.db.read_bytes()
        (self.repo.root / "README.md").write_text("dirty\n", encoding="utf-8")

        proc = self.repo.refresh()

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(json.loads(self.repo.receipt.read_text())["stage"], "preflight")
        self.assertEqual(self.repo.db.read_bytes(), before)
        self.assertFalse((self.repo.root / "data/cache/build-count").exists())

    def test_revalidation_detects_race_before_promotion(self) -> None:
        before = self.repo.db.read_bytes()
        (self.repo.root / "data/cache/race-dirty").touch()

        proc = self.repo.refresh()

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.repo.db.read_bytes(), before)
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertEqual(receipt["stage"], "revalidate")
        self.assertEqual((self.repo.root / "data/cache/build-count").read_text(), "1")

    def test_dry_run_fails_if_canonical_artifacts_change_during_gates(self) -> None:
        module = load_auto_refresh()
        real_run_command = module.run_command

        def race_after_canary(command: list[str], *, root: Path, timeout: float):
            proc = real_run_command(command, root=root, timeout=timeout)
            if any(str(item).endswith("qa_ingestion_canary.py") for item in command):
                self.repo.db.write_text('{"raced": true}\n', encoding="utf-8")
            return proc

        args = module.parse_args(["--root", str(self.repo.root), "--dry-run"])
        with patch.object(module, "run_command", side_effect=race_after_canary):
            returncode = module.execute(args)

        self.assertNotEqual(returncode, 0)
        receipt = json.loads(self.repo.dry_receipt.read_text())
        self.assertEqual(receipt["stage"], "revalidate")
        self.assertFalse(receipt["ok"])

    def test_allowed_path_race_after_git_add_is_not_committed(self) -> None:
        module = load_auto_refresh()
        before_db = self.repo.db.read_bytes()
        before_history = self.repo.history.read_bytes()
        before_free_report = self.repo.free_report.read_bytes()
        before_valencia_report = self.repo.valencia_report.read_bytes()
        before_head = self.repo.head()
        real_run_command = module.run_command

        def race_after_add(command: list[str], *, root: Path, timeout: float):
            proc = real_run_command(command, root=root, timeout=timeout)
            if list(command[:2]) == ["git", "add"]:
                self.repo.history.write_text('{"raced": true}\n', encoding="utf-8")
            return proc

        args = module.parse_args(["--root", str(self.repo.root)])
        with patch.object(module, "run_command", side_effect=race_after_add):
            returncode = module.execute(args)

        self.assertNotEqual(returncode, 0)
        self.assertEqual(self.repo.head(), before_head)
        self.assertEqual(self.repo.db.read_bytes(), before_db)
        self.assertEqual(self.repo.history.read_bytes(), before_history)
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertEqual(receipt["stage"], "git-verify")
        self.assertTrue(receipt["promotion"]["rollback_performed"])

    def test_every_post_promotion_pre_commit_escape_restores_files_and_index(self) -> None:
        module = load_auto_refresh()
        before_db = self.repo.db.read_bytes()
        before_history = self.repo.history.read_bytes()
        before_free_report = self.repo.free_report.read_bytes()
        before_valencia_report = self.repo.valencia_report.read_bytes()
        before_head = self.repo.head()

        for failure in (
            module.RefreshFailure("git-verify", "forced blob failure"),
            RuntimeError("forced unexpected blob failure"),
        ):
            with self.subTest(failure=type(failure).__name__):
                args = module.parse_args(["--root", str(self.repo.root)])
                with patch.object(module, "git_blob_oid", side_effect=failure):
                    returncode = module.execute(args)

                self.assertNotEqual(returncode, 0)
                self.assertEqual(self.repo.db.read_bytes(), before_db)
                self.assertEqual(self.repo.history.read_bytes(), before_history)
                self.assertEqual(self.repo.free_report.read_bytes(), before_free_report)
                self.assertEqual(self.repo.valencia_report.read_bytes(), before_valencia_report)
                self.assertEqual(self.repo.head(), before_head)
                self.assertEqual(run(["git", "status", "--porcelain"], self.repo.root).stdout, "")
                receipt = json.loads(self.repo.receipt.read_text())
                self.assertEqual(receipt["stage"], "git-verify")
                self.assertFalse(receipt["ok"])
                self.assertTrue(receipt["promotion"]["rollback_performed"])
                self.assertTrue(receipt["promotion"]["index_reset_performed"])

    def test_nonblocking_lock_returns_temporary_failure_without_overwriting_receipt(self) -> None:
        lock_path = self.repo.root / "data/cache/test.lock"
        lock_path.touch(mode=0o600)
        self.repo.receipt.parent.mkdir(parents=True)
        self.repo.receipt.write_text('{"sentinel": true}\n', encoding="utf-8")
        with lock_path.open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            proc = self.repo.refresh("--dry-run", "--lock-file", str(lock_path))

        self.assertEqual(proc.returncode, 75)
        self.assertEqual(json.loads(self.repo.receipt.read_text()), {"sentinel": True})
        self.assertFalse((self.repo.root / "data/cache/build-count").exists())

    def test_push_failure_preserves_successful_local_commit(self) -> None:
        module = load_auto_refresh()
        before_head = self.repo.head()
        real_run_command = module.run_command

        def fail_publication(command: list[str], *, root: Path, timeout: float):
            if command and command[0] == "git" and "push" in command:
                return subprocess.CompletedProcess(command, 1, "", "forced push failure")
            return real_run_command(command, root=root, timeout=timeout)

        args = module.parse_args(["--root", str(self.repo.root), "--push"])
        with patch.object(module, "run_command", side_effect=fail_publication):
            returncode = module.execute(args)

        self.assertNotEqual(returncode, 0)
        self.assertNotEqual(self.repo.head(), before_head)
        self.assertEqual(json.loads(self.repo.db.read_text())["refresh_seq"], 1)
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertEqual(receipt["stage"], "push")
        self.assertTrue(receipt["commit"]["created"])
        self.assertTrue(receipt["commit"]["preserved"])
        self.assertTrue(receipt["promotion"]["backup_cleanup_performed"])
        self.assertEqual(
            list((self.repo.root / "data/cache/auto-refresh").glob("rollback-*")),
            [],
        )

        preserved_head = self.repo.head()
        pending = self.repo.root / "data/cache/auto-refresh/pending-push.json"
        self.assertTrue(pending.is_file())

        accidental_no_push = self.repo.refresh()

        self.assertNotEqual(accidental_no_push.returncode, 0)
        self.assertEqual(self.repo.head(), preserved_head)
        self.assertTrue(pending.is_file())
        self.assertEqual(json.loads(self.repo.receipt.read_text())["stage"], "sync")

        retry_module = load_auto_refresh()
        retry_run_command = retry_module.run_command

        def fail_retry_publication(command: list[str], *, root: Path, timeout: float):
            if command and command[0] == "git" and "push" in command:
                return subprocess.CompletedProcess(command, 1, "", "forced retry failure")
            return retry_run_command(command, root=root, timeout=timeout)

        retry_args = retry_module.parse_args(["--root", str(self.repo.root), "--push"])
        with patch.object(retry_module, "run_command", side_effect=fail_retry_publication):
            failed_retry = retry_module.execute(retry_args)

        self.assertNotEqual(failed_retry, 0)
        self.assertEqual(self.repo.head(), preserved_head)
        self.assertTrue(pending.is_file())

        retry = self.repo.refresh("--push")

        self.assertEqual(retry.returncode, 0, retry.stderr or retry.stdout)
        retry_receipt = json.loads(self.repo.receipt.read_text())
        self.assertTrue(retry_receipt["sync"]["local_ahead"])
        self.assertTrue(retry_receipt["sync"]["preserved_push_retry"])
        self.assertEqual(retry_receipt["sync"]["head_before"], preserved_head)
        remote_head = run(
            ["git", "--git-dir", str(self.repo.remote), "rev-parse", "refs/heads/main"],
            self.repo.root,
        ).stdout.strip()
        self.assertEqual(remote_head, self.repo.head())
        self.assertFalse(pending.exists())

    def test_push_publishes_only_gated_sha_and_rejects_concurrent_local_commit(self) -> None:
        module = load_auto_refresh()
        remote = self.repo.remote
        real_run_command = module.run_command
        raced = False

        def commit_unrelated_change_before_push(command: list[str], *, root: Path, timeout: float):
            nonlocal raced
            if command and command[0] == "git" and "push" in command and not raced:
                raced = True
                (self.repo.root / "README.md").write_text("concurrent unrelated change\n", encoding="utf-8")
                self.assertEqual(run(["git", "add", "README.md"], self.repo.root).returncode, 0)
                committed = run(["git", "commit", "-m", "concurrent unrelated change"], self.repo.root)
                self.assertEqual(committed.returncode, 0, committed.stderr)
            return real_run_command(command, root=root, timeout=timeout)

        args = module.parse_args(["--root", str(self.repo.root), "--push"])
        with patch.object(module, "run_command", side_effect=commit_unrelated_change_before_push):
            returncode = module.execute(args)

        self.assertNotEqual(returncode, 0)
        self.assertTrue(raced)
        receipt = json.loads(self.repo.receipt.read_text())
        gated_sha = receipt["commit"]["sha"]
        self.assertEqual(receipt["stage"], "push")
        self.assertFalse(receipt["ok"])
        self.assertTrue(receipt["push"]["ok"])
        self.assertEqual(receipt["push"]["published_sha"], gated_sha)
        self.assertNotEqual(self.repo.head(), gated_sha)
        remote_head = run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
            self.repo.root,
        )
        self.assertEqual(remote_head.returncode, 0, remote_head.stderr)
        self.assertEqual(remote_head.stdout.strip(), gated_sha)
        remote_readme = run(
            ["git", "--git-dir", str(remote), "show", "refs/heads/main:README.md"],
            self.repo.root,
        )
        self.assertEqual(remote_readme.stdout, "fixture\n")

    def test_repository_hooks_cannot_replace_gated_bytes_during_commit(self) -> None:
        hook = self.repo.root / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' '{\"hook_mutated\":true}' > qa/ingestion-canary-history.jsonl\n"
            "git add qa/ingestion-canary-history.jsonl\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)

        proc = self.repo.refresh()

        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertNotIn("hook_mutated", self.repo.history.read_text(encoding="utf-8"))
        committed_history = run(
            ["git", "show", "HEAD:qa/ingestion-canary-history.jsonl"],
            self.repo.root,
        )
        self.assertEqual(committed_history.returncode, 0)
        self.assertNotIn("hook_mutated", committed_history.stdout)
        receipt = json.loads(self.repo.receipt.read_text())
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["history_canonical_sha256"], receipt["canary"]["history_sha256"])


class PromotionRollbackTests(unittest.TestCase):
    def test_partial_promotion_restores_all_four_originals(self) -> None:
        module = load_auto_refresh()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "data/cache/auto-refresh"
            db = root / "data/free_intelligence.json"
            history = root / "qa/history.jsonl"
            candidate = work / "candidate.json"
            next_history = work / "history.jsonl"
            free_report = root / "qa/free-report.json"
            valencia_report = root / "qa/valencia-report.json"
            next_free_report = work / "free-report.json"
            next_valencia_report = work / "valencia-report.json"
            for path, value in (
                (db, b"old-db"),
                (history, b"old-history"),
                (free_report, b"old-free-report"),
                (valencia_report, b"old-valencia-report"),
                (candidate, b"new-db"),
                (next_history, b"new-history"),
                (next_free_report, b"new-free-report"),
                (next_valencia_report, b"new-valencia-report"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)

            real_replace = os.replace
            calls = 0

            def fail_second_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated history promotion failure")
                real_replace(src, dst)

            with patch.object(module.os, "replace", side_effect=fail_second_replace):
                with self.assertRaises(module.PromotionError) as raised:
                    module.promote_transaction(
                        [
                            ("db", candidate, db),
                            ("history", next_history, history),
                            ("free-report", next_free_report, free_report),
                            ("valencia-report", next_valencia_report, valencia_report),
                        ],
                        work,
                    )

            self.assertEqual(db.read_bytes(), b"old-db")
            self.assertEqual(history.read_bytes(), b"old-history")
            self.assertEqual(free_report.read_bytes(), b"old-free-report")
            self.assertEqual(valencia_report.read_bytes(), b"old-valencia-report")
            self.assertTrue(raised.exception.rollback_performed)
            self.assertTrue(raised.exception.backup_cleanup_performed)
            self.assertEqual(list(work.glob("rollback-*")), [])


class SchedulerTemplateTests(unittest.TestCase):
    def test_launchd_templates_are_disabled_and_point_to_dedicated_checkout(self) -> None:
        for path in sorted((ROOT / "deploy" / "launchd").glob("com.volta.*.plist")):
            with self.subTest(path=path.name):
                payload = plistlib.loads(path.read_bytes())
                self.assertIs(payload.get("Disabled"), True)
                joined = " ".join(payload.get("ProgramArguments") or [])
                self.assertIn("/PATH/TO/VOLTA-DEDICATED-CHECKOUT", joined)
                if path.name == "com.volta.refresh.plist":
                    self.assertEqual(
                        payload.get("StartCalendarInterval"),
                        {"Weekday": 1, "Hour": 5, "Minute": 30},
                        "launchd Weekday=1 is Monday; keep the documented Monday cadence exact",
                    )

    def test_launchd_activation_docs_explicitly_override_disabled_state(self) -> None:
        deploy = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")
        for label in ("com.volta.refresh", "com.volta.health"):
            enable = f"launchctl enable gui/$(id -u)/{label}"
            bootstrap = f"launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{label}.plist"
            self.assertIn(enable, deploy)
            self.assertIn(bootstrap, deploy)
            self.assertLess(deploy.index(enable), deploy.index(bootstrap))

    def test_systemd_templates_remain_uninstalled_and_conditioned_on_marker(self) -> None:
        directory = ROOT / "deploy" / "systemd"
        service_paths = sorted(directory.glob("*.service"))
        timer_paths = sorted(directory.glob("*.timer"))
        self.assertEqual({p.name for p in service_paths}, {
            "volta-health.service", "volta-server.service", "volta-refresh.service", "volta-data-refresh.service"})
        self.assertEqual({p.name for p in timer_paths}, {
            "volta-health.timer", "volta-refresh.timer", "volta-data-refresh.timer"})
        runtime = (directory / "volta-data-refresh.service").read_text()
        self.assertIn("EnvironmentFile=%h/.config/volta/data-refresh.env", runtime)
        self.assertIn("scripts/runtime_refresh.py --state-dir", runtime)
        self.assertIn("ExecStopPost=", runtime)
        self.assertNotIn("--push", runtime)
        for path in service_paths:
            if path.name == "volta-data-refresh.service":
                continue  # separate runtime-data job; existing Git templates keep their marker
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("ConditionPathExists=/PATH/TO/VOLTA-DEDICATED-CHECKOUT/.volta-auto-refresh-dedicated", text)
                self.assertIn("DISABLED TEMPLATE", text)

    def test_legacy_shell_is_only_a_thin_orchestrator_shim(self) -> None:
        text = (ROOT / "scripts" / "refresh_intelligence.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/auto_refresh.py", text)
        self.assertNotIn("scripts/ingest_sources.py", text)
        self.assertNotIn("qa_ingestion_delta.py", text)
        self.assertNotIn("qa_ingestion_canary.py", text)


if __name__ == "__main__":
    unittest.main()
