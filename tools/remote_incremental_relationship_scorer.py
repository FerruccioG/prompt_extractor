#!/usr/bin/env python3
"""
remote_incremental_relationship_scorer.py

Prepare and run the existing generic Candidate <-> Remote Source relationship
scorer for semantically eligible incremental sources.

Input
-----
<run>/validation/eligible_new_sources.jsonl

Run-scoped outputs
------------------
<run>/scoring/relationship_queue.jsonl
<run>/scoring/relationship_validation_results.jsonl
<run>/scoring/source_relationship_scored.jsonl
<run>/scoring/source_relationship_shortlist.jsonl
<run>/scoring/source_relationship_review.jsonl
<run>/scoring/source_relationship_excluded.jsonl
<run>/scoring/source_relationship_scoring_summary.json

Important identity rule
-----------------------
The validation assessor may change identity after a proven locale redirect
(e.g. ziprecruiter.com -> ziprecruiter.ie). This wrapper rewrites both queue
and validation keys to the assessed canonical host before invoking the legacy
scorer, preventing the scorer from treating the validated evidence as missing.

No deep candidate profiling, final Golden scoring, Excel publication, or
watermark advancement occurs here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def latest_run_dir(root: Path) -> Path:
    runs_dir = root / "data" / "remote" / "refresh_runs"
    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"No refresh run directories found under {runs_dir}")
    return candidates[0]


def prepare_rows(eligible_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queue_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []

    for row in eligible_rows:
        host = str(
            row.get("assessed_canonical_host")
            or row.get("canonical_host")
            or ""
        ).strip().lower()
        if not host:
            raise RuntimeError("Eligible source missing assessed canonical host")

        root_url = str(
            row.get("assessed_canonical_root_url")
            or row.get("canonical_root_url")
            or f"https://{host}/"
        ).strip()

        queue_rows.append({
            "canonical_host": host,
            "canonical_root_url": root_url,
            "prevalidation_score": row.get("prevalidation_score"),
            "prevalidation_reason": row.get("prevalidation_reason") or "incremental_live_validated",
            "email_evidence_count": int(row.get("email_evidence_count", 0) or 0),
            "evidence_url_count": int(row.get("evidence_url_count", 0) or 0),
            "discovery_resolution_evidence_count": int(
                row.get("discovery_resolution_evidence_count", 0) or 0
            ),
            "original_candidate_host": row.get("original_candidate_host"),
        })

        validation = dict(row)
        validation["validation_key"] = host
        validation["canonical_host"] = host
        validation["canonical_root_url"] = root_url
        validation_rows.append(validation)

    return queue_rows, validation_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)
    eligible_path = run_dir / "validation" / "eligible_new_sources.jsonl"
    scoring_dir = run_dir / "scoring"

    if not eligible_path.exists():
        raise RuntimeError(f"Eligible source file not found: {eligible_path}")

    eligible_rows = load_jsonl(eligible_path)
    queue_rows, validation_rows = prepare_rows(eligible_rows)

    queue_path = scoring_dir / "relationship_queue.jsonl"
    validation_path = scoring_dir / "relationship_validation_results.jsonl"
    unresolved_path = scoring_dir / "relationship_validation_unresolved.jsonl"

    scored_path = scoring_dir / "source_relationship_scored.jsonl"
    shortlist_path = scoring_dir / "source_relationship_shortlist.jsonl"
    review_path = scoring_dir / "source_relationship_review.jsonl"
    excluded_path = scoring_dir / "source_relationship_excluded.jsonl"
    summary_path = scoring_dir / "source_relationship_scoring_summary.json"

    scoring_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(queue_path, queue_rows)
    write_jsonl(validation_path, validation_rows)
    write_jsonl(unresolved_path, [])

    os.environ["REMOTE_SOURCE_VALIDATION_QUEUE"] = str(queue_path)
    os.environ["REMOTE_SOURCE_VALIDATION_RESULTS"] = str(validation_path)
    os.environ["REMOTE_SOURCE_VALIDATION_UNRESOLVED"] = str(unresolved_path)
    os.environ["REMOTE_SOURCE_RELATIONSHIP_SCORED"] = str(scored_path)
    os.environ["REMOTE_SOURCE_RELATIONSHIP_SHORTLIST"] = str(shortlist_path)
    os.environ["REMOTE_SOURCE_RELATIONSHIP_REVIEW"] = str(review_path)
    os.environ["REMOTE_SOURCE_RELATIONSHIP_EXCLUDED"] = str(excluded_path)
    os.environ["REMOTE_SOURCE_RELATIONSHIP_SUMMARY"] = str(summary_path)

    # Import after setting environment variables: the existing scorer resolves
    # all path constants at module import time.
    from tools.scoring import remote_source_relationship_scorer as scorer

    print("REMOTE INCREMENTAL RELATIONSHIP SCORING")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Eligible sources:          {len(eligible_rows)}")
    for row in queue_rows:
        print(
            f"  {row.get('original_candidate_host') or row['canonical_host']} "
            f"-> {row['canonical_host']}"
        )
    print()
    print("Reusing existing generic relationship scorer.")
    print()

    rc = scorer.main()

    print()
    if rc == 0:
        print("REMOTE INCREMENTAL RELATIONSHIP SCORING COMPLETE")
        print(f"Scored output:             {scored_path}")
        print("Deep candidate profiling has NOT yet run.")
        print("Final Golden scoring has NOT yet run.")
        print("Excel was NOT touched.")
        print("Watermark was NOT advanced.")
    else:
        print("REMOTE INCREMENTAL RELATIONSHIP SCORING FAILED")
        print("Watermark was NOT advanced.")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
