#!/usr/bin/env python3
"""
remote_linkedin_candidate_profiler.py

Run-scoped deep Candidate <-> Remote Source profiler for LinkedIn-derived sources.

Inputs:
  <run>/harvest/linkedin/scoring/source_relationship_scored.jsonl
  <run>/harvest/linkedin/scoring/relationship_validation_results.jsonl

Outputs:
  <run>/harvest/linkedin/profiling/candidate_source_profile_results.jsonl
  <run>/harvest/linkedin/profiling/candidate_source_profile_unresolved.jsonl

No final Golden scoring, Excel publication, or watermark advancement.
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
    linkedin_dir = run_dir / "harvest" / "linkedin"

    scored_path = linkedin_dir / "scoring" / "source_relationship_scored.jsonl"
    validation_path = linkedin_dir / "scoring" / "relationship_validation_results.jsonl"
    profiling_dir = linkedin_dir / "profiling"
    results_path = profiling_dir / "candidate_source_profile_results.jsonl"
    unresolved_path = profiling_dir / "candidate_source_profile_unresolved.jsonl"

    if not scored_path.exists():
        raise RuntimeError(f"LinkedIn relationship scoring output not found: {scored_path}")
    if not validation_path.exists():
        raise RuntimeError(f"LinkedIn validation evidence not found: {validation_path}")

    profiling_dir.mkdir(parents=True, exist_ok=True)

    os.environ["REMOTE_SOURCE_RELATIONSHIP_SCORED"] = str(scored_path)
    os.environ["REMOTE_SOURCE_VALIDATION_RESULTS"] = str(validation_path)
    os.environ["REMOTE_CANDIDATE_SOURCE_PROFILE_RESULTS"] = str(results_path)
    os.environ["REMOTE_CANDIDATE_SOURCE_PROFILE_UNRESOLVED"] = str(unresolved_path)
    os.environ["REMOTE_PROFILE_LIMIT"] = "0"

    from tools.scoring import remote_candidate_source_profiler as profiler

    print("REMOTE LINKEDIN CANDIDATE PROFILING")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Relationship input:        {scored_path}")
    print(f"Validation evidence:       {validation_path}")
    print(f"Profile results:           {results_path}")
    print(f"Profile unresolved:        {unresolved_path}")
    print()
    print("Reusing existing deep candidate-specific profiler.")
    print()

    rc = profiler.main()

    print()
    if rc == 0:
        print("REMOTE LINKEDIN CANDIDATE PROFILING COMPLETE")
        print("Final Golden scoring has NOT yet run.")
        print("Excel was NOT touched.")
        print("Watermark was NOT advanced.")
    else:
        print("REMOTE LINKEDIN CANDIDATE PROFILING FAILED")
        print("Watermark was NOT advanced.")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
