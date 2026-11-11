"""Offline check of the Gaia public start-page parser.

The sample page lives in scripts/smoke_gaia_parser.py (QA ledgers reference that
script); importing it runs no network code.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from scripts.smoke_gaia_parser import SAMPLE_HTML  # noqa: E402
from tests import _support  # noqa: E402


def setUpModule():
    _support.isolate_community_store()


class GaiaPublicPageTests(unittest.TestCase):
    def test_sample_page_yields_the_connector_facts(self) -> None:
        parsed = server.parse_gaia_public_page(server.GAIA_DEFAULT_JUMP_URL, SAMPLE_HTML)
        self.assertTrue(parsed)
        self.assertEqual(parsed["connector_id"], "CIRCUTOR-0786-1")
        self.assertEqual(parsed["status"], "CHARGING")
        self.assertEqual(parsed["label"], "Type 2")
        self.assertEqual(parsed["power_kw"], 11)
        self.assertEqual(parsed["physical_reference"], "H")
        self.assertEqual(parsed["session_power_kw"], 6.8)
        self.assertIs(parsed["is_free"], True)


if __name__ == "__main__":
    unittest.main()
