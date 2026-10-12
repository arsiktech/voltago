#!/usr/bin/env python3
"""Spain envelope includes Canaries, Ceuta and Melilla without inverted viewports."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


CANARIES = {
    "Las Palmas": (28.1235, -15.4363),
    "Santa Cruz de Tenerife": (28.4636, -16.2518),
}
NORTH_AFRICA = {
    "Ceuta": (35.8894, -5.3213),
    "Melilla": (35.2923, -2.9381),
}
MAINLAND = {
    "Madrid": (40.4168, -3.7038),
    "València": (39.4699, -0.3763),
}


class SpainEnvelopeTests(unittest.TestCase):
    def test_envelope_contains_all_spanish_territories(self) -> None:
        for name, (lat, lon) in {**CANARIES, **NORTH_AFRICA, **MAINLAND}.items():
            self.assertTrue(
                server.in_bbox(lat, lon, server.SPAIN_BBOX),
                f"{name} is outside SPAIN_BBOX",
            )

    def test_canary_geo_viewport_is_ordered_and_contains_the_city(self) -> None:
        bbox, source, _clamped = server.resolve_viewport(
            {"lat": ["28.1235"], "lon": ["-15.4363"]}
        )
        self.assertEqual(source, "geo")
        self.assertLess(bbox["latitude_sw"], bbox["latitude_ne"])
        self.assertLess(bbox["longitude_sw"], bbox["longitude_ne"])
        self.assertTrue(server.in_bbox(28.1235, -15.4363, bbox))
        self.assertGreaterEqual(bbox["latitude_sw"], server.SPAIN_BBOX["latitude_sw"])
        self.assertLessEqual(bbox["longitude_ne"], server.SPAIN_BBOX["longitude_ne"])

    def test_melilla_geo_viewport_is_not_inverted(self) -> None:
        bbox, source, _ = server.resolve_viewport({"lat": ["35.2923"], "lon": ["-2.9381"]})
        self.assertEqual(source, "geo")
        self.assertLess(bbox["latitude_sw"], bbox["latitude_ne"])
        self.assertLess(bbox["longitude_sw"], bbox["longitude_ne"])
        self.assertTrue(server.in_bbox(35.2923, -2.9381, bbox))

    def test_snapped_canary_envelope_stays_ordered(self) -> None:
        bbox, _, _ = server.resolve_viewport({"lat": ["28.4636"], "lon": ["-16.2518"]})
        envelope = server.snapped_bbox_envelope(bbox)
        self.assertLessEqual(envelope["latitude_sw"], envelope["latitude_ne"])
        self.assertLessEqual(envelope["longitude_sw"], envelope["longitude_ne"])
        self.assertTrue(
            server.bbox_contains(envelope, bbox) or server.in_bbox(28.4636, -16.2518, envelope)
        )


if __name__ == "__main__":
    unittest.main()
