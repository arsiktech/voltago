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
    def test_ingest_notes_preserve_owner_decision_after_policy_cleaning(self):
        with mock.patch.object(ing, "ACTIVE_SOURCE_ADAPTERS", ()), mock.patch.object(ing, "FETCH_EVIDENCE", []):
            notes = " ".join(ing.build_payload()["license_notes"])
        self.assertIn("Electromaps and Chargemap", notes)
        self.assertIn("owner decision 2026-10-12", notes)
        self.assertIn("not a third-party reuse licence", notes)
        self.assertIn("5.2.2", notes)
        self.assertIn("owner-attested written redistribution permission", notes)
        self.assertNotIn("Rights-blocked commercial map/locator station ingestion is retired", notes)

    def test_active_appstore_docs_keep_collection_policy_separate_from_rights(self):
        for filename in ("source-rights-inventory.md", "review-notes.md", "compliance.md"):
            with self.subTest(filename=filename):
                text = (ROOT / "docs" / "appstore" / filename).read_text()
                self.assertIn("Electromaps", text)
                self.assertIn("Chargemap", text)
                self.assertIn("owner decision", text)
                self.assertIn("5.2.2", text)
                self.assertIn("Consum locator evidence remains excluded", text)
                self.assertIn("owner-attested", text)
                self.assertNotIn("Feníe is attribution/link-only", text)
                self.assertNotIn("Chargemap is removed", text)
                self.assertNotIn("Electromaps and\nConsum remain link-only", text)

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
