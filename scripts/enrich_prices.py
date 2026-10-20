#!/usr/bin/env python3
"""Add bounded price evidence to an existing canonical candidate; never promote it."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path

try:
    from .price_evidence import enrich_price_evidence, price_evidence_counts
    from .source_policy import recordset_sha256
except ImportError:
    from price_evidence import enrich_price_evidence, price_evidence_counts
    from source_policy import recordset_sha256


def enrich_payload(baseline, cache_dir, **kwargs):
    candidate = deepcopy(baseline)
    rows = candidate.get('records') or []
    report = enrich_price_evidence(rows, cache_dir, **kwargs)
    candidate['price_evidence_counts'] = price_evidence_counts(rows)
    candidate['price_evidence_collection'] = report
    candidate['recordset_sha256'] = recordset_sha256(rows)
    candidate['recordset_records'] = len(rows)
    # Canonical IDs, aliases, coordinates and every non-evidence field stay intact.
    def primary(row):
        return {k: v for k, v in row.items() if k != 'price_evidence'}
    if [primary(row) for row in rows] != [primary(row) for row in baseline.get('records') or []]:
        raise RuntimeError('price enrichment changed canonical station fields')
    return candidate, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'data' / 'cache')
    args = parser.parse_args()
    if args.out.resolve() == args.baseline.resolve() or args.report.resolve() in (args.baseline.resolve(), args.out.resolve()):
        parser.error('baseline, candidate and report must be separate files')
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    candidate, report = enrich_payload(baseline, args.cache_dir)
    for path, value in ((args.out, candidate), (args.report, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
