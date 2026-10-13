"""Current runtime/submission source text must not imply a third-party licence."""
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import ingest_sources as ing
import server


class SourcePolicyCopyTests(unittest.TestCase):
    def test_ingest_notes_match_the_restored_source_exclusions(self):
        with mock.patch.object(ing, "ACTIVE_SOURCE_ADAPTERS", ()), mock.patch.object(ing, "FETCH_EVIDENCE", []):
            notes = " ".join(ing.build_payload()["license_notes"])
        self.assertIn("Rights-blocked commercial map/locator station ingestion is retired", notes)
        self.assertIn("owner-attested written redistribution permission", notes)
        self.assertNotIn("enabled by owner decision", notes)

    def test_active_appstore_docs_do_not_claim_retired_collectors_are_enabled(self):
        for filename in ("source-rights-inventory.md", "review-notes.md", "compliance.md"):
            with self.subTest(filename=filename):
                text = (ROOT / "docs" / "appstore" / filename).read_text()
                self.assertIn("Electromaps", text)
                self.assertIn("Chargemap", text)
                self.assertNotIn("enabled by owner decision", text)
                self.assertNotIn("owner decision 2026-10-12", text)

    def test_review_assembly_byte_receipts_are_recomputed(self):
        text = (ROOT / "docs" / "appstore" / "review-notes.md").read_text()
        blocks = re.findall(r"```text\n(.*?)\n```", text, re.S)
        replacements = {
            "[STABLE PUBLIC HTTPS VOLTA ENDPOINT]": "https://api.volta-carga-clara.example",
            "[FINAL PUBLIC PRIVACY URL]": "https://www.volta-carga-clara.example/legal/privacy",
            "[FINAL PUBLIC TERMS URL]": "https://www.volta-carga-clara.example/legal/terms",
            "[FINAL PUBLIC SUPPORT URL]": "https://www.volta-carga-clara.example/help/support",
        }
        for letter, block in zip(("A", "B"), blocks):
            for key, value in replacements.items():
                block = block.replace(key, value)
            size = len((block + "\n").encode())
            self.assertLessEqual(size, 4000)
            row = re.search(r"\| " + letter + r" .*?\| ([\d,]+) \| ([\d,]+) \|", text)
            self.assertIsNotNone(row)
            self.assertEqual(int(row[1].replace(",", "")), size)
            self.assertEqual(int(row[2].replace(",", "")), 4000-size)


if __name__ == "__main__":
    unittest.main()
