"""Release-runtime closure contracts for the container and durable QA revision."""
from __future__ import annotations

import importlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _local_docker_copy_sources(dockerfile: str) -> set[str]:
    """Return build-context sources from simple Dockerfile COPY instructions."""
    sources: set[str] = set()
    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if not line.startswith("COPY "):
            continue
        tokens = shlex.split(line)
        if any(token.startswith("--from=") for token in tokens[1:]):
            continue
        operands = [token for token in tokens[1:] if not token.startswith("--")]
        sources.update(operands[:-1])
    return sources


class RuntimeClosureTests(unittest.TestCase):
    def test_manifest_is_packaged_and_bound_to_regression_revision(self):
        runtime_closure = importlib.import_module("runtime_closure")
        qa_revision = importlib.import_module("qa_revision")
        runtime_files = set(runtime_closure.RUNTIME_FILES)

        self.assertIn("server.py", runtime_files)
        self.assertIn("community.py", runtime_files)
        self.assertIn("scripts/source_policy.py", runtime_files)
        self.assertIn("scripts/source_transport.py", runtime_files)

        docker_sources = _local_docker_copy_sources(
            (ROOT / "Dockerfile").read_text(encoding="utf-8")
        )
        self.assertEqual(set(), runtime_files - docker_sources)
        self.assertEqual(set(), runtime_files - set(qa_revision.REVISION_FILES))

    def test_manifest_alone_can_import_the_server_in_a_clean_root(self):
        runtime_closure = importlib.import_module("runtime_closure")
        with tempfile.TemporaryDirectory() as temp_dir:
            clean_root = Path(temp_dir)
            for relative in runtime_closure.RUNTIME_FILES:
                source = ROOT / relative
                destination = clean_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, "-c", "import server; print('runtime-import-ok')"],
                cwd=clean_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("runtime-import-ok", result.stdout.strip())

    def test_container_disables_community_writes_without_durable_storage(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("VOLTA_COMMUNITY_WRITES_ENABLED=false", dockerfile)


if __name__ == "__main__":
    unittest.main()
