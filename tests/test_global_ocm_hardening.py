"""Hostile archive and all-or-nothing admission contracts for worldwide OCM."""
from __future__ import annotations

import gzip
import io
import json
import sys
import tarfile
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import global_sources as gs
import build_global_intelligence as builder


ROOT = "ocm-export-" + gs.OCM_EXPORT_REVISION


def reference():
    return {
        "Countries": [{"ID": 2, "ISOCode": "US"}, {"ID": 5, "ISOCode": "CA"}],
        "DataProviders": [{"ID": 1, "Title": "Contributors", "IsOpenDataLicensed": True, "License": "CC BY 4.0"}],
        "ConnectionTypes": [{"ID": 32, "Title": "CCS (Type 1)"}],
        "StatusTypes": [{"ID": 50, "Title": "Operational", "IsOperational": True}],
        "UsageTypes": [{"ID": 1, "Title": "Public", "IsAccessKeyRequired": False}],
        "Operators": [{"ID": 1, "Title": "Independent operator"}],
        "SubmissionStatusTypes": [{"ID": 200, "Title": "Published", "IsLive": True}],
    }


def poi():
    return {
        "ID": 123, "DataProviderID": 1, "OperatorID": 1, "StatusTypeID": 50,
        "UsageTypeID": 1, "SubmissionStatusTypeID": 200,
        "AddressInfo": {"Title": "Fixture", "CountryID": 2, "Latitude": 40.7, "Longitude": -74},
        "Connections": [{"ID": 234, "ConnectionTypeID": 32, "PowerKW": 150, "Quantity": 2}],
    }


class ArchiveFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "snapshot.tar.gz"

    def write(self, *, rows=None, ref=None, extra=()):
        if rows is None:
            rows = [(f"{ROOT}/data/US/OCM-123.json", poi())]
        members = [*rows, (f"{ROOT}/data/referencedata.json", reference() if ref is None else ref), *extra]
        with tarfile.open(self.path, "w:gz") as archive:
            for name, payload in members:
                info = name if isinstance(name, tarfile.TarInfo) else tarfile.TarInfo(name)
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
        return self.path

    def read(self, **kwargs):
        return list(gs.iter_ocm_export(self.path, **kwargs))


class ArchiveValidationTests(ArchiveFixture, unittest.TestCase):
    def test_valid_archive_reads_references_after_pois_without_extraction(self):
        self.write()
        with mock.patch.object(tarfile.TarFile, "extractall", side_effect=AssertionError("no extraction")):
            rows = self.read()
        self.assertEqual(rows[0]["DataProvider"]["License"], "CC BY 4.0")
        self.assertEqual(rows[0]["AddressInfo"]["Country"]["ISOCode"], "US")
        self.assertNotIn("_ocm_unresolved_references", rows[0])

    def test_paths_are_validated_even_outside_selected_country(self):
        for name in (f"{ROOT}/../outside", f"{ROOT}/./outside", f"{ROOT}//outside", "/absolute",
                     "different-root/README", f"{ROOT}/bad\\name", f"{ROOT}/data/CA/not-a-station.json"):
            with self.subTest(name=name):
                self.write(extra=[(name, {})])
                with self.assertRaises(ValueError):
                    self.read(countries={"US"})

    def test_links_devices_and_sparse_members_reject_entire_archive(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            info = tarfile.TarInfo(f"{ROOT}/link")
            info.type = kind
            info.linkname = f"{ROOT}/data/US/OCM-123.json"
            with self.subTest(kind=kind):
                self.write(extra=[(info, b"")])
                with self.assertRaisesRegex(ValueError, "non-regular"):
                    self.read()

    def test_sparse_metadata_is_rejected_before_tarfile_expands_it(self):
        for headers in ({"GNU.sparse.map": "0,1"}, {"GNU.sparse.size": "1"},
                        {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"}, None):
            info = tarfile.TarInfo(f"{ROOT}/sparse")
            if headers is None:
                info.type = tarfile.GNUTYPE_SPARSE
            else:
                info.pax_headers = headers
            self.write(extra=[(info, b"")])
            with self.subTest(headers=headers), self.assertRaisesRegex(ValueError, "sparse member"):
                self.read()

    def test_duplicate_id_across_country_folders_is_rejected(self):
        self.write(extra=[(f"{ROOT}/data/CA/OCM-123.json", poi())])
        with self.assertRaisesRegex(ValueError, "duplicate POI ID"):
            self.read(countries={"US"})

    def test_missing_or_duplicate_reference_file_is_rejected(self):
        self.write(extra=[(f"{ROOT}/data/referencedata.json", reference())])
        with self.assertRaisesRegex(ValueError, "repeats referencedata"):
            self.read()
        with tarfile.open(self.path, "w:gz") as archive:
            body = json.dumps(poi()).encode()
            info = tarfile.TarInfo(f"{ROOT}/data/US/OCM-123.json")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        with self.assertRaisesRegex(ValueError, "lacks data/referencedata"):
            self.read()

    def test_filename_and_json_id_must_match_exactly(self):
        for identifier in (124, "123", True, None):
            with self.subTest(identifier=identifier):
                self.write(rows=[(f"{ROOT}/data/US/OCM-123.json", dict(poi(), ID=identifier))])
                with self.assertRaisesRegex(ValueError, "ID differs"):
                    self.read()

    def test_duplicate_json_fields_and_nonfinite_json_values_are_rejected(self):
        for body in (b'{"ID":123,"ID":124}', b'{"ID":123,"PowerKW":NaN}'):
            self.write(rows=[(f"{ROOT}/data/US/OCM-123.json", body)])
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.read()

    def test_nested_poi_shapes_are_validated_before_any_row_is_yielded(self):
        for update in ({"AddressInfo": []}, {"Connections": {}}, {"Connections": [1]},
                       {"Connections": [{"ConnectionType": "CCS"}]}, {"UsageCost": 10}):
            self.write(extra=[(f"{ROOT}/data/CA/OCM-124.json", dict(poi(), ID=124, **update))])
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "malformed"):
                next(gs.iter_ocm_export(self.path))

    def test_reference_ids_shapes_booleans_and_licences_are_checked(self):
        for table, update in (("Countries", {"ID": True}), ("Countries", {"ISOCode": "ZZ"}),
                              ("DataProviders", {"IsOpenDataLicensed": "true"}),
                              ("DataProviders", {"License": []}), ("Operators", {"Title": []}),
                              ("StatusTypes", {"IsOperational": "true"}),
                              ("UsageTypes", {"IsAccessKeyRequired": "false"})):
            ref = reference()
            ref[table][0].update(update)
            self.write(ref=ref)
            with self.subTest(table=table, update=update), self.assertRaises(ValueError):
                self.read()
        ref = reference()
        ref["DataProviders"].append(deepcopy(ref["DataProviders"][0]))
        self.write(ref=ref)
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            self.read()
        for table in gs.OCM_REFERENCE_TABLES:
            ref = reference()
            del ref[table]
            self.write(ref=ref)
            with self.subTest(missing=table), self.assertRaisesRegex(ValueError, "missing or empty"):
                self.read()

    def test_embedded_provider_grant_cannot_override_reference_licence(self):
        ref = reference()
        ref["DataProviders"][0]["License"] = "CC BY-NC 4.0"
        record = poi()
        record["DataProvider"] = reference()["DataProviders"][0]
        self.write(ref=ref, rows=[(f"{ROOT}/data/US/OCM-123.json", record)])
        expanded = self.read()[0]
        self.assertEqual(expanded["DataProvider"]["License"], "CC BY-NC 4.0")
        self.assertFalse(gs.provider_license_allowed(expanded["DataProvider"]))

    def test_contradictory_country_and_unknown_access_status_are_quarantined(self):
        updates = [{"StatusTypeID": 999}, {"UsageTypeID": 999}, {"DataProviderID": True},
                   {"DataProvider": {"ID": 999}},
                   {"AddressInfo": dict(poi()["AddressInfo"], Country={"ISOCode": "CA"})}]
        for update in updates:
            self.write(rows=[(f"{ROOT}/data/US/OCM-123.json", dict(poi(), **update))])
            with self.subTest(update=update):
                expanded = self.read()[0]
                self.assertTrue(expanded["_ocm_unresolved_references"])
                self.assertIsNone(gs.normalize_ocm(expanded, None))

    def test_nonpublic_submission_cannot_enter_candidate(self):
        ref = reference()
        ref["SubmissionStatusTypes"][0]["IsLive"] = False
        self.write(ref=ref)
        expanded = self.read()[0]
        self.assertIsNone(gs.normalize_ocm(expanded, None))

    def test_compressed_decompressed_member_and_deadline_budgets(self):
        for name, bound in (("OCM_MAX_ARCHIVE_BYTES", 10), ("OCM_MAX_DECOMPRESSED_BYTES", 1000),
                            ("OCM_MAX_MEMBERS", 1), ("OCM_MAX_READ_SECONDS", -1),
                            ("OCM_MAX_REFERENCE_BYTES", 20)):
            self.write()
            with self.subTest(bound=name), mock.patch.object(gs, name, bound), self.assertRaises(ValueError):
                self.read()

    def test_extended_header_allocation_is_bounded(self):
        info = tarfile.TarInfo(f"{ROOT}/README")
        info.pax_headers = {"comment": "x" * 65536}
        self.write(extra=[(info, b"")])
        with self.assertRaisesRegex(ValueError, "extended header"):
            self.read()

    def test_truncated_gzip_tar_and_trailing_data_are_rejected(self):
        self.write()
        compressed = self.path.read_bytes()
        raw = gzip.decompress(compressed)
        for body in (compressed[:-4], gzip.compress(raw.rstrip(b"\0")),
                     compressed + gzip.compress(b"unexpected trailing data")):
            self.path.write_bytes(body)
            with self.subTest(length=len(body)), self.assertRaises((ValueError, EOFError, tarfile.TarError)):
                self.read()

    def test_padding_and_concatenated_gzip_count_towards_budget(self):
        self.write()
        compressed = self.path.read_bytes()
        raw_size = len(gzip.decompress(compressed))
        self.path.write_bytes(compressed + gzip.compress(b"\0" * 100_000))
        with mock.patch.object(gs, "OCM_MAX_DECOMPRESSED_BYTES", raw_size + 5000), \
             self.assertRaisesRegex(ValueError, "decompressed byte limit"):
            self.read()


class TransactionalAdmissionTests(ArchiveFixture, unittest.TestCase):
    def test_rollback_preserves_baseline_prior_sources_identities_and_counters(self):
        store = gs.CandidateStore(self.directory / "candidate.sqlite")
        self.addCleanup(store.close)
        baseline = {"id": "baseline", "lat": 1, "lon": 1, "country_code": "ES"}
        previous = {"id": "previous", "lat": 2, "lon": 2, "country_code": "FR"}
        staged = {"id": "new", "lat": 3, "lon": 3, "country_code": "US"}
        store.add(baseline, baseline=True)
        store.add(previous)
        stats = store.stats()
        with self.assertRaisesRegex(ValueError, "late failure"):
            with store.source_transaction():
                store.add(staged)
                store.add(staged)
                raise ValueError("late failure")
        self.assertEqual(list(store.rows()), [baseline, previous])
        self.assertEqual(store.stats(), stats)
        self.assertEqual(store.add(staged), "added")

    def test_changed_source_file_rolls_back_successful_stream(self):
        self.write()
        args = SimpleNamespace(ocm_archive=self.path, offline=True,
                               ocm_revision=gs.OCM_EXPORT_REVISION, cache_dir=self.directory)
        store = gs.CandidateStore(self.directory / "candidate.sqlite")
        self.addCleanup(store.close)
        with mock.patch.object(gs, "file_sha256", side_effect=["first", "changed"]):
            manifest = builder.run_ocm(args, None, store)
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["accepted"], 0)
        self.assertEqual(manifest["rolled_back_records"], 1)
        self.assertEqual(manifest["accepted_by_provider_id"], {})
        self.assertEqual(list(store.rows()), [])
        self.assertEqual(store.stats().get("added", 0), 0)
        self.assertIn("changed during validation", manifest["errors"][0])


if __name__ == "__main__":
    unittest.main()
