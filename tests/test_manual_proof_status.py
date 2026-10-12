import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qa_manual_proof_status", ROOT / "scripts" / "qa_manual_proof_status.py")
qa_manual_proof_status = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qa_manual_proof_status)


def complete_field(**overrides):
    data = {
        "tested_at_local": "2026-06-23T10:00:00+02:00",
        "candidate": "Gaia Green Tech - Alboraia",
        "recordset_sha256": "a" * 64,
        "device": "iPhone + Tesla",
        "app_url": "https://voltago.app",
        "provider_source_url": "https://partner-next.gaiacharge.com/cpo/jump/example",
        "volta_card_price_text": "FREE",
        "volta_card_parking_text": "Parking: street/public",
        "volta_card_source_text": "Source: start page exact",
        "volta_card_live_or_lead_text": "Live feed is cheap and available.",
        "volta_default_screenshot": "screens/field-default.png",
        "open_now_screenshot": "screens/field-open-now.png",
        "volta_card_screenshot": "screens/volta-card.png",
        "volta_source_details_screenshot": "screens/volta-source-details.png",
        "provider_source_screenshot": "screens/provider.png",
        "arrival_or_address_evidence": "screens/arrival.png",
        "parking_terms_photo_or_note": "screens/parking.png",
        "charge_session_screenshot": "screens/session.png",
        "volta_disclosed_live_or_lead_status": True,
        "volta_disclosed_parking_cost_or_risk": True,
        "volta_source_link_opened_from_card": True,
        "volta_card_matches_provider_before_departure": True,
        "provider_page_matched": True,
        "parking_free_or_acceptable": True,
        "parking_cost_eur": 0,
        "parking_cost_disclosed_in_volta": True,
        "tesla_connector_fit": True,
        "tariff_eur_per_kwh": 0,
        "tariff_within_cap": True,
        "charge_started": True,
        "onsite_notes": "Started successfully.",
        "mismatches": [],
    }
    data.update(overrides)
    return data


def complete_voiceover(**overrides):
    data = {
        "tested_at_local": "2026-06-23T11:00:00+02:00",
        "browser_or_device": "iPhone 16 Pro Max / iOS VoiceOver",
        "app_url": "https://voltago.app",
        "viewport": "iPhone 16 Pro Max",
        "recording_or_transcript_evidence": "screens/voiceover-run.mov plus transcript below",
        "app_entry_transcript": "Volta Go, Spain overview, Not live, Search a city, Open now button not selected, Free button not selected, Leads button selected.",
        "status_update_transcript": "Status announces live refresh limited and trusted leads are shown.",
        "controls_reachable": True,
        "filter_state_announced": True,
        "status_update_announced_or_discoverable": True,
        "first_card_announcement": "Gaia card announces price, parking, proof and source.",
        "first_card_transcript": "Gaia Green Tech Alboraia, FREE, street public, live open, Source start page.",
        "first_card_includes_price_parking_proof_source": True,
        "card_actions_station_named": True,
        "card_actions_transcript": "Open in Maps and Source for Gaia Green Tech.",
        "source_details_reachable": True,
        "source_details_notes": "Source details and links reachable.",
        "source_details_transcript": "Source details announces public Gaia start page and source links with useful names.",
        "detail_claims_separate": True,
        "connector_rows_understandable": True,
        "settings_reachable": True,
        "settings_notes": "Backend field, presets, selected preset, and Test Connection were reachable.",
        "settings_transcript": "Settings, Backend address, Personal server selected, Test Connection button.",
        "settings_preset_selected_state_announced": True,
        "settings_test_connection_reachable": True,
        "open_now_fail_closed": True,
        "recovery_actions_reachable": True,
        "open_now_notes": "Open now state and recovery actions understandable.",
        "open_now_transcript": "No matching route-ready chargers, Show trusted leads button, Broaden search button.",
        "no_focus_trap": True,
        "focus_order_notes": "VoiceOver moved through controls, cards, source details, and recovery buttons without trapping.",
        "issues": [],
    }
    data.update(overrides)
    return data


class ManualProofStatusTests(unittest.TestCase):
    def test_missing_evidence_blocks_manual_gates_but_artifacts_are_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            # This unit test exercises the missing-manual-evidence branch. The checked-in
            # field packet is intentionally allowed to become stale between runs, so do
            # not let its live deployment state change the structural premise here.
            with mock.patch.object(
                qa_manual_proof_status,
                "field_packet_status",
                return_value={
                    "path": "qa/field-test-candidates.md",
                    "ok": True,
                    "missing": False,
                    "missing_tokens": [],
                    "candidate_revision_match": True,
                    "ready_for_field_test": True,
                    "expected_recordset_sha256": "a" * 64,
                    "deployed_recordset_sha256": "a" * 64,
                },
            ):
                report = qa_manual_proof_status.build_report(pathlib.Path(tmp) / "missing.json")

        self.assertTrue(report["ok"])
        self.assertFalse(report["ready_to_complete_manual_gates"])
        self.assertEqual(set(report["blocked_gates"]), {"TC-051", "TC-056"})
        self.assertEqual(report["failed_gates"], [])

    def test_complete_evidence_marks_manual_gates_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "evidence.json"
            path.write_text(json.dumps({"field_test": complete_field(), "voiceover": complete_voiceover()}), encoding="utf-8")

            with mock.patch.object(
                qa_manual_proof_status,
                "field_packet_status",
                return_value={
                    "path": "qa/field-test-candidates.md",
                    "ok": True,
                    "missing": False,
                    "missing_tokens": [],
                    "candidate_revision_match": True,
                    "ready_for_field_test": True,
                    "expected_recordset_sha256": "a" * 64,
                    "deployed_recordset_sha256": "a" * 64,
                },
            ):
                report = qa_manual_proof_status.build_report(path)

        self.assertTrue(report["ok"])
        self.assertTrue(report["ready_to_complete_manual_gates"])
        self.assertEqual(report["blocked_gates"], [])
        self.assertEqual(report["failed_gates"], [])

    def test_mismatched_production_packet_blocks_otherwise_complete_field_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "evidence.json"
            path.write_text(
                json.dumps({"field_test": complete_field(), "voiceover": complete_voiceover()}),
                encoding="utf-8",
            )
            with mock.patch.object(
                qa_manual_proof_status,
                "field_packet_status",
                return_value={
                    "path": "qa/field-test-candidates.md",
                    "ok": True,
                    "missing": False,
                    "missing_tokens": [],
                    "candidate_revision_match": False,
                    "ready_for_field_test": False,
                    "expected_recordset_sha256": "a" * 64,
                    "deployed_recordset_sha256": "b" * 64,
                },
            ):
                report = qa_manual_proof_status.build_report(path)

        self.assertTrue(report["ok"])
        self.assertFalse(report["ready_to_complete_manual_gates"])
        self.assertIn("TC-051", report["blocked_gates"])
        self.assertNotIn("TC-056", report["blocked_gates"])

    def test_prior_revision_field_evidence_stays_blocked_after_a_new_matching_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "evidence.json"
            path.write_text(
                json.dumps({
                    "field_test": complete_field(recordset_sha256="b" * 64),
                    "voiceover": complete_voiceover(),
                }),
                encoding="utf-8",
            )
            with mock.patch.object(
                qa_manual_proof_status,
                "field_packet_status",
                return_value={
                    "path": "qa/field-test-candidates.md",
                    "ok": True,
                    "missing": False,
                    "missing_tokens": [],
                    "candidate_revision_match": True,
                    "ready_for_field_test": True,
                    "expected_recordset_sha256": "a" * 64,
                    "deployed_recordset_sha256": "a" * 64,
                },
            ):
                report = qa_manual_proof_status.build_report(path)

        field_gate = next(gate for gate in report["gates"] if gate["gate"] == "TC-051")
        self.assertEqual(field_gate["status"], "blocked")
        self.assertIn("not bound", field_gate["reason"])
        self.assertIn("TC-051", report["blocked_gates"])
        self.assertNotIn("TC-056", report["blocked_gates"])

    def test_template_status_rejects_missing_manual_evidence_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "template.json"
            field = complete_field()
            voiceover = complete_voiceover()
            del field["provider_source_url"]
            del voiceover["issues"]
            path.write_text(json.dumps({"field_test": field, "voiceover": voiceover}), encoding="utf-8")

            result = qa_manual_proof_status.template_status(path)

        self.assertFalse(result["ok"])
        self.assertIn("field_test.provider_source_url", result["missing_fields"])
        self.assertIn("voiceover.issues", result["missing_fields"])

    def test_field_checklist_rejects_stale_app_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            checklist = tmp_path / "field-test-checklist.md"
            template = tmp_path / "manual-proof-evidence-template.json"
            checklist.write_text(
                "\n".join([
                    "# Field Test",
                    "Default Trusted View",
                    "Open Now",
                    "Source Launch",
                    "On-Site Reality",
                    "Evidence To Capture",
                    "Current Blocker",
                    "python3 scripts/qa_field_test_packet.py --base-url http://127.0.0.1:8772",
                ]),
                encoding="utf-8",
            )
            template.write_text(json.dumps({"field_test": complete_field(), "voiceover": complete_voiceover()}), encoding="utf-8")

            result = qa_manual_proof_status.field_checklist_status(checklist, template)

        self.assertFalse(result["ok"])
        self.assertTrue(any("field_test.app_url" in item for item in result["missing_tokens"]))

    def test_field_packet_rejects_localhost_base_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            packet = tmp_path / "field-test-candidates.md"
            template = tmp_path / "manual-proof-evidence-template.json"
            packet.write_text(
                "\n".join([
                    "# Volta Go Field-Test Candidate Packet",
                    "Generated local: 2026-07-15T10:00:00+02:00",
                    "Base URL: `http://127.0.0.1:8773`",
                    "Expected local recordset SHA-256: `expected`",
                    "Deployed recordset SHA-256: `deployed`",
                    "Candidate revision match: **yes**",
                    "Candidate status: **departure-ready revision match**",
                    "Live Route-Ready Candidates",
                    "Trusted Backup Leads",
                    "Field-Test Result Table",
                    "Stop Conditions",
                ]),
                encoding="utf-8",
            )
            template.write_text(
                json.dumps({"field_test": complete_field(), "voiceover": complete_voiceover()}),
                encoding="utf-8",
            )

            result = qa_manual_proof_status.field_packet_status(packet, template)

        self.assertFalse(result["ok"])
        self.assertTrue(any("Base URL must match" in item for item in result["missing_tokens"]))

    def test_field_packet_rejects_prior_matching_revision_against_current_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            packet = tmp_path / "field-test-candidates.md"
            template = tmp_path / "manual-proof-evidence-template.json"
            canonical = tmp_path / "free_intelligence.json"
            records = [{"id": "current", "source": "test"}]
            current_sha = qa_manual_proof_status.recordset_sha256(records)
            canonical.write_text(
                json.dumps({"records": records, "recordset_sha256": current_sha}),
                encoding="utf-8",
            )
            packet.write_text(
                "\n".join([
                    "# Volta Go Field-Test Candidate Packet",
                    "Generated local: 2026-07-15T10:00:00+02:00",
                    "Base URL: `https://voltago.app`",
                    "Expected local recordset SHA-256: `prior`",
                    "Deployed recordset SHA-256: `prior`",
                    "Candidate revision match: **yes**",
                    "Candidate status: **departure-ready revision match**",
                    "Live Route-Ready Candidates",
                    "Trusted Backup Leads",
                    "Field-Test Result Table",
                    "Stop Conditions",
                ]),
                encoding="utf-8",
            )
            template.write_text(
                json.dumps({"field_test": complete_field(), "voiceover": complete_voiceover()}),
                encoding="utf-8",
            )

            result = qa_manual_proof_status.field_packet_status(packet, template, canonical)

        self.assertTrue(result["ok"])
        self.assertFalse(result["candidate_revision_match"])
        self.assertFalse(result["ready_for_field_test"])
        self.assertEqual(result["canonical_recordset_sha256"], current_sha)

    def test_voiceover_checklist_rejects_stale_app_url_and_missing_transcript_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            checklist = tmp_path / "voiceover-checklist.md"
            template = tmp_path / "manual-proof-evidence-template.json"
            checklist.write_text(
                "\n".join([
                    "# VoiceOver Test",
                    "Map, sheet, search, and filters",
                    "Status and mode changes",
                    "Charger Card Semantics",
                    "Detail and Settings semantics",
                    "Open Now And Empty Recovery",
                    "Evidence To Capture",
                    "Current Blocker",
                    "Open http://127.0.0.1:8772/",
                ]),
                encoding="utf-8",
            )
            template.write_text(json.dumps({"field_test": complete_field(), "voiceover": complete_voiceover()}), encoding="utf-8")

            result = qa_manual_proof_status.voiceover_checklist_status(checklist, template)

        self.assertFalse(result["ok"])
        self.assertIn("VoiceOver Transcript Requirements", result["missing_tokens"])
        self.assertTrue(any("voiceover.app_url" in item for item in result["missing_tokens"]))

    def test_field_tariff_above_cap_fails_for_defect_triage(self):
        field = complete_field(tariff_eur_per_kwh=0.31, tariff_within_cap=False)

        result = qa_manual_proof_status.validate_field_evidence(field)

        self.assertEqual(result["status"], "fail_needs_defect")
        self.assertTrue(any("exceeds 0.25" in item for item in result["failures"]))
        self.assertTrue(any("tariff_within_cap=false" in item for item in result["failures"]))

    def test_field_rejects_malformed_timestamp_and_source_url(self):
        field = complete_field(tested_at_local="not-a-date", provider_source_url="not-url")

        result = qa_manual_proof_status.validate_field_evidence(field)

        self.assertEqual(result["status"], "fail_needs_defect")
        self.assertTrue(any("tested_at_local is not ISO-8601 datetime" in item for item in result["failures"]))
        self.assertTrue(any("provider_source_url must be an http(s) URL" in item for item in result["failures"]))

    def test_field_blocks_when_volta_card_disclosure_evidence_is_missing(self):
        field = complete_field(volta_card_price_text="", volta_source_link_opened_from_card=None)

        result = qa_manual_proof_status.validate_field_evidence(field)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("volta_card_price_text", result["missing"])
        self.assertIn("volta_source_link_opened_from_card", result["missing"])

    def test_voiceover_rejects_naive_timestamp_and_bad_app_url(self):
        voiceover = complete_voiceover(tested_at_local="2026-06-23T11:00:00", app_url="localhost:8773")

        result = qa_manual_proof_status.validate_voiceover_evidence(voiceover)

        self.assertEqual(result["status"], "fail_needs_defect")
        self.assertTrue(any("tested_at_local must include timezone offset" in item for item in result["failures"]))
        self.assertTrue(any("app_url must be an http(s) URL" in item for item in result["failures"]))

    def test_voiceover_issue_fails_for_triage(self):
        voiceover = complete_voiceover(issues=["Source details repeats unlabeled link."])

        result = qa_manual_proof_status.validate_voiceover_evidence(voiceover)

        self.assertEqual(result["status"], "fail_needs_defect")
        self.assertTrue(any("issues" in item for item in result["failures"]))

    def test_incomplete_voiceover_stays_blocked(self):
        voiceover = complete_voiceover(first_card_announcement="")

        result = qa_manual_proof_status.validate_voiceover_evidence(voiceover)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("first_card_announcement", result["missing"])

    def test_voiceover_blocks_when_transcript_evidence_is_missing(self):
        voiceover = complete_voiceover(recording_or_transcript_evidence="", first_card_transcript="")

        result = qa_manual_proof_status.validate_voiceover_evidence(voiceover)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("recording_or_transcript_evidence", result["missing"])
        self.assertIn("first_card_transcript", result["missing"])

    def test_voiceover_blocks_without_detail_and_settings_evidence(self):
        voiceover = complete_voiceover(
            settings_transcript="",
            settings_preset_selected_state_announced=None,
            detail_claims_separate=None,
        )

        result = qa_manual_proof_status.validate_voiceover_evidence(voiceover)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("settings_transcript", result["missing"])
        self.assertIn("settings_preset_selected_state_announced", result["missing"])
        self.assertIn("detail_claims_separate", result["missing"])

    def test_manual_evidence_blocks_when_it_targets_a_noncanonical_app_url(self):
        field = complete_field(app_url="http://127.0.0.1:8773/")
        voiceover = complete_voiceover(app_url="http://127.0.0.1:8773/")

        field_result = qa_manual_proof_status.validate_field_evidence(field, "https://voltago.app")
        voiceover_result = qa_manual_proof_status.validate_voiceover_evidence(voiceover, "https://voltago.app")

        self.assertEqual(field_result["status"], "blocked")
        self.assertEqual(voiceover_result["status"], "blocked")
        self.assertTrue(any("must match manual template" in item for item in field_result["missing"]))
        self.assertTrue(any("must match manual template" in item for item in voiceover_result["missing"]))


if __name__ == "__main__":
    unittest.main()
