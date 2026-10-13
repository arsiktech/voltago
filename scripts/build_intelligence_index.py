#!/usr/bin/env python3
"""Build and atomically publish a bounded-memory runtime index from approved JSON."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.intelligence_index import APPLICATION_ID, SCHEMA_VERSION, IntelligenceIndex, file_sha256, indexed_fields
from scripts.json_recordset import load_json_recordset
from scripts.global_source_catalog import ADAPTER_IDS
from scripts.source_policy import load_source_policy_exclusions, partition_records


def _health_summaries(path: Path, metadata: dict, summaries: dict, seeds_path: Path) -> None:
    """Compute classification once, binding it to the exact runtime source files."""
    import server
    code_hash = server.intelligence_health_fingerprint()
    original_data = server.DATA
    previous_inventory = dict(server._SOURCE_INVENTORY)
    try:
        server.DATA = seeds_path.parent
        # A builder may finalize several bundles in one process. Its temporary
        # metadata object can reuse an old Python id, unlike the runtime cache.
        server._SOURCE_INVENTORY.clear()
        index = IntelligenceIndex(path, policy_sha256=metadata["index_manifest"]["policy_sha256"])
        inventory_input = index.metadata()
        inventory_input["records"] = index.records
        summaries["source_health_inventory"] = server.source_health_inventory(inventory_input, allow_index_scan=True)
        counts = {"free_records": 0, "cheap_records": 0, "official_records": 0, "access_limited_records": 0}
        for row in index.records:
            counts["free_records"] += bool(row.get("is_free"))
            counts["cheap_records"] += bool(row.get("is_cheap"))
            counts["official_records"] += str(row.get("reliability", "")).startswith("official")
            counts["access_limited_records"] += server.access_profile(row).get("level") == "restricted"
        summaries["intel_counts"] = counts
    finally:
        server.DATA = original_data
        server._SOURCE_INVENTORY.clear()
        server._SOURCE_INVENTORY.update(previous_inventory)
    if server.intelligence_health_fingerprint() != code_hash:
        raise ValueError("Runtime classification changed during index construction")
    metadata["index_manifest"]["health_code_sha256"] = code_hash


def finalize_index(source: str | Path, output: str | Path, *, policy_path: str | Path | None = None,
                   seeds_path: str | Path | None = None) -> dict:
    """Refresh derived summaries into a new immutable file without rereading JSON."""
    source, output = Path(source).resolve(), Path(output).absolute()
    if os.path.lexists(output):
        raise FileExistsError("An immutable index must use a new output path")
    policy_path = Path(policy_path or ROOT / "data/source_policy_exclusions.json").resolve()
    seeds_path = Path(seeds_path or ROOT / "data/seed_leads.json").resolve()
    index = IntelligenceIndex(source, policy_sha256=file_sha256(policy_path))
    metadata, summaries = index.metadata(), index.summaries
    metadata["index_manifest"]["seeds_sha256"] = file_sha256(seeds_path) if seeds_path.exists() else None
    _health_summaries(source, metadata, summaries, seeds_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        shutil.copyfile(source, temporary)
        with sqlite3.connect(temporary) as connection:
            connection.execute("UPDATE metadata SET value=? WHERE key='catalogue'", (json.dumps(metadata, ensure_ascii=False),))
            connection.execute("UPDATE metadata SET value=? WHERE key='summaries'", (json.dumps(summaries, ensure_ascii=False),))
        if file_sha256(policy_path) != metadata["index_manifest"]["policy_sha256"] or (file_sha256(seeds_path) if seeds_path.exists() else None) != metadata["index_manifest"]["seeds_sha256"]:
            raise ValueError("Source policy or seed catalogue changed during summary construction")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.link(temporary, output)
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {"records": index.count, "recordset_sha256": metadata["recordset_sha256"],
                "source_sha256": metadata["index_manifest"]["source_sha256"],
                "index_sha256": file_sha256(output), "index_bytes": output.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)


def build_index(source: str | Path, output: str | Path, *, policy_path: str | Path | None = None,
                seeds_path: str | Path | None = None, health_summary: bool = False) -> dict:
    source, output = Path(source).resolve(), Path(output).absolute()
    if source == output:
        raise ValueError("Source catalogue and derived index must be different paths")
    if os.path.lexists(output):
        raise FileExistsError("An immutable index must use a new output path; switch the runtime symlink after verification")
    policy_path = Path(policy_path or ROOT / "data/source_policy_exclusions.json").resolve()
    seeds_path = Path(seeds_path or ROOT / "data/seed_leads.json").resolve()
    policy = load_source_policy_exclusions(policy_path)
    source_identity = (source.stat().st_size, source.stat().st_mtime_ns)
    source_sha256 = file_sha256(source)
    policy_sha256 = file_sha256(policy_path)
    seeds_sha256 = file_sha256(seeds_path) if seeds_path.exists() else None
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    count = excluded = 0
    summaries = {"global_source_counts": {"gaia_total": 0, "gaia_free": 0, "adapters": {}}, "fenie_origin_records": 0}
    gaia_pattern = re.compile(r"\bgaia\b", re.I)
    connection = None
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(f"""
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=FILE;
            PRAGMA cache_size=-8192;
            PRAGMA application_id={APPLICATION_ID};
            PRAGMA user_version={SCHEMA_VERSION};
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE stations(seq INTEGER PRIMARY KEY, identifier TEXT NOT NULL,
                lat REAL, lon REAL, price REAL, is_free INTEGER NOT NULL, is_cheap INTEGER NOT NULL,
                fast INTEGER NOT NULL, score REAL NOT NULL, payload BLOB NOT NULL);
            CREATE TABLE identifiers(alias TEXT PRIMARY KEY, seq INTEGER NOT NULL);
        """)

        def consume(row: dict) -> None:
            nonlocal count, excluded
            kept, removed = partition_records([row], policy)
            excluded += len(removed)
            if not kept:
                return None
            row = kept[0]
            identifier = str(row.get("id") or "")
            canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            connection.execute("INSERT INTO stations VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (count, identifier, *indexed_fields(row), zlib.compress(canonical, 6)))
            aliases = [identifier] + list(row.get("merged_ids") or [])
            connection.executemany("INSERT OR IGNORE INTO identifiers VALUES (?,?)",
                                   [(str(alias), count) for alias in aliases if alias])
            source_counts = summaries["global_source_counts"]
            for adapter in ADAPTER_IDS:
                if identifier.startswith(f"global_{adapter}_"):
                    source_counts["adapters"][adapter] = source_counts["adapters"].get(adapter, 0) + 1
                    break
            if gaia_pattern.search(" ".join(str(row.get(key) or "") for key in ("source", "name", "operator"))):
                source_counts["gaia_total"] += 1
                source_counts["gaia_free"] += bool(row.get("is_free"))
            primary = row.get("primary_source") if isinstance(row.get("primary_source"), dict) else {}
            origin = " ".join(str(value or "") for value in (row.get("source"), row.get("source_url"), primary.get("url"))).casefold()
            summaries["fenie_origin_records"] += int("fenieenergia.es" in origin or "feníe energía public map" in origin)
            count += 1
            if count % 10000 == 0:
                connection.commit()
            return None

        metadata = load_json_recordset(source, record_transform=consume)
        metadata.pop("records", None)
        connection.executescript("""
            CREATE INDEX station_coordinates ON stations(lat,lon);
            CREATE INDEX station_overview ON stations(is_free DESC,is_cheap DESC,score DESC,identifier);
            CREATE INDEX station_identifiers ON stations(identifier,seq);
        """)
        digest = hashlib.sha256(b"[")
        for position, (payload,) in enumerate(connection.execute("SELECT payload FROM stations ORDER BY identifier,seq")):
            if position:
                digest.update(b",")
            digest.update(zlib.decompress(payload))
        digest.update(b"]")
        metadata.update(total_records=count, recordset_records=count, recordset_sha256=digest.hexdigest(),
                        source_policy_excluded_records=excluded)
        metadata["index_manifest"] = {
            "schema_version": SCHEMA_VERSION, "record_encoding": "canonical-json-zlib",
            "records": count, "source_sha256": source_sha256, "policy_sha256": policy_sha256,
            "seeds_sha256": seeds_sha256, "built_at": datetime.now(timezone.utc).isoformat(),
        }
        connection.execute("INSERT INTO metadata VALUES ('catalogue',?)", (json.dumps(metadata, ensure_ascii=False),))
        connection.execute("INSERT INTO metadata VALUES ('summaries',?)", (json.dumps(summaries, ensure_ascii=False),))
        connection.commit()
        if health_summary:
            _health_summaries(temporary, metadata, summaries, seeds_path)
            connection.execute("UPDATE metadata SET value=? WHERE key='catalogue'", (json.dumps(metadata, ensure_ascii=False),))
            connection.execute("UPDATE metadata SET value=? WHERE key='summaries'", (json.dumps(summaries, ensure_ascii=False),))
            connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Index integrity check failed: {integrity}")
        connection.close()
        connection = None
        if source_identity != (source.stat().st_size, source.stat().st_mtime_ns) or file_sha256(source) != source_sha256:
            raise ValueError("Source catalogue changed during index construction")
        if file_sha256(policy_path) != policy_sha256 or (file_sha256(seeds_path) if seeds_path.exists() else None) != seeds_sha256:
            raise ValueError("Source policy or seed catalogue changed during index construction")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        # Atomic no-clobber publication: an existing reader must never see new
        # rows beneath its cached metadata at the same immutable pathname.
        os.link(temporary, output)
        temporary.unlink()
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {"records": count, "excluded_records": excluded, "recordset_sha256": digest.hexdigest(),
                "source_sha256": source_sha256, "index_sha256": file_sha256(output), "index_bytes": output.stat().st_size}
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--seeds", type=Path)
    parser.add_argument("--health-summary", action="store_true", help="Precompute source health with the current server classification")
    parser.add_argument("--finalize-index", action="store_true", help="Source is an existing immutable index; rebuild only its runtime summaries")
    args = parser.parse_args()
    if args.finalize_index:
        result = finalize_index(args.source, args.output, policy_path=args.policy, seeds_path=args.seeds)
    else:
        result = build_index(args.source, args.output, policy_path=args.policy, seeds_path=args.seeds,
                             health_summary=args.health_summary)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
