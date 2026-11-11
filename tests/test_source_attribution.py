"""Attribution is per source entry, including sources added after the first stamp."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from source_attribution import stamp_row


class SourceAttributionTests(unittest.TestCase):
    def test_corroborating_source_is_not_labelled_as_the_primary(self):
        row = {
            "source": "OpenStreetMap public tags",
            "source_url": "https://www.openstreetmap.org/node/1",
            "sources": [
                {
                    "name": "OpenStreetMap public tags",
                    "url": "https://www.openstreetmap.org/node/1",
                    "attribution": "© OpenStreetMap contributors",
                },
                {
                    "name": "Red MOVEM official public-price regulation",
                    "url": "https://www.dip-badajoz.es/toolsphp/descarga.php?fr=6",
                },
            ],
        }
        stamp_row(row)
        self.assertEqual(row["sources"][0]["attribution"], "© OpenStreetMap contributors")
        self.assertEqual(row["sources"][1]["attribution"], "Diputación de Badajoz")

    def test_unknown_entry_falls_back_to_the_row_source(self):
        row = {
            "source": "Lidl official free list",
            "source_url": "https://empresa.lidl.es/puntos",
            "sources": [{"name": "Store page", "url": "https://example.com/store"}],
        }
        stamp_row(row)
        self.assertEqual(row["sources"][0]["attribution"], "Lidl")

    def test_existing_attribution_is_kept(self):
        row = {
            "source": "OpenStreetMap public tags",
            "source_url": "https://www.openstreetmap.org/node/1",
            "sources": [{
                "name": "Electromaps web-map API",
                "url": "https://www.electromaps.com/p/1",
                "attribution": "Electromaps",
            }],
        }
        stamp_row(row)
        self.assertEqual(row["sources"][0]["attribution"], "Electromaps")


if __name__ == "__main__":
    unittest.main()
