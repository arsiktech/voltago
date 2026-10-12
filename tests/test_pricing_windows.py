#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class PricingWindowTests(unittest.TestCase):
    def test_time_window_station_is_kept_even_when_not_currently_cheap(self) -> None:
        windows = [
            {"price": 0.19, "currency": "EUR", "unit": "kWh", "valid_from": "00:00", "valid_until": "10:00"},
            {"price": 0.39, "currency": "EUR", "unit": "kWh", "valid_from": "10:00", "valid_until": "24:00"},
        ]
        status = server.pricing_status(
            windows,
            price_cap=0.25,
            now=datetime(2026, 6, 21, 12, 0, tzinfo=server.MADRID_TZ),
        )

        self.assertTrue(status["is_cheap"])
        self.assertEqual(status["price_min"], 0.19)
        self.assertEqual(status["current_price_min"], 0.39)
        self.assertFalse(status["is_currently_within_standard"])
        self.assertEqual(status["next_matching_window"]["valid_until"], "10:00")

    def test_time_window_station_is_go_now_inside_cheap_window(self) -> None:
        item = {
            "id": "dynamic-tesla",
            "name": "Dynamic Tesla",
            "lat": 39.47,
            "lon": -0.49,
            "status": "AVAILABLE",
            "available": 4,
            "total_evse": 12,
            "tesla_ok": True,
            "connectors": [{"label": "Tesla CCS", "power_kw": 250, "status": "AVAILABLE"}],
            "pricing": [
                {"price": 0.19, "currency": "EUR", "unit": "kWh", "valid_from": "00:00", "valid_until": "10:00"},
                {"price": 0.39, "currency": "EUR", "unit": "kWh", "valid_from": "10:00", "valid_until": "24:00"},
            ],
            "parking": {"kind": "retail_parking", "paid": False},
            "last_seen": server.now_iso(),
            "reliability": "official-live",
        }
        server.attach_pricing_summary(item)
        # Re-evaluate deterministically for the status object, then run the decision contract manually.
        fixed = server.pricing_status(item["pricing"], now=datetime(2026, 6, 21, 8, 30, tzinfo=server.MADRID_TZ))
        item["price_window_status"] = {k: v for k, v in fixed.items() if k != "windows"}
        item["is_cheap"] = fixed["is_cheap"]
        item["is_free"] = fixed["is_free"]
        decision = server.decision_for(item, live=True)

        self.assertTrue(item["is_cheap"])
        self.assertTrue(item["price_window_status"]["is_currently_within_standard"])
        self.assertEqual(decision["level"], "go")

    def test_ocpi_tariff_restrictions_become_pricing_windows(self) -> None:
        connector = {
            "tariffs": [
                {
                    "tariff": {
                        "currency": "EUR",
                        "elements": [
                            {
                                "restrictions": {"start_time": "00:00", "end_time": "10:00"},
                                "price_components": [{"type": "ENERGY", "price": 0.19, "step_size": 1}],
                            },
                            {
                                "restrictions": {"start_time": "10:00", "end_time": "24:00"},
                                "price_components": [{"type": "ENERGY", "price": 0.39, "step_size": 1}],
                            },
                        ],
                    }
                }
            ]
        }
        windows = server.connector_pricing_windows(connector)

        self.assertEqual(len(windows), 2)
        self.assertTrue(windows[0]["is_within_standard"])
        self.assertFalse(windows[1]["is_within_standard"])
        self.assertEqual(windows[0]["valid_until"], "10:00")

    def test_ocpi_time_fee_is_not_parking_price(self) -> None:
        connector = {
            "tariffs": [
                {
                    "tariff": {
                        "currency": "EUR",
                        "elements": [
                            {
                                "price_components": [
                                    {"type": "ENERGY", "price": 0.19, "step_size": 1},
                                    {"type": "TIME", "price": 0.05, "step_size": 60},
                                ],
                            }
                        ],
                    }
                }
            ]
        }
        energy, parking, _human = server.extract_prices(connector)

        self.assertEqual(energy, [0.19])
        self.assertEqual(parking, [])

    def test_ocpi_parking_time_fee_is_parking_price(self) -> None:
        connector = {
            "tariffs": [
                {
                    "tariff": {
                        "currency": "EUR",
                        "elements": [{"price_components": [{"type": "PARKING_TIME", "price": 0.07, "step_size": 60}]}],
                    }
                }
            ]
        }
        _energy, parking, _human = server.extract_prices(connector)

        self.assertEqual(parking, [0.07])

    def test_primary_source_contract_classifies_start_and_station_pages(self) -> None:
        gaia = server.primary_source_for({
            "source": "Gaia public start-session page",
            "source_url": "https://partner-next.gaiacharge.com/cpo/jump/fr-grn-ees-val-alb-artesans-1/CIRCUTOR-0786-1",
            "sources": [],
        })
        electro = server.primary_source_for({
            "source": "Electromaps web-map API",
            "source_url": "https://map.electromaps.com/en/p/123",
            "sources": [],
        })

        self.assertEqual(gaia["kind"], "start_page")
        self.assertEqual(gaia["quality"], "exact")
        self.assertEqual(electro["kind"], "station_page")
        self.assertEqual(electro["quality"], "exact")

    def test_rejects_nonexistent_or_unavailable_directory_points(self) -> None:
        bad = {
            "name": "Calle Xàtiva Valencia NEXISTE PAS",
            "status": "VERIFY",
            "sources": [{"evidence": "Chargemap public map pool is_free=True; operational_status=OUT_OF_ORDER; availability_status=UNAVAILABLE"}],
        }
        self.assertIsNotNone(server.reject_station_reason(bad))

        for name in [
            "Carrer del Poeta Llombart - Port de Sagunt INEXISTANT",
            "Carrefour València SUPPRIMÉ",
            "Avenida Puerto de Sagunto DEMONTEE",
            "Parc de l'Estacio DOUBLON",
            "Old duplicate charger",
        ]:
            self.assertIsNotNone(
                server.reject_station_reason({
                    "name": name,
                    "status": "AVAILABLE",
                    "sources": [{"evidence": "Chargemap public map pool is_free=True"}],
                }),
                name,
            )

        good = {
            "name": "Verified cheap charger",
            "status": "AVAILABLE",
            "sources": [{"evidence": "Live source says AVAILABLE"}],
            "available": 1,
        }
        self.assertIsNone(server.reject_station_reason(good))

    def test_overnight_window_wraps_across_midnight(self) -> None:
        # 22:00-06:00: start > end in minutes, so pricing_window_is_current takes the
        # wraparound branch (server.py: `minute >= start or minute < end`) instead of
        # the same-day `start <= minute < end` branch exercised by the tests above.
        overnight = {"valid_from": "22:00", "valid_until": "06:00"}

        self.assertTrue(server.pricing_window_is_current(overnight, now=datetime(2026, 7, 13, 3, 0, tzinfo=server.MADRID_TZ)))
        self.assertFalse(server.pricing_window_is_current(overnight, now=datetime(2026, 7, 13, 12, 0, tzinfo=server.MADRID_TZ)))
        # Start is inclusive: exactly 22:00 is already "current".
        self.assertTrue(server.pricing_window_is_current(overnight, now=datetime(2026, 7, 13, 22, 0, tzinfo=server.MADRID_TZ)))
        # End is exclusive: exactly 06:00 has already rolled off the window.
        self.assertFalse(server.pricing_window_is_current(overnight, now=datetime(2026, 7, 13, 6, 0, tzinfo=server.MADRID_TZ)))

    def test_day_allowed_gates_by_weekday(self) -> None:
        # 2026-07-13 is a Monday, 2026-07-14 is a Tuesday (both fixed, tz-aware Madrid times).
        monday = datetime(2026, 7, 13, 12, 0, tzinfo=server.MADRID_TZ)
        tuesday = datetime(2026, 7, 14, 12, 0, tzinfo=server.MADRID_TZ)

        self.assertTrue(server.day_allowed(["MON"], monday))
        self.assertFalse(server.day_allowed(["MON"], tuesday))
        # A single string (not a list) takes the same normalized-3-letter path.
        self.assertTrue(server.day_allowed("Mon", monday))
        # Untruncated day names still normalize to the first 3 letters.
        self.assertTrue(server.day_allowed(["Monday"], monday))
        # No restriction at all (None or empty) means allowed every day.
        self.assertTrue(server.day_allowed(None, tuesday))
        self.assertTrue(server.day_allowed([], tuesday))

    def test_pricing_status_overnight_cheap_window_is_currently_valid_at_23_00(self) -> None:
        windows = [{"price": 0.15, "currency": "EUR", "unit": "kWh", "valid_from": "22:00", "valid_until": "06:00"}]

        status = server.pricing_status(windows, price_cap=0.25, now=datetime(2026, 7, 13, 23, 0, tzinfo=server.MADRID_TZ))

        self.assertTrue(status["is_cheap"])
        self.assertFalse(status["is_free"])
        self.assertTrue(status["is_currently_within_standard"])
        self.assertEqual(status["current_price_min"], 0.15)
        self.assertEqual(len(status["current_windows"]), 1)
        self.assertTrue(status["label"].startswith("Now "))


if __name__ == "__main__":
    unittest.main()
