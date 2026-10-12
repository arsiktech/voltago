"""Unit tests for the (inactive, key-gated) Mapa REVE external client helpers."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reve_external as reve


def tariff(components):
    return {"elements": [{"price_components": components}]}


class ReveFreeVerdictTests(unittest.TestCase):
    def test_all_zero_components_is_free(self) -> None:
        self.assertTrue(reve.tariff_is_free(tariff([
            {"type": "ENERGY", "price": 0}, {"type": "FLAT", "price": 0.0},
        ])))

    def test_any_positive_component_is_paid(self) -> None:
        self.assertFalse(reve.tariff_is_free(tariff([
            {"type": "ENERGY", "price": 0}, {"type": "FLAT", "price": 1.0},
        ])))

    def test_missing_components_is_unknown_never_free(self) -> None:
        self.assertIsNone(reve.tariff_is_free({"elements": []}))
        self.assertIsNone(reve.tariff_is_free({}))

    def test_malformed_price_is_not_free(self) -> None:
        self.assertFalse(reve.tariff_is_free(tariff([{"type": "ENERGY", "price": "gratis"}])))
        self.assertFalse(reve.tariff_is_free(tariff([{"type": "ENERGY"}])))

    def test_connector_verdict_requires_every_tariff_free(self) -> None:
        free_t = tariff([{"type": "ENERGY", "price": 0}])
        paid_t = tariff([{"type": "ENERGY", "price": 0.3}])
        self.assertTrue(reve.connector_free_verdict({"tariffs": [free_t, free_t]}))
        self.assertFalse(reve.connector_free_verdict({"tariffs": [free_t, paid_t]}))
        self.assertIsNone(reve.connector_free_verdict({"tariffs": []}))
        self.assertIsNone(reve.connector_free_verdict({}))

    def test_rows_from_payload_shapes(self) -> None:
        rows = [{"evse_id": "ES*X*1"}]
        self.assertEqual(reve.rows_from_payload(rows), rows)
        self.assertEqual(reve.rows_from_payload({"data": rows}), rows)
        self.assertEqual(reve.rows_from_payload({"items": rows}), rows)
        self.assertEqual(reve.rows_from_payload("nonsense"), [])

    def test_sync_without_key_exits_inactive(self) -> None:
        original = dict(reve.os.environ)
        reve.os.environ.pop("VOLTA_REVE_API_KEY", None)
        try:
            with self.assertRaises(SystemExit):
                reve.sync_tariffs()
        finally:
            reve.os.environ.clear()
            reve.os.environ.update(original)


if __name__ == "__main__":
    unittest.main()
