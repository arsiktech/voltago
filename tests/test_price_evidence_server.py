"""Additive price annotations must survive transport without granting authority."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from scripts.price_evidence import parse_electromaps_price, price_evidence_counts, sanitize_electromaps


def evidence(kind="electromaps_community", price=0.0):
    labels = {
        "repsol_network_tariff": "Repsol published tariff · station may differ",
        "electromaps_community": "Electromaps community price · 2025-08-16",
        "chargemap_pass": "Price with Chargemap Pass",
    }
    urls = {
        "repsol_network_tariff": "https://www.repsol.es/particulares/vehiculos/movilidad-electrica/recarga-fuera-de-casa/",
        "electromaps_community": "https://www.electromaps.com/mapi/v2/locations/757380",
        "chargemap_pass": "https://map.chargemap.com/pool-prices/v3/prices/315448?locale=es-es&noBootstrap=true&noFooter=true",
    }
    return {
        "kind": kind, "source": labels[kind].split(" · ")[0], "label": labels[kind],
        "text": "0 €/kWh; parking 0 € reported separately", "source_url": urls[kind],
        "source_updated_at": "2025-08-16T19:12:42+0000" if kind == "electromaps_community" else None,
        "fetched_at": "2026-10-20T10:00:00Z", "price_per_kwh": price, "currency": "EUR",
        "tariff_details": {"parking_price": "0€, están en la acera"},
        "rates": [{"kind": "energy", "label": None, "min": price, "max": None,
                   "from": False, "unit": "kWh", "after_minutes": None}],
    }


def registry_row(identifier="registry-1", **updates):
    row = {
        "id": identifier, "name": "Public charger Test Road", "address": "Test Road 1",
        "lat": 39.47, "lon": -0.37, "country_code": "ES",
        "source": "Official registry", "source_url": "https://nap.dgt.es/",
        "sources": [{"name": "Official registry", "url": "https://nap.dgt.es/"}],
        "reliability": "official-snapshot", "price_min": None, "is_free": False, "is_cheap": False,
        "location_claim": {"method": "public_source_coordinate"},
        "connectors": [{"label": "Type 2", "type": "Type 2", "power_kw": 22}],
        "parking": {"kind": "unknown"},
    }
    row.update(updates)
    return row


def live_row(identifier="registry-1", price=0.60):
    row = registry_row(
        identifier, source="MapaREVE public live OCPI", source_url="https://www.mapareve.es/",
        sources=[{"name": "MapaREVE public live OCPI", "url": "https://www.mapareve.es/"}],
        reliability="official-live", price_min=price, is_free=price == 0,
        is_cheap=price is not None and price <= 0.25,
        status="AVAILABLE", available=1, total_evse=1, last_seen=server.now_iso(),
        parking={"kind": "street_or_municipal"},
        connectors=[{"label": "Type 2", "type": "Type 2", "power_kw": 22, "status": "AVAILABLE"}],
    )
    return server.enrich_station(row, live=True)


class PriceEvidenceServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "free_intelligence.json"
        (self.directory / "seed_leads.json").write_text("[]")
        (self.directory / "source_policy_exclusions.json").write_bytes(
            (server.DATA / "source_policy_exclusions.json").read_bytes())
        self.write_rows([])
        for patcher in (
            patch.object(server, "DATA", self.directory),
            patch.object(server, "intelligence_path", return_value=self.source),
            patch.object(server.community_store, "summaries", return_value={"stations": {}}),
            patch.object(server.community_store, "generation", return_value=0),
            patch.dict(server._INTEL_RAW, {"mtime": None, "data": None}, clear=True),
            patch.dict(server._INTEL_ENRICHED, {"key": None}, clear=True),
            patch.dict(server._INTEL_SCOPED, {}, clear=True),
            patch.dict(server.CACHE, {}, clear=True),
            patch.dict(server.GAIA_CACHE, {}, clear=True),
            patch.dict(server._GLOBAL_SOURCE_COUNTS, {}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_rows(self, rows):
        self.source.write_text(json.dumps({
            "records": rows, "generated_at": "2026-10-20T10:00:00Z",
            "price_evidence_counts": price_evidence_counts(rows),
        }), encoding="utf-8")

    def live_response(self, rows, **filters):
        params = {"include_leads": ["true"], "max_price": ["5.0"],
                  "bbox": ["39.30,-0.55,39.62,-0.20"], "force": ["true"]}
        params.update({name: [str(value)] for name, value in filters.items()})
        with (
            patch.object(server, "fetch_mapareve", return_value={
                "stations": deepcopy(rows), "raw_count": len(rows), "cache": {},
                "fetched_at": server.now_iso(),
            }),
            patch.object(server, "fetch_gaia_live", return_value={
                "stations": [], "raw_count": 0, "cache": {}, "errors": [],
            }),
            patch.object(server, "fetch_electromaps_live") as historical_live,
        ):
            status, payload = server.live_response(params)
        self.assertEqual(status, 200, payload)
        historical_live.assert_not_called()
        return payload

    def test_stored_normalized_compact_summary_and_detail_keep_dated_evidence(self):
        claim = evidence()
        self.write_rows([registry_row(price_evidence=[claim])])
        intel = server.intelligence_response({})["records"][0]
        status, overview = server.overview_response({"compact": ["true"], "summary": ["true"]}, 5.0, False)
        self.assertEqual(status, 200)
        summary = overview["leads"][0]
        status, detail = server.station_detail_response({"id": ["registry-1"], "snapshot": [summary["detail_snapshot"]]})
        self.assertEqual(status, 200, detail)
        for row in (intel, summary, detail["station"]):
            self.assertEqual(row["price_evidence"], [claim])
            self.assertIsNone(row["price_min"])
            self.assertFalse(row["is_free"])
            self.assertFalse(row["is_cheap"])
            self.assertFalse(row["verification"]["price_verified"])
            self.assertFalse(row["verification"]["live_verified"])
            self.assertFalse(server.route_ready_now(row))
            self.assertEqual(row["parking"]["kind"], "unknown")
        self.assertEqual(overview["price_evidence_counts"], price_evidence_counts([intel]))
        self.assertEqual(intel["price_evidence"][0]["source_updated_at"], "2025-08-16T19:12:42+0000")

    def test_zero_annotation_cannot_enter_free_or_cheap_overview_filters(self):
        self.write_rows([
            registry_row("unknown", price_evidence=[evidence()]),
            registry_row("expensive", lat=39.48, price_min=0.60, price_evidence=[evidence(price=0.02)]),
        ])
        for params, cap in (({"free_only": ["true"]}, 5.0), ({}, 0.25)):
            with self.subTest(params=params, cap=cap):
                status, payload = server.overview_response(params, cap, False)
                self.assertEqual(status, 200)
                self.assertEqual(payload["leads"], [])
        self.assertEqual(len(server.overview_response({}, 5.0, False)[1]["leads"]), 2)

    def test_actual_parsed_parking_text_reaches_clients_without_primary_parking_authority(self):
        fixture = Path(__file__).parent / "fixtures" / "price_evidence" / "electromaps.json"
        detail = json.loads(fixture.read_text(encoding="utf-8"))
        claim = parse_electromaps_price(sanitize_electromaps(detail), "2026-10-20T10:00:00Z")
        self.write_rows([registry_row(price_evidence=[claim])])
        intel = server.intelligence_response({})["records"][0]
        status, overview = server.overview_response({"compact": ["true"], "summary": ["true"]}, 5.0, False)
        self.assertEqual(status, 200)
        summary = overview["leads"][0]
        status, response = server.station_detail_response({"id": ["registry-1"], "snapshot": [summary["detail_snapshot"]]})
        self.assertEqual(status, 200)
        for row in (intel, summary, response["station"]):
            self.assertEqual(row["price_evidence"][0]["parking_price"], detail["parking_price"])
            self.assertEqual(row["price_evidence"][0]["tariff_details"]["parking_price"], detail["parking_price"])
            self.assertEqual(row["price_evidence"][0]["text"], detail["charge_price"])
            self.assertEqual(row["parking"]["kind"], "unknown")
            self.assertIsNone(row["price_min"])
            self.assertFalse(row["verification"]["price_verified"])

    def test_live_official_price_parking_authority_and_rank_beat_zero_annotation(self):
        original = live_row(price=0.60)
        donor = registry_row(price_evidence=[evidence()])
        self.write_rows([donor])
        baseline = server.enrich_station(deepcopy(original), live=True)
        result = self.live_response([original])["stations"][0]
        self.assertEqual(result["price_evidence"], donor["price_evidence"])
        for field in ("price_min", "current_price_min", "price_label", "is_free", "is_cheap",
                      "charge_claim", "parking_claim", "parking", "verification", "decision"):
            self.assertEqual(result.get(field), baseline.get(field), field)
        self.assertFalse(server.route_ready_now(result))
        self.assertEqual(result["corroboration"]["families"], ["mapareve"])
        self.assertFalse(any("electromaps" in source.get("url", "") for source in result["sources"]))
        # An allowed 0.02 annotation cannot rescue a primary 0.60 tariff at 0.25.
        self.assertEqual(self.live_response([original], max_price="0.25")["stations"], [])
        self.assertEqual(self.live_response([original], available="true")["stations"], [])

    def test_cheap_live_station_stays_route_ready_with_conflicting_pass_price(self):
        original = live_row(price=0.20)
        claim = evidence("chargemap_pass", price=0.799)
        self.write_rows([registry_row(price_evidence=[claim])])
        payload = self.live_response([original], available="true", max_price="0.25")
        self.assertEqual([row["id"] for row in payload["stations"]], ["registry-1"])
        row = payload["stations"][0]
        self.assertEqual(row["price_min"], 0.20)
        self.assertTrue(row["verification"]["price_verified"])
        self.assertTrue(server.route_ready_now(row))
        self.assertEqual(row["price_evidence"], [claim])
        self.assertEqual(payload["price_evidence_counts"], price_evidence_counts([row]))
        self.assertEqual(payload["electromaps"]["promoted_live"], 0)

    def test_annotations_do_not_change_recommendation_or_primary_source(self):
        for price in (None, 0, 0.20, 0.60):
            with self.subTest(price=price):
                source = registry_row(price_min=price)
                baseline = server.enrich_station(deepcopy(source), live=False)
                source["price_evidence"] = [evidence(), evidence("chargemap_pass", 0.799)]
                result = server.enrich_station(source, live=False)
                for field in ("recommendation_score", "primary_source", "verification", "decision", "charge_claim", "parking_claim"):
                    self.assertEqual(result[field], baseline[field], field)

    def test_existing_live_merge_winners_retain_annotations_without_source_donation(self):
        claim = evidence()
        first = live_row("shared", 0.20)
        first["price_evidence"] = [claim]
        replacement = live_row("shared", 0.20)
        replacement["source"] = "Gaia public page"
        merged = server.merge_gaia_stations([deepcopy(first)], [deepcopy(replacement)])
        self.assertEqual(merged[0]["source"], "Gaia public page")
        self.assertEqual(merged[0]["price_evidence"], [claim])
        stale = deepcopy(replacement)
        stale["_volta_stale_live_cache"] = True
        stale["price_evidence"] = [evidence("chargemap_pass", 0.799)]
        merged = server.merge_gaia_stations([deepcopy(first)], [stale])
        self.assertEqual(merged[0]["source"], first["source"])
        self.assertEqual(len(merged[0]["price_evidence"]), 2)
        duplicate = registry_row("shared", price_evidence=[claim])
        live = [deepcopy(first)]
        self.assertEqual(server.remove_duplicate_leads([duplicate], live), [])
        self.assertEqual(live[0]["price_evidence"], [claim])
        self.assertEqual(first["sources"], live[0]["sources"][:1])
        live[0]["price_evidence"][0]["text"] = "changed copy"
        self.assertEqual(claim["text"], "0 €/kWh; parking 0 € reported separately")

    def test_price_urls_do_not_change_existing_station_identity(self):
        shared = evidence()
        a = registry_row("a", name="North independent site", address="North Road 1", price_evidence=[shared])
        b = registry_row("b", lat=39.4703, name="South unrelated charging", address="South Lane 8",
                         source_url="https://nap.dgt.es/other-record",
                         sources=[{"name": "Official registry", "url": "https://nap.dgt.es/other-record"}],
                         price_evidence=[shared])
        # An identical annotation URL is not a station identifier or source family.
        self.assertFalse(server.likely_same_station(a, b))
        self.assertEqual(server.station_families(a), set())
        self.assertEqual(server.remove_duplicate_leads([b], [a]), [b])

    def test_strategy_reports_actual_counts_and_owner_policy_without_rights_clearance(self):
        rows = [registry_row(price_evidence=[evidence(), evidence("chargemap_pass", 0.799)])]
        self.write_rows(rows)
        # Runtime policy partitioning can change retained rows after export. Never
        # present stale artifact counters as the number of retained annotations.
        document = json.loads(self.source.read_text())
        document["price_evidence_counts"]["total"] = 999
        document["price_evidence_counts"]["repsol_network_tariff"] = 999
        self.source.write_text(json.dumps(document))
        strategy = server.requested_source_strategy()
        self.assertEqual(strategy["price_evidence_counts"], price_evidence_counts(rows))
        apps = {row["name"]: row for row in strategy["requested_apps"]}
        for name, count in (("Repsol", 0), ("Electromaps", 1), ("Chargemap", 1)):
            self.assertEqual(apps[name]["status"], "active-additive-price-only-bounded")
            self.assertEqual(apps[name]["decision_type"], "owner-collection-policy-not-provider-licence")
            self.assertEqual(apps[name]["records_now"]["additive_price_evidence"], count)
            self.assertEqual(apps[name]["records_now"]["direct_station_records"], 0)
            self.assertIn("5.2.2", apps[name]["risk"])
            self.assertIn("not a provider licence", apps[name]["risk"])
        self.assertEqual(apps["Consum store locator"]["status"], "link-only-rights-blocked")

    def test_historical_electromaps_live_helpers_make_no_requests(self):
        self.assertFalse(server.ELECTROMAPS_DIRECT_ENABLED)
        with patch.object(server.urllib.request, "urlopen") as network:
            self.assertIsNone(server._electromaps_detail("757380"))
            payload = server.fetch_electromaps_live([registry_row()], max_price=5.0, fetched_at=server.now_iso())
        self.assertEqual(payload["stations"], [])
        self.assertEqual(payload["checked_ids"], set())
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
