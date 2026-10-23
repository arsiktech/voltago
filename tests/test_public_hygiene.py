"""Keep private machine and infrastructure details out of this public repository."""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    "home directory path": re.compile(r"/Users/[A-Za-z0-9._-]+/|/home/[a-z][a-z0-9_-]+/"),
    "Tailscale address": re.compile(r"\b100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}\b"),
    "Tailscale network name": re.compile(r"[a-z0-9-]+\.ts\.net\b"),
    "private tracker link": re.compile(r"app\.notion\.com/|notion\.so/|linear\.app/"),
    "agent session link": re.compile(r"claude\.ai/code/session_|chatgpt\.com/c/"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


class PublicHygieneTests(unittest.TestCase):
    def test_agent_instructions_and_project_skills_are_not_tracked(self) -> None:
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True, check=True,
        ).stdout.decode("utf-8")
        forbidden = []
        for rel in filter(None, listed.split("\0")):
            parts = rel.lower().split("/")
            if (".claude" in parts or ".agents" in parts or parts[-1] in {
                "agents.md", "claude.md", "skill.md", "copilot-instructions.md",
            }):
                forbidden.append(rel)
        self.assertEqual(forbidden, [])

    def test_tracked_text_files_contain_no_private_details(self) -> None:
        try:
            listed = subprocess.run(
                ["git", "-C", str(ROOT), "ls-files", "-z"],
                capture_output=True, check=True,
            ).stdout.decode("utf-8")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("needs a Git checkout")
        hits = []
        for rel in filter(None, listed.split("\0")):
            if rel == "tests/test_public_hygiene.py":
                continue
            path = ROOT / rel
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for label, pattern in PATTERNS.items():
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    hits.append(f"{rel}:{line}: {label}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
