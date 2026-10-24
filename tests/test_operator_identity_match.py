#!/usr/bin/env python3
"""A registry row and a community row that name the same operator nearby are one site."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import ingest_sources as ing  # noqa: E402
from test_ingest_merge import rec  # noqa: E402

NAP = ("DGT National Access Point charging registry", "https://nap.dgt.es/dataset/x")
OSM = ("OpenStreetMap public tags", "https://www.openstreetmap.org/node/1")
OCM = ("OpenChargeMap public snapshot", "https://map.openchargemap.io/?id=1")
LAT, LON = 41.6100, 0.6200
METRE = 1 / 111_320


def nap(id_: str, operator: str, north_m: float = 0, price=None) -> dict:
    return rec(id_, *NAP, LAT + north_m * METRE, LON, price=price, name=f"{operator} — Av. de Miquel Batllori, 55",
               address="Av. De Miquel Batllori, 55", reliability="official-charger-location-price-unknown",
               raw={"corroboration_only": True, "operator": operator})


def osm(id_: str, operator: str | None, north_m: float, price=None) -> dict:
    raw = {"operator": operator} if operator else {}
    # A neutral name keeps the existing name-based identity rules out of these cases.
    return rec(id_, *OSM, LAT + north_m * METRE, LON, price=price, name="Charger", address="", raw=raw)


class OperatorIdentityMatchTests(unittest.TestCase):
    def merge(self, *rows):
        merged, count = ing.merge_cross_source_duplicates(list(rows))
        return merged, count

    def test_same_operator_registry_and_community_rows_merge_within_80_m(self) -> None:
        merged, count = self.merge(nap("nap", "REPSOL SOLUCIONES ENERGÉTICAS SA"), osm("osm", "Repsol", 60))
        self.assertEqual(count, 1)
        self.assertEqual(len(merged[0]["sources"]), 2)

    def test_different_operators_stay_apart(self) -> None:
        _merged, count = self.merge(nap("nap", "IBERDROLA CLIENTES S.A.U"), osm("osm", "Endesa", 30))
        self.assertEqual(count, 0)

    def test_two_same_operator_candidates_stay_apart(self) -> None:
        _merged, count = self.merge(nap("nap", "REPSOL SOLUCIONES ENERGÉTICAS SA"),
                                    osm("osm-a", "Repsol", 40), osm("osm-b", "Repsol", -50))
        self.assertEqual(count, 0)

    def test_beyond_80_m_stays_apart(self) -> None:
        _merged, count = self.merge(nap("nap", "REPSOL SOLUCIONES ENERGÉTICAS SA"), osm("osm", "Repsol", 95))
        self.assertEqual(count, 0)

    def test_name_alone_is_not_an_operator(self) -> None:
        community = rec("ocm", *OCM, LAT + 50 * METRE, LON, name="Repsol", address="", raw={})
        _merged, count = self.merge(nap("nap", "REPSOL SOLUCIONES ENERGÉTICAS SA"), community)
        self.assertEqual(count, 0)

    def test_legal_form_words_alone_do_not_match(self) -> None:
        _merged, count = self.merge(nap("nap", "ACME Movilidad Eléctrica SL"), osm("osm", "Zeta Movilidad Eléctrica SL", 30))
        self.assertEqual(count, 0)

    def test_conflicting_prices_beyond_40_m_stay_apart(self) -> None:
        _merged, count = self.merge(nap("nap", "REPSOL SOLUCIONES ENERGÉTICAS SA", price=0.49),
                                    osm("osm", "Repsol", 60, price=0.0))
        self.assertEqual(count, 0)

    def test_same_family_never_matches_on_operator(self) -> None:
        _merged, count = self.merge(nap("nap-a", "Repsol"), nap("nap-b", "Repsol", 30))
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
