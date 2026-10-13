#!/usr/bin/env python3
"""Current OpenChargeMap export intake: licensing, continuity, archive safety, dates, transport.

Fixtures reproduce the real compact shape of ``ocm-export`` files (lookup IDs only, no nested
ConnectionType/StatusType/Country objects) and the real nested shape of the legacy
``ocm-data`` feed. Archives are built in memory; nothing here touches the network.
"""
from __future__ import annotations

import gzip
import io
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_sources as ing  # noqa: E402
import ocm_export as ocm  # noqa: E402
from station_identity import preserve_record_ids, distance_m

SHA = "8e3bedca48ca94807d96b2f5e7ee02cffe63ae54"
COMMIT_DATE = "2026-04-22T10:59:34Z"
TOP = f"ocm-export-{SHA}"

REFERENCE = {
    "Countries": [{"ID": 1, "ISOCode": "GB", "Title": "United Kingdom"}, {"ID": 210, "ISOCode": "ES", "Title": "Spain"}],
    "ConnectionTypes": [
        {"ID": 25, "Title": "Type 2 (Socket Only)"},
        {"ID": 33, "Title": "CCS (Type 2)"},
        {"ID": 0, "Title": "Unknown"},
    ],
    "StatusTypes": [
        {"ID": 0, "Title": "Unknown", "IsOperational": None},
        {"ID": 30, "Title": "Temporarily Unavailable", "IsOperational": True},
        {"ID": 50, "Title": "Operational", "IsOperational": True},
        {"ID": 100, "Title": "Not Operational", "IsOperational": False},
        {"ID": 150, "Title": "Planned For Future Date", "IsOperational": False},
        {"ID": 200, "Title": "Removed (Decommissioned)", "IsOperational": False},
    ],
    "UsageTypes": [{"ID": 0, "Title": "(Unknown)"}, {"ID": 1, "Title": "Public"}, {"ID": 4, "Title": "Public - Membership Required"}],
    "Operators": [
        {"ID": 1, "Title": "(Unknown Operator)", "IsPrivateIndividual": None},
        {"ID": 3279, "Title": "Fenie Energía (Spain)", "IsPrivateIndividual": False},
        {"ID": 4001, "Title": "Consum", "IsPrivateIndividual": False},
    ],
    "ChargerTypes": [{"ID": 2, "Title": "Level 2 : Medium (Over 2kW)"}, {"ID": 3, "Title": "Level 3:  High (Over 40kW)"}],
    "CurrentTypes": [{"ID": 10, "Title": "AC (Single-Phase)"}, {"ID": 20, "Title": "AC (Three-Phase)"}, {"ID": 30, "Title": "DC"}],
    "SubmissionStatusTypes": [
        {"ID": 100, "Title": "Imported and Published", "IsLive": True},
        {"ID": 200, "Title": "Submission Published", "IsLive": True},
        {"ID": 1, "Title": "Submission Under Review", "IsLive": False},
    ],
    "DataProviders": [
        {"ID": 1, "Title": "Open Charge Map Contributors", "IsOpenDataLicensed": True,
         "License": "Licensed under Creative Commons Attribution 4.0 International (CC BY 4.0)"},
        {"ID": 25, "Title": "Catalan Energy Institute (ICAEN)", "IsOpenDataLicensed": True, "License": "General public data"},
        {"ID": 27, "Title": "Place To Plug", "IsOpenDataLicensed": False, "License": None},
        {"ID": 33, "Title": "Gaia Green Tech SLU", "IsOpenDataLicensed": True, "License": "CC-0"},
        {"ID": 38, "Title": "PowerGo", "IsOpenDataLicensed": True, "License": "Licensed under CC0 by data sharing agreement"},
        {"ID": 40, "Title": "ShareAlike Example", "IsOpenDataLicensed": True,
         "License": "Licensed under Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)"},
    ],
}


def compact_poi(poi_id: int, *, title: str = "Charger", lat: float = 39.53, lon: float = -0.35,
                provider: int = 1, status: int = 50, operator: int = 1, usage: int = 1,
                cost: Optional[str] = "", country: int = 210, submission: int = 200,
                kw: float = 22, conn_type: int = 25) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "DateLastVerified": "2025-10-30T18:01:00Z",
        "ID": poi_id,
        "UUID": f"00000000-0000-0000-0000-{poi_id:012d}",
        "DataProviderID": provider,
        "OperatorID": operator,
        "UsageTypeID": usage,
        "AddressInfo": {"ID": poi_id + 367, "Title": title, "AddressLine1": "Carrer de Prova", "Postcode": "46134",
                        "CountryID": country, "Latitude": lat, "Longitude": lon, "DistanceUnit": 0},
        "Connections": [{"ID": poi_id * 10, "ConnectionTypeID": conn_type, "StatusTypeID": status, "LevelID": 2,
                         "PowerKW": kw, "CurrentTypeID": 20, "Quantity": 2}],
        "NumberOfPoints": 2,
        "StatusTypeID": status,
        "DateLastStatusUpdate": "2025-10-30T18:01:00Z",
        "DateCreated": "2022-09-23T14:35:00Z",
        "SubmissionStatusTypeID": submission,
        # Contributor media/comments exist upstream and must never be reused.
        "MediaItems": [{"ID": 1, "ItemURL": "https://example.invalid/photo.jpg", "Comment": "private note",
                        "IsEnabled": True, "IsVideo": False, "User": {"Username": "someone"}}],
        "GeneralComments": "free-text contributor comment",
    }
    if cost is not None:
        row["UsageCost"] = cost
    return row


def legacy_poi(poi_id: int, *, title: str, lat: float, lon: float, provider: int = 1,
               cost: Optional[str] = "Free", status_title: str = "Operational") -> Dict[str, Any]:
    return {
        "ID": poi_id, "UUID": f"legacy-{poi_id}", "DataProviderID": provider, "UsageCost": cost, "NumberOfPoints": 2,
        "AddressInfo": {"Title": title, "AddressLine1": "Calle Vieja", "Town": "Foios", "Postcode": "46134",
                        "Latitude": lat, "Longitude": lon, "Country": {"ISOCode": "ES"}},
        "Connections": [{"ID": 7, "ConnectionTypeID": 25, "ConnectionType": {"Title": "Type 2 (Socket Only)"},
                         "StatusType": {"Title": status_title}, "PowerKW": 22, "Quantity": 2}],
        "StatusType": {"Title": status_title}, "UsageType": {"Title": "Public"},
        "DateLastVerified": None, "DateLastStatusUpdate": {"$date": "2018-01-02T03:04:00Z"},
        "SubmissionStatus": {"Title": "Submission Published"}, "MediaItems": None,
    }


def make_archive(path: Path, pois: Dict[str, Any], *, reference: Any = REFERENCE, extra=None, top: str = TOP,
                 raw_files: Optional[Dict[str, bytes]] = None, include_reference: bool = True) -> None:
    with tarfile.open(path, "w:gz") as tf:
        def add_bytes(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo(top); info.type = tarfile.DIRTYPE; tf.addfile(info)
        if include_reference:
            add_bytes(f"{top}/data/referencedata.json", json.dumps(reference).encode())
        for poi_id, row in pois.items():
            add_bytes(f"{top}/data/ES/OCM-{poi_id}.json", json.dumps(row).encode())
        for name, data in (raw_files or {}).items():
            add_bytes(f"{top}/{name}", data)
        if extra:
            extra(tf)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._buf = io.BytesIO(body)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class Network:
    """Fake ``policy_urlopen`` that serves pinned fixtures and records every request."""

    def __init__(self, *, manifest: bytes, archive: bytes, legacy: Optional[bytes]) -> None:
        self.requests: List[urllib.request.Request] = []
        self.manifest, self.archive, self.legacy = manifest, archive, legacy

    def __call__(self, request: urllib.request.Request, *, timeout: float, cookiejar=None):
        self.requests.append(request)
        url = request.full_url
        if url == ocm.OCM_EXPORT_COMMIT_API_URL:
            return _FakeResponse(self.manifest)
        if url == ocm.archive_url(SHA):
            return _FakeResponse(self.archive)
        if url == ocm.OCM_LEGACY_URL:
            if self.legacy is None:
                raise OSError("legacy unreachable")
            return _FakeResponse(self.legacy)
        raise AssertionError(f"unexpected URL {url}")


MANIFEST = json.dumps({"sha": SHA, "commit": {"committer": {"date": COMMIT_DATE}}}).encode()


def legacy_gz(rows: List[Dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as fh:
        for row in rows:
            fh.write((json.dumps(row) + "\n").encode())
    return buf.getvalue()


class TempCacheCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.cache = self.tmp / "cache"
        self.cache.mkdir()
        patcher = mock.patch.object(ing, "CACHE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        floor = mock.patch.object(ocm, "MIN_COUNTRY_FILES", 1)
        floor.start()
        self.addCleanup(floor.stop)
        ing.FETCH_EVIDENCE.clear()

    def archive_bytes(self, pois, **kw) -> bytes:
        path = self.tmp / "a.tar.gz"
        make_archive(path, pois, **kw)
        return path.read_bytes()

    def run_adapter(self, pois, legacy_rows: Optional[List[Dict[str, Any]]] = None, *, previous_records=None, **kw):
        for path in self.cache.glob("ocm_*"):
            path.unlink()
        net = Network(manifest=MANIFEST, archive=self.archive_bytes(pois, **kw),
                      legacy=legacy_gz(legacy_rows) if legacy_rows is not None else None)
        records: List[Dict[str, Any]] = []
        with mock.patch.object(ing, "policy_urlopen", net):
            ing.source_openchargemap(records, previous_records=previous_records)
        return records, net


class LicenseClassTests(unittest.TestCase):
    def test_only_explicit_cc_by_4_and_cc0_pass(self) -> None:
        self.assertEqual(ocm.license_class("Licensed under Creative Commons Attribution 4.0 International (CC BY 4.0)"), "CC-BY-4.0")
        self.assertEqual(ocm.license_class("CC-0"), "CC0")
        self.assertEqual(ocm.license_class("Licensed under CC0 by data sharing agreement"), "CC0")

    def test_missing_ambiguous_restricted_or_unflagged_fail_closed(self) -> None:
        self.assertIsNone(ocm.license_class(None, open_licensed=False))
        self.assertIsNone(ocm.license_class("", open_licensed=True))
        self.assertIsNone(ocm.license_class("General public data"))
        self.assertIsNone(ocm.license_class("Licensed under Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)"))
        self.assertIsNone(ocm.license_class("Attribution-NonCommercial-ShareAlike 3.0"))
        self.assertIsNone(ocm.license_class("CC BY 4.0", open_licensed=None))
        self.assertIsNone(ocm.license_class("CC BY 4.0", open_licensed="yes"))


class CompactResolutionTests(unittest.TestCase):
    def test_lookup_ids_resolve_and_media_comments_are_dropped(self) -> None:
        ref = ocm.build_reference(REFERENCE)
        poi = ocm.expand_poi(compact_poi(203975, operator=3279, provider=1), ref)
        self.assertEqual(poi["AddressInfo"]["Country"]["ISOCode"], "ES")
        self.assertEqual(poi["Connections"][0]["ConnectionType"]["Title"], "Type 2 (Socket Only)")
        self.assertEqual(poi["Connections"][0]["CurrentType"]["Title"], "AC (Three-Phase)")
        self.assertEqual(poi["Connections"][0]["Level"]["Title"], "Level 2 : Medium (Over 2kW)")
        self.assertEqual(poi["StatusType"]["Title"], "Operational")
        self.assertEqual(poi["UsageType"]["Title"], "Public")
        self.assertEqual(poi["OperatorInfo"]["Title"], "Fenie Energía (Spain)")
        self.assertEqual(poi["SubmissionStatus"]["IsLive"], True)
        for dropped in ("MediaItems", "GeneralComments", "UserComments"):
            self.assertNotIn(dropped, poi)

    def test_poi_id_is_the_json_id_not_the_address_id(self) -> None:
        poi = ocm.expand_poi(compact_poi(203975), ocm.build_reference(REFERENCE))
        self.assertEqual(poi["ID"], 203975)
        self.assertNotEqual(poi["AddressInfo"]["ID"], poi["ID"])

    def test_unknown_lookup_ids_become_none_not_guesses(self) -> None:
        row = compact_poi(5, conn_type=999)
        row["Connections"][0]["CurrentTypeID"] = None
        poi = ocm.expand_poi(row, ocm.build_reference(REFERENCE))
        self.assertIsNone(poi["Connections"][0]["ConnectionType"])
        self.assertIsNone(poi["Connections"][0]["CurrentType"])

    def test_reference_requires_exactly_one_spain_country(self) -> None:
        bad = deepcopy(REFERENCE)
        bad["Countries"] = [{"ID": 1, "ISOCode": "GB"}]
        with self.assertRaises(ocm.OcmExportError):
            ocm.build_reference(bad)


class ArchiveSafetyTests(TempCacheCase):
    def test_extended_headers_and_gzip_trailing_bytes_count_toward_bound(self) -> None:
        def extra(tf):
            info = tarfile.TarInfo(f"{TOP}/extra.txt")
            info.pax_headers = {"comment": "x" * 32768}
            tf.addfile(info, io.BytesIO())
        for data in (self.build({1: compact_poi(1)}, extra=extra),
                     self.build({1: compact_poi(1)}) + gzip.compress(b"x" * 32768)):
            with self.subTest(size=len(data)), mock.patch.object(ocm, "MAX_ARCHIVE_TOTAL_BYTES", 20000):
                with self.assertRaisesRegex(ocm.OcmExportError, "byte bound"):
                    self.read(data)

    def test_legacy_line_read_is_bounded_before_allocation_and_has_deadline(self) -> None:
        path = self.tmp / "legacy.gz"
        path.write_bytes(gzip.compress(b"x" * 65536))
        with self.assertRaisesRegex(ocm.OcmExportError, "byte bound"):
            ocm.read_legacy_es_rows(path, max_bytes=128)
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.readline.return_value = b"x" * 129
        with mock.patch.object(ocm.gzip, "open", return_value=stream):
            with self.assertRaises(ocm.OcmExportError):
                ocm.read_legacy_es_rows(path, max_bytes=128)
        stream.readline.assert_called_once_with(129)
        with self.assertRaisesRegex(ocm.OcmExportError, "time budget"):
            ocm.read_legacy_es_rows(path, max_seconds=-1)

    def test_duplicate_and_malformed_references_reject_instead_of_overwriting(self) -> None:
        for table in REFERENCE:
            for bad_row in (None, {"ID": "bad"}, deepcopy(REFERENCE[table][0])):
                with self.subTest(table=table, row=bad_row):
                    ref = deepcopy(REFERENCE)
                    ref[table].append(bad_row)
                    with self.assertRaises(ocm.OcmExportError):
                        ocm.build_reference(ref)
        ref = deepcopy(REFERENCE)
        ref["DataProviders"].append({**ref["DataProviders"][1], "License": "CC BY 4.0"})
        with self.assertRaises(ocm.OcmExportError):
            ocm.build_reference(ref)

    def test_legacy_storage_duplicates_collapse_but_conflicting_facts_fail(self) -> None:
        old = legacy_poi(1, title="Prior", lat=39.7, lon=-0.3)
        path = self.tmp / "legacy.gz"
        path.write_bytes(legacy_gz([{**old, "_id": "storage-1"}, {**old, "_id": "storage-2"}]))
        self.assertEqual(list(ocm.read_legacy_es_rows(path)), [1])
        path.write_bytes(legacy_gz([old, {**old, "UsageCost": "0.59 €/kWh"}]))
        with self.assertRaisesRegex(ocm.OcmExportError, "conflicting duplicate"):
            ocm.read_legacy_es_rows(path)

    def read(self, data: bytes, **kw):
        path = self.tmp / "x.tar.gz"
        path.write_bytes(data)
        return ocm.read_export_archive(path, SHA, **kw)

    def build(self, pois, **kw) -> bytes:
        return self.archive_bytes(pois, **kw)

    def test_valid_archive_reads_rows_by_poi_id(self) -> None:
        snap = self.read(self.build({203975: compact_poi(203975)}))
        self.assertEqual(sorted(snap.pois), [203975])
        self.assertEqual(snap.commit_sha, SHA)

    def test_symlink_hardlink_and_device_members_reject_the_whole_snapshot(self) -> None:
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            def extra(tf: tarfile.TarFile, kind=kind) -> None:
                info = tarfile.TarInfo(f"{TOP}/data/ES/OCM-9.json")
                info.type = kind
                info.linkname = "/etc/passwd"
                tf.addfile(info)
            with self.subTest(kind=kind), self.assertRaises(ocm.OcmExportError):
                self.read(self.build({1: compact_poi(1)}, extra=extra))

    def test_traversal_absolute_and_wrong_toplevel_paths_reject(self) -> None:
        for name in ("../escape.json", f"{TOP}/../escape.json", "/abs/OCM-1.json", f"{TOP}/data/../../x"):
            def extra(tf: tarfile.TarFile, name=name) -> None:
                info = tarfile.TarInfo(name); info.size = 2
                tf.addfile(info, io.BytesIO(b"{}"))
            with self.subTest(name=name), self.assertRaises(ocm.OcmExportError):
                self.read(self.build({1: compact_poi(1)}, extra=extra))
        with self.assertRaises(ocm.OcmExportError):
            self.read(self.build({1: compact_poi(1)}, top="ocm-export-" + "0" * 40))

    def test_unexpected_country_dir_files_and_wrong_names_reject(self) -> None:
        with self.assertRaises(ocm.OcmExportError):
            self.read(self.build({1: compact_poi(1)}, raw_files={"data/ES/evil.sh": b"echo"}))
        with self.assertRaises(ocm.OcmExportError):
            self.read(self.build({1: compact_poi(1)}, raw_files={"data/ES/OCM-2.json.bak": b"{}"}))

    def test_other_countries_and_repo_files_are_never_parsed(self) -> None:
        snap = self.read(self.build({1: compact_poi(1)}, raw_files={"data/US/OCM-5.json": b"not json", "README.md": b"x" * 10}))
        self.assertEqual(sorted(snap.pois), [1])

    def test_missing_reference_truncated_and_non_gzip_fail_closed(self) -> None:
        with self.assertRaises(ocm.OcmExportError):
            self.read(self.build({1: compact_poi(1)}, include_reference=False))
        good = self.build({i: compact_poi(i) for i in range(1, 30)})
        with self.assertRaises(ocm.OcmExportError):
            self.read(good[: len(good) // 2])
        with self.assertRaises(ocm.OcmExportError):
            self.read(b"<html>rate limited</html>")

    def test_partial_snapshot_below_floor_fails_closed(self) -> None:
        with mock.patch.object(ocm, "MIN_COUNTRY_FILES", 5), self.assertRaises(ocm.OcmExportError):
            self.read(self.build({1: compact_poi(1), 2: compact_poi(2)}))

    def test_filename_id_mismatch_malformed_and_duplicates_are_not_trusted(self) -> None:
        pois = {i: compact_poi(i) for i in range(1, 300)}
        pois[7] = compact_poi(8)  # file says 7, JSON says 8
        snap = self.read(self.build(pois))
        self.assertNotIn(7, snap.pois)
        self.assertEqual(snap.stats["malformed_files"], 1)
        many_bad = {i: compact_poi(i) for i in range(1, 50)}
        for i in range(1, 10):
            many_bad[i] = compact_poi(i + 1000)
        with self.assertRaises(ocm.OcmExportError):
            self.read(self.build(many_bad))
        with self.assertRaises(ocm.OcmExportError):
            self.read(self.build({1: compact_poi(1)}, extra=lambda tf: _add(tf, f"{TOP}/data/ES/OCM-1.json", b"{}")))

    def test_size_member_and_time_bounds(self) -> None:
        data = self.build({1: compact_poi(1)})
        with mock.patch.object(ocm, "MAX_POI_FILE_BYTES", 10), self.assertRaises(ocm.OcmExportError):
            self.read(data)
        with mock.patch.object(ocm, "MAX_ARCHIVE_MEMBERS", 2), self.assertRaises(ocm.OcmExportError):
            self.read(data)
        with mock.patch.object(ocm, "MAX_ARCHIVE_TOTAL_BYTES", 10), self.assertRaises(ocm.OcmExportError):
            self.read(data)
        with self.assertRaises(ocm.OcmExportError):
            self.read(data, max_seconds=-1)

    def test_extra_archive_content_is_never_written_to_disk(self) -> None:
        before = {p for p in self.tmp.rglob("*")}
        self.read(self.build({1: compact_poi(1)}, raw_files={"payload.sh": b"touch pwned"}))
        created = {p.name for p in set(self.tmp.rglob("*")) - before}
        self.assertNotIn("payload.sh", created)
        self.assertFalse((self.tmp / "pwned").exists())

    def test_commit_manifest_must_be_sha_and_utc_date(self) -> None:
        self.assertEqual(ocm.parse_commit_manifest(MANIFEST), (SHA, COMMIT_DATE))
        for body in (b"{}", b"not json", json.dumps({"sha": "main", "commit": {"committer": {"date": COMMIT_DATE}}}).encode(),
                     json.dumps({"sha": SHA, "commit": {"committer": {"date": "yesterday"}}}).encode()):
            with self.assertRaises(ocm.OcmExportError):
                ocm.parse_commit_manifest(body)
        with self.assertRaises(ocm.OcmExportError):
            ocm.archive_url("main")


def _add(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


class AdapterTests(TempCacheCase):
    def test_source_failure_after_building_a_row_commits_nothing(self) -> None:
        original_emit = ing._openchargemap_emit
        def fail_second(records, poi, **kwargs):
            if poi["ID"] == 2:
                self.assertEqual(len(records), 1)
                raise ocm.OcmExportError("injected source failure after one row")
            return original_emit(records, poi, **kwargs)
        net = Network(manifest=MANIFEST, archive=self.archive_bytes({1: compact_poi(1), 2: compact_poi(2)}), legacy=None)
        output = [{"id": "another-source"}]
        with mock.patch.object(ing, "policy_urlopen", net), mock.patch.object(ing, "_openchargemap_emit", fail_second):
            with self.assertRaisesRegex(ocm.OcmExportError, "after one row"):
                ing.source_openchargemap(output)
        self.assertEqual(output, [{"id": "another-source"}])
        self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS, {})

    def test_quarantined_current_file_cannot_revive_legacy_claims(self) -> None:
        pois = {i: compact_poi(i) for i in range(1, 300)}
        pois[7] = compact_poi(999)  # Accepted malformed share; filename 7 remains present.
        records, _ = self.run_adapter(pois, legacy_rows=[
            legacy_poi(7, title="Historical free claim", lat=40, lon=-3)])
        self.assertNotIn(7, [row["raw"]["ID"] for row in records])
        self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["counts"]["export_malformed_files"], 1)

    def test_malformed_nested_row_after_valid_row_is_atomic(self) -> None:
        for field, value in (("AddressInfo", "bad"), ("Connections", {}),
                             ("Connections", [None]), ("UsageCost", ["Free"])):
            with self.subTest(field=field, value=value):
                bad = compact_poi(2)
                bad[field] = value
                net = Network(manifest=MANIFEST, archive=self.archive_bytes({1: compact_poi(1), 2: bad}), legacy=None)
                records = [{"id": "existing-other-source"}]
                for path in self.cache.iterdir():
                    path.unlink()
                with mock.patch.object(ing, "policy_urlopen", net), self.assertRaises(ocm.OcmExportError):
                    ing.source_openchargemap(records)
                self.assertEqual(records, [{"id": "existing-other-source"}])
                self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS, {})

    def test_unresolved_publication_status_and_usage_are_quarantined(self) -> None:
        records, _ = self.run_adapter({1: compact_poi(1, submission=999),
                                      2: compact_poi(2, status=999),
                                      3: compact_poi(3, usage=999), 4: compact_poi(4)})
        self.assertEqual([row["raw"]["ID"] for row in records], [4])
        self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["counts"]["current_quarantined"], 3)

    def test_blocked_provider_origins_are_excluded_but_station_brands_are_not(self) -> None:
        for title, url in (("Electromaps", ""), ("Chargemap", ""), ("Consum", ""),
                           ("Unrelated display title", "https://electromaps.com/data")):
            with self.subTest(provider=title, url=url):
                ref = deepcopy(REFERENCE)
                ref["DataProviders"].append({"ID": 99, "Title": title, "WebsiteURL": url,
                                             "License": "CC BY 4.0", "IsOpenDataLicensed": True})
                records, _ = self.run_adapter({1: compact_poi(1, provider=99),
                                              2: compact_poi(2, title="Consum", operator=4001)}, reference=ref)
                self.assertEqual([row["raw"]["ID"] for row in records], [2])
                self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["counts"]["current_origin_excluded"], 1)
        for provenance in ({"raw": {"DataProviderTitle": "Electromaps"}},
                           {"sources": [{"data_provider": "Chargemap"}]}):
            allowed, denied = ing.partition_records([provenance], ing.SOURCE_POLICY_EXCLUSIONS)
            self.assertEqual((len(allowed), len(denied)), (0, 1))

    def test_historical_third_party_grant_is_not_replaced_by_current_reference(self) -> None:
        old = legacy_poi(2, title="Historical third party", lat=40, lon=-3, provider=38)
        old["DataProvider"] = {"ID": 38, "Title": "PowerGo", "IsOpenDataLicensed": True,
                               "License": "CC BY-NC-SA 4.0"}
        records, _ = self.run_adapter({1: compact_poi(1)}, legacy_rows=[old])
        self.assertEqual([row["raw"]["ID"] for row in records], [1])
        old["DataProvider"]["License"] = "CC0"
        records, _ = self.run_adapter({1: compact_poi(1)}, legacy_rows=[old])
        source = next(row for row in records if row["raw"]["ID"] == 2)["sources"][0]
        self.assertEqual(source["license"], "CC0")
        self.assertEqual(source["license_url"], ocm.LICENSE_URLS["CC0"])
        self.assertNotIn("substitution", source["license_basis"])

    def test_contributor_historical_substitution_has_exact_basis_and_license_link(self) -> None:
        old = legacy_poi(2, title="Historical contributor", lat=40, lon=-3)
        old["DataProvider"] = {"ID": 1, "Title": "Open Charge Map Contributors", "IsOpenDataLicensed": True,
                               "License": "CC BY-SA 4.0"}
        records, _ = self.run_adapter({1: compact_poi(1)}, legacy_rows=[old])
        source = next(row for row in records if row["raw"]["ID"] == 2)["sources"][0]
        self.assertEqual(source["license"], "CC-BY-4.0")
        self.assertEqual(source["license_basis"], ocm.CONTRIBUTOR_LICENSE_CHANGE_URL)
        self.assertEqual(source["license_url"], ocm.LICENSE_URLS["CC-BY-4.0"])
        self.assertIn("2022-04-01", source["license_text"])

    def test_two_refreshes_retain_post_2021_station_using_canonical_context(self) -> None:
        for status, submission in ((100, 200), (150, 200), (200, 200), (50, 1)):
            with self.subTest(status=status, submission=submission):
                first, _ = self.run_adapter({800001: compact_poi(800001, cost="Free")})
                first = {"records": ing.dedupe(first)}
                for path in self.cache.iterdir():
                    path.unlink()
                net = Network(manifest=MANIFEST, archive=self.archive_bytes({800001: compact_poi(
                    800001, title="Changed", status=status, submission=submission, usage=4, cost="0.59 €/kWh")}), legacy=None)
                with mock.patch.object(ing, "policy_urlopen", net), mock.patch.object(ing, "ACTIVE_SOURCE_ADAPTERS", (ing.source_openchargemap,)):
                    second = ing.build_payload(first)
                second, report = preserve_record_ids(first, second)
                self.assertEqual(report["unresolved_missing_ids"], [])
                self.assertEqual(second["records"][0]["id"], first["records"][0]["id"])
                row = second["records"][0]
                self.assertIn("source_reported_unavailable", row)
                self.assertEqual(row["price_min"], 0.59)
                self.assertFalse(row["is_free"])
                self.assertEqual(row["raw"]["UsageType"], "Public - Membership Required")

    def test_primary_reference_survives_while_corroborating_poi_stays_separate(self) -> None:
        first, _ = self.run_adapter({1: compact_poi(1), 2: compact_poi(2)})
        baseline = {"records": ing.dedupe(first)}
        self.assertEqual(len(baseline["records"]), 1)
        current, _ = self.run_adapter({1: compact_poi(1, lat=39.5302), 2: compact_poi(2, lat=39.5304)},
                                      previous_records=baseline["records"])
        result, report = preserve_record_ids(baseline, {"records": ing.dedupe(current)})
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(len({row["id"] for row in result["records"]}), 2)
        self.assertEqual(report["preserved_ids"], 1)
        self.assertEqual(report["unresolved_missing_ids"], [])
        primary = next(row for row in result['records'] if row['id'] == baseline['records'][0]['id'])
        self.assertEqual(primary['raw']['ID'], baseline['records'][0]['raw']['ID'])
        self.assertEqual(primary['lat'], 39.5302)
        self.assertEqual(report['merged_ids'], 0)
        self.assertEqual(len(report['changes'][0]['contributions']), 2)
        self.assertEqual(len({sid for item in report['changes'][0]['contributions'] for sid in item['current_ids']}), 2)

    def test_canonical_identity_requires_distance_at_each_refresh(self) -> None:
        first, _ = self.run_adapter({1: compact_poi(1)})
        first[0]["id"] = "canonical-prior-id"
        for offset, accepted in ((0.001, True), (0.003, False), (0.12, False)):
            with self.subTest(offset=offset):
                current, _ = self.run_adapter({1: compact_poi(1, lat=39.53 + offset)}, previous_records=first)
                result, report = preserve_record_ids({"records": first}, {"records": current})
                self.assertEqual(report["preserved_ids"], int(accepted))
                self.assertEqual(result["records"][0]["id"] == first[0]["id"], accepted)
                self.assertEqual(distance_m(first[0], current[0]) <= 250, accepted)

    def test_current_rows_are_admitted_with_license_dates_and_attribution(self) -> None:
        records, _ = self.run_adapter({203975: compact_poi(203975, title="Ayunt. Foios", operator=3279)}, legacy_rows=[
            legacy_poi(1, title="x", lat=40.0, lon=-3.0)])
        current = [r for r in records if r["raw"]["ID"] == 203975]
        self.assertEqual(len(current), 1)
        rec = current[0]
        src = rec["sources"][0]
        self.assertEqual(rec["source_url"], "https://map.openchargemap.io/?mode=details&id=203975")
        self.assertEqual(src["license"], "CC-BY-4.0")
        self.assertEqual(src["data_provider"], "Open Charge Map Contributors")
        self.assertIn(src["data_provider"], src["attribution"])
        self.assertIn("Open Charge Map", src["attribution"])
        self.assertEqual(src["license_url"], ocm.LICENSE_URLS[src["license"]])
        from server import compact_station_for_ui
        public_source = compact_station_for_ui(rec)["sources"][0]
        self.assertEqual(public_source["license_url"], src["license_url"])
        self.assertEqual(public_source["attribution"], src["attribution"])
        self.assertEqual(rec["connector_labels"], ["Type 2"])
        self.assertEqual(rec["max_power_kw"], 22)
        self.assertEqual(rec["raw"]["OperatorTitle"], "Fenie Energía (Spain)")
        self.assertEqual(rec["media"]["photos"], [])
        self.assertNotIn("private note", json.dumps(rec))
        self.assertNotIn("contributor comment", json.dumps(rec))
        self.assertEqual(rec["price_claim_type"], "price_unknown_lead")
        self.assertFalse(rec["is_free"])

    def test_dates_keep_commit_lookup_and_contributor_timestamps_apart(self) -> None:
        records, _ = self.run_adapter({1: compact_poi(1)}, legacy_rows=[legacy_poi(2, title="o", lat=40.1, lon=-3.1)])
        rec = next(r for r in records if r["raw"]["ID"] == 1)
        self.assertEqual(rec["source_fetched_at"], COMMIT_DATE)
        self.assertEqual(rec["last_seen"], "2025-10-30T18:01:00Z")
        src = rec["sources"][0]
        self.assertEqual(src["source_snapshot_at"], COMMIT_DATE)
        self.assertEqual(src["source_last_updated"], "2025-10-30T18:01:00Z")
        self.assertNotEqual(src["source_lookup_at"], COMMIT_DATE)
        self.assertTrue(src["source_lookup_at"].endswith("Z"))
        self.assertFalse(rec["source_integration"]["live_status_integrated"])

    def test_same_poi_id_supersedes_legacy_and_preserves_application_id(self) -> None:
        legacy = legacy_poi(203975, title="Ayuntamiento Foios", lat=39.5360, lon=-0.3580, cost="Free")
        old_records: List[Dict[str, Any]] = []
        ing.add_record(old_records, source="OpenChargeMap public snapshot", source_url="https://map.openchargemap.io/?mode=details&id=203975",
                       name="Ayuntamiento Foios", lat=39.5360, lon=-0.3580)
        records, _ = self.run_adapter({203975: compact_poi(203975, title="Ayunt. Foios", lat=39.53676, lon=-0.35794, cost="")},
                                      legacy_rows=[legacy])
        reconciled, report = preserve_record_ids({"records": old_records}, {"records": ing.dedupe(records)})
        self.assertEqual(report["preserved_ids"], 1)
        ids = [r for r in reconciled["records"] if r["raw"]["ID"] == 203975]
        self.assertEqual(len(ids), 1, "legacy and current rows for one POI id must not both survive")
        self.assertFalse(ids[0]["raw"]["historical_snapshot"])
        self.assertIsNone(ids[0]["price_min"], "a newer feed must not carry the 2021 'Free' claim forward")
        self.assertEqual(ids[0]["id"], f"intel_openchargemap-public-snapshot_ayuntamiento-foios_39.536_-0.358")
        self.assertEqual(ids[0]["identity_reconciliation"]["source_identifiers"], [["ocm-station", "203975"]])
        self.assertIn(("ocm-station", "203975"), ing.identity_keys(ids[0]))

    def test_legacy_only_rows_are_retained_as_historical_not_closed(self) -> None:
        records, _ = self.run_adapter({1: compact_poi(1)}, legacy_rows=[legacy_poi(111, title="Solo 2021", lat=40.2, lon=-3.2, cost="0,30 €/kWh")])
        old = next(r for r in records if r["raw"]["ID"] == 111)
        self.assertTrue(old["raw"]["historical_snapshot"])
        self.assertEqual(old["source_fetched_at"], ocm.OCM_LEGACY_DATE)
        self.assertEqual(old["sources"][0]["source_snapshot_at"], "2021-08-13T12:15:47Z")
        self.assertEqual(old["raw"]["snapshot_commit"], ocm.OCM_LEGACY_SHA)
        self.assertIn("Historical OCM row", old["parking"]["note"] if "note" in old["parking"] else old["parking_claim"]["summary"])
        self.assertNotIn("source_reported_unavailable", old)
        self.assertEqual(old["raw"]["DataProviderID"], 1)
        self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["counts"]["legacy_retained_historical"], 1)

    def test_current_gate_failure_never_restores_historical_claims(self) -> None:
        # The former assertion required 2021 facts to replace conflicting current facts.
        # Retain the gate coverage, but require an explicit loss instead of false continuity.
        for status, submission in ((50, 200), (100, 200), (150, 200), (200, 200), (50, 1)):
            with self.subTest(status=status, submission=submission):
                for path in self.cache.iterdir():
                    path.unlink()
                weak = compact_poi(70, lat=39.7, lon=-0.3, conn_type=0,
                                   status=status, submission=submission, usage=4, cost="0.59 €/kWh")
                prior = []
                ing.add_record(prior, source="OpenChargeMap public snapshot",
                               source_url="https://map.openchargemap.io/?mode=details&id=70",
                               name="Canonical prior", lat=39.7, lon=-0.3)
                records, _ = self.run_adapter({70: weak},
                    legacy_rows=[legacy_poi(70, title="Was free/public", lat=39.7, lon=-0.3)],
                    previous_records=prior)
                self.assertEqual(records, [])
                self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["counts"]["current_gate_dropped"], 1)
                _, report = preserve_record_ids({"records": prior}, {"records": records})
                self.assertEqual(report["unresolved_missing_ids"], [prior[0]["id"]])
        records, _ = self.run_adapter({71: compact_poi(71, conn_type=0)})
        self.assertNotIn(71, [r["raw"]["ID"] for r in records])

    def test_legacy_only_delisted_row_is_retained_for_identity_but_flagged_unavailable(self) -> None:
        delisted = legacy_poi(113, title="Delisted 2021", lat=40.4, lon=-3.4)
        delisted["SubmissionStatusTypeID"] = 1
        delisted["SubmissionStatus"] = {"Title": "Submission Under Review"}
        records, _ = self.run_adapter({1: compact_poi(1)}, legacy_rows=[delisted])
        rec = next(r for r in records if r["raw"]["ID"] == 113)
        self.assertTrue(rec["raw"]["historical_snapshot"])
        self.assertEqual(rec["source_reported_unavailable"]["field"], "SubmissionStatus")

    def test_legacy_only_row_with_non_open_provider_is_not_retained(self) -> None:
        records, _ = self.run_adapter({1: compact_poi(1)}, legacy_rows=[legacy_poi(112, title="Place2Plug", lat=40.3, lon=-3.3, provider=27)])
        self.assertEqual([r["raw"]["ID"] for r in records], [1])
        self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["counts"]["legacy_license_excluded"], 1)

    def test_license_fails_closed_per_provider(self) -> None:
        pois = {
            10: compact_poi(10, provider=1, lat=39.1),
            11: compact_poi(11, provider=38, lat=39.2),   # CC0
            12: compact_poi(12, provider=33, lat=39.3),   # CC0
            13: compact_poi(13, provider=25, lat=39.4),   # "General public data": ambiguous
            14: compact_poi(14, provider=27, lat=39.5),   # null license
            15: compact_poi(15, provider=40, lat=39.6),   # ShareAlike
            16: compact_poi(16, provider=999, lat=39.7),  # unknown provider
        }
        records, _ = self.run_adapter(pois, legacy_rows=[legacy_poi(1, title="x", lat=40.0, lon=-3.0)])
        self.assertEqual(sorted(r["raw"]["ID"] for r in records if not r["raw"]["historical_snapshot"]), [10, 11, 12])
        counts = ing.OCM_SNAPSHOT_LOOKUPS["counts"]
        self.assertEqual(counts["current_license_excluded"], 4)
        self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["license_excluded_by_provider"], {"25": 1, "27": 1, "40": 1, "999": 1})
        by_id = {r["raw"]["ID"]: r["sources"][0]["license"] for r in records}
        self.assertEqual((by_id[10], by_id[11], by_id[12]), ("CC-BY-4.0", "CC0", "CC0"))

    def test_wrong_country_rows_inside_the_country_dir_are_dropped(self) -> None:
        records, _ = self.run_adapter({20: compact_poi(20, country=1), 21: compact_poi(21, lat=39.4)},
                                      legacy_rows=[legacy_poi(1, title="x", lat=40.0, lon=-3.0)])
        self.assertEqual(sorted(r["raw"]["ID"] for r in records if not r["raw"]["historical_snapshot"]), [21])
        self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["counts"]["current_wrong_country"], 1)

    def test_new_planned_removed_and_unpublished_rows_are_omitted(self) -> None:
        pois = {
            30: compact_poi(30, status=150, lat=39.1),
            31: compact_poi(31, status=100, lat=39.2),
            32: compact_poi(32, status=200, lat=39.3),
            33: compact_poi(33, submission=1, lat=39.4),
            34: compact_poi(34, status=50, lat=39.5),
        }
        records, _ = self.run_adapter(pois, legacy_rows=[legacy_poi(1, title="x", lat=40.0, lon=-3.0)])
        self.assertEqual(sorted(r["raw"]["ID"] for r in records if not r["raw"]["historical_snapshot"]), [34])
        self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["counts"]["current_non_live_new_omitted"], 4)

    def test_known_id_now_planned_or_not_operational_keeps_identity_but_is_unavailable(self) -> None:
        legacy = [legacy_poi(40, title="Was working", lat=39.7, lon=-0.3), legacy_poi(41, title="Was working 2", lat=39.8, lon=-0.3)]
        prior = []
        for poi_id, lat in ((40, 39.7), (41, 39.8)):
            ing.add_record(prior, source="OpenChargeMap public snapshot",
                           source_url=f"https://map.openchargemap.io/?mode=details&id={poi_id}",
                           name=f"Canonical {poi_id}", lat=lat, lon=-0.3)
        records, _ = self.run_adapter({40: compact_poi(40, status=100, lat=39.7, lon=-0.3), 41: compact_poi(41, status=150, lat=39.8, lon=-0.3)},
                                      legacy_rows=legacy, previous_records=prior)
        for poi_id in (40, 41):
            rec = next(r for r in records if r["raw"]["ID"] == poi_id)
            self.assertIn("source_reported_unavailable", rec)
            self.assertIn("Do not route", rec["source_reported_unavailable"]["summary"])
            self.assertNotEqual(rec["status"], "AVAILABLE")
        self.assertEqual(ing.OCM_SNAPSHOT_LOOKUPS["counts"]["current_non_live_existing_flagged"], 2)

    def test_temporarily_unavailable_is_listed_but_flagged(self) -> None:
        records, _ = self.run_adapter({50: compact_poi(50, status=30)}, legacy_rows=[legacy_poi(1, title="x", lat=40.0, lon=-3.0)])
        rec = next(r for r in records if r["raw"]["ID"] == 50)
        self.assertIn("source_reported_unavailable", rec)

    def test_independent_licensed_consum_operator_rows_remain_allowed(self) -> None:
        pois = {60: compact_poi(60, title="Consum Foios LLuna", operator=4001, usage=4, cost="0,39€/kWh DC")}
        records, _ = self.run_adapter(pois, legacy_rows=[legacy_poi(1, title="x", lat=40.0, lon=-3.0)])
        rec = next(r for r in records if r["raw"]["ID"] == 60)
        self.assertEqual(rec["raw"]["OperatorTitle"], "Consum")
        kept, excluded = ing.partition_records([rec], ing.SOURCE_POLICY_EXCLUSIONS)
        self.assertEqual((len(kept), len(excluded)), (1, 0))

    def test_quarantined_id_is_not_exported(self) -> None:
        records, _ = self.run_adapter({169683: compact_poi(169683), 61: compact_poi(61, lat=39.9)},
                                      legacy_rows=[legacy_poi(1, title="x", lat=40.0, lon=-3.0)])
        self.assertNotIn(169683, [r["raw"]["ID"] for r in records])

    def test_transport_is_three_bounded_gets_and_then_cache_only(self) -> None:
        records, net = self.run_adapter({1: compact_poi(1)}, legacy_rows=[legacy_poi(2, title="o", lat=40.1, lon=-3.1)])
        self.assertEqual([r.get_method() for r in net.requests], ["GET", "GET", "GET"])
        self.assertEqual([r.full_url for r in net.requests],
                         [ocm.OCM_EXPORT_COMMIT_API_URL, ocm.archive_url(SHA), ocm.OCM_LEGACY_URL])
        self.assertTrue(all(r.data is None for r in net.requests))
        self.assertIn(f"/{SHA}", net.requests[1].full_url)
        self.assertIn(f"/{ocm.OCM_LEGACY_SHA}/", net.requests[2].full_url)
        again: List[Dict[str, Any]] = []
        with mock.patch.object(ing, "policy_urlopen", side_effect=AssertionError("cache hit must not touch the network")):
            ing.source_openchargemap(again)
        self.assertEqual(len(again), len(records))
        urls = [a["url"] for a in ing.fetch_evidence_payload()["artifacts"]]
        self.assertIn(ocm.archive_url(SHA), urls)

    def test_transport_policy_rejects_any_post(self) -> None:
        import source_transport as st
        req = urllib.request.Request(ocm.archive_url(SHA), data=b"x", method="POST")
        with self.assertRaises(st.SourcePolicyBlocked):
            st.policy_urlopen(req, timeout=5)

    def test_download_byte_bound_is_enforced(self) -> None:
        net = Network(manifest=MANIFEST, archive=b"x" * 5000, legacy=None)
        with mock.patch.object(ing, "policy_urlopen", net), mock.patch.object(ocm, "MAX_ARCHIVE_DOWNLOAD_BYTES", 1000):
            with self.assertRaises(RuntimeError):
                ing.source_openchargemap([])
        self.assertFalse(list(self.cache.glob("ocm_export_*.tar.gz")), "an over-bound download must not be cached")

    def test_malformed_archive_raises_and_emits_no_partial_rows(self) -> None:
        net = Network(manifest=MANIFEST, archive=b"not an archive", legacy=None)
        records: List[Dict[str, Any]] = []
        with mock.patch.object(ing, "policy_urlopen", net), self.assertRaises(ocm.OcmExportError):
            ing.source_openchargemap(records)
        self.assertEqual(records, [])
        self.assertFalse((self.cache / f"ocm_export_{SHA}.tar.gz").exists())
        self.assertEqual(list(self.cache.glob("validate-*")), [])

    def test_bad_manifest_raises(self) -> None:
        net = Network(manifest=b"{}", archive=b"", legacy=None)
        with mock.patch.object(ing, "policy_urlopen", net), self.assertRaises(ocm.OcmExportError):
            ing.source_openchargemap([])
        self.assertFalse((self.cache / "ocm_export_commit.json").exists())

    def test_legacy_unreachable_degrades_to_current_only_without_dropping_current(self) -> None:
        records, _ = self.run_adapter({1: compact_poi(1)}, legacy_rows=None)
        self.assertEqual([r["raw"]["ID"] for r in records], [1])
        self.assertIn("legacy unreachable", ing.OCM_SNAPSHOT_LOOKUPS["legacy_error"])

    def test_build_payload_surfaces_snapshot_and_license_notes(self) -> None:
        net = Network(manifest=MANIFEST, archive=self.archive_bytes({1: compact_poi(1)}), legacy=legacy_gz([legacy_poi(2, title="o", lat=40.1, lon=-3.1)]))
        with mock.patch.object(ing, "ACTIVE_SOURCE_ADAPTERS", (ing.source_openchargemap,)), mock.patch.object(ing, "policy_urlopen", net):
            payload = ing.build_payload()
        self.assertEqual(payload["errors"], [])
        snap = payload["source_snapshots"]["openchargemap"]
        self.assertEqual(snap["commit"], SHA)
        self.assertEqual(snap["commit_date"], COMMIT_DATE)
        self.assertEqual(payload["source_adapter_counts"], {"source_openchargemap": 2})
        self.assertTrue(any("commit-pinned ocm-export" in note for note in payload["license_notes"]))
        self.assertEqual(payload["total_records"], 2)


if __name__ == "__main__":
    unittest.main()
