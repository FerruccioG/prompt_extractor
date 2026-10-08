#!/usr/bin/env python3
"""
remote_incremental_final_scorer.py

Run-scoped wrapper around the existing final evidence-weighted
Candidate <-> Remote Source scorer.

Inputs
------
<run>/scoring/source_relationship_scored.jsonl
<run>/profiling/candidate_source_profile_results.jsonl
<run>/profiling/candidate_source_profile_unresolved.jsonl

Outputs
-------
<run>/final/source_records.jsonl
<run>/final/source_records_review.jsonl
<run>/final/source_records_excluded.jsonl
<run>/final/source_final_scoring_summary.json

This stage computes the full Candidate <-> Source dimensions and relationship
tier. It does NOT publish to Excel and does NOT advance the refresh watermark.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)

    relationship_path = run_dir / "scoring" / "source_relationship_scored.jsonl"
    profile_path = run_dir / "profiling" / "candidate_source_profile_results.jsonl"
    profile_unresolved_path = run_dir / "profiling" / "candidate_source_profile_unresolved.jsonl"

    final_dir = run_dir / "final"
    output_path = final_dir / "source_records.jsonl"
    review_path = final_dir / "source_records_review.jsonl"
    excluded_path = final_dir / "source_records_excluded.jsonl"
    summary_path = final_dir / "source_final_scoring_summary.json"

    if not relationship_path.exists():
        raise RuntimeError(f"Relationship scoring input not found: {relationship_path}")
    if not profile_path.exists():
        raise RuntimeError(f"Profile results not found: {profile_path}")

    final_dir.mkdir(parents=True, exist_ok=True)

    os.environ["REMOTE_SOURCE_RELATIONSHIP_SCORED"] = str(relationship_path)
    os.environ["REMOTE_CANDIDATE_SOURCE_PROFILE_RESULTS"] = str(profile_path)
    os.environ["REMOTE_CANDIDATE_SOURCE_PROFILE_UNRESOLVED"] = str(profile_unresolved_path)
    os.environ["REMOTE_SOURCE_RECORDS"] = str(output_path)
    os.environ["REMOTE_SOURCE_RECORDS_REVIEW"] = str(review_path)
    os.environ["REMOTE_SOURCE_RECORDS_EXCLUDED"] = str(excluded_path)
    os.environ["REMOTE_SOURCE_FINAL_SCORING_SUMMARY"] = str(summary_path)

    # Import after environment configuration because the existing final scorer
    # binds its input/output paths at module import time.
    from tools.scoring import remote_candidate_source_final_scorer as scorer

    print("REMOTE INCREMENTAL FINAL CANDIDATE-SOURCE SCORING")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Relationship evidence:     {relationship_path}")
    print(f"Candidate profiles:        {profile_path}")
    print(f"Final output directory:    {final_dir}")
    print()
    print("Reusing existing final Candidate <-> Source scorer.")
    print()

    rc = scorer.main()

    print()
    if rc == 0:
        print("REMOTE INCREMENTAL FINAL SCORING COMPLETE")
        print(f"Included A/B/C:            {output_path}")
        print(f"Review:                    {review_path}")
        print(f"Excluded:                  {excluded_path}")
        print("Promotion gate has NOT yet been applied.")
        print("Excel was NOT touched.")
        print("Watermark was NOT advanced.")
    else:
        print("REMOTE INCREMENTAL FINAL SCORING FAILED")
        print("Watermark was NOT advanced.")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
