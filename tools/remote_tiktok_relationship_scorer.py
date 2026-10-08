#!/usr/bin/env python3
"""
remote_tiktok_relationship_scorer.py

Prepare and run the existing generic Candidate <-> Remote Source relationship
scorer for semantically eligible TikTok-derived sources.

Inputs:
  <run>/harvest/tiktok/validation/eligible_new_sources.jsonl

Outputs:
  <run>/harvest/tiktok/scoring/relationship_queue.jsonl
  <run>/harvest/tiktok/scoring/relationship_validation_results.jsonl
  <run>/harvest/tiktok/scoring/source_relationship_scored.jsonl
  <run>/harvest/tiktok/scoring/source_relationship_shortlist.jsonl
  <run>/harvest/tiktok/scoring/source_relationship_review.jsonl
  <run>/harvest/tiktok/scoring/source_relationship_excluded.jsonl
  <run>/harvest/tiktok/scoring/source_relationship_scoring_summary.json

No deep candidate profiling, final Golden scoring, Excel publication, or
watermark advancement occurs here.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.remote_incremental_relationship_scorer import (
    latest_run_dir,
    load_jsonl,
    prepare_rows,
    write_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)
    tiktok_dir = run_dir / "harvest" / "tiktok"
    eligible_path = tiktok_dir / "validation" / "eligible_new_sources.jsonl"
    scoring_dir = tiktok_dir / "scoring"

    if not eligible_path.exists():
        raise RuntimeError(f"TikTok eligible source file not found: {eligible_path}")

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

    from tools.scoring import remote_source_relationship_scorer as scorer

    print("REMOTE TIKTOK RELATIONSHIP SCORING")
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
        print("REMOTE TIKTOK RELATIONSHIP SCORING COMPLETE")
        print(f"Scored output:             {scored_path}")
        print("Deep candidate profiling has NOT yet run.")
        print("Final Golden scoring has NOT yet run.")
        print("Excel was NOT touched.")
        print("Watermark was NOT advanced.")
    else:
        print("REMOTE TIKTOK RELATIONSHIP SCORING FAILED")
        print("Watermark was NOT advanced.")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
