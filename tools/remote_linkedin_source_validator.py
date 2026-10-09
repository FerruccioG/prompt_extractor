#!/usr/bin/env python3
"""
remote_linkedin_source_validator.py

Run-scoped live validator for NEW candidate sources extracted/resolved from
LinkedIn evidence.

Input:
  <run>/harvest/linkedin/dedupe/new_source_validation_queue.jsonl

Outputs:
  <run>/harvest/linkedin/validation/source_validation_results.jsonl
  <run>/harvest/linkedin/validation/source_validation_unresolved.jsonl

Reuses the existing Remote source validator without touching historical files.
No scoring, Excel publication, or watermark advancement.
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
    queue_path = linkedin_dir / "dedupe" / "new_source_validation_queue.jsonl"
    validation_dir = linkedin_dir / "validation"
    results_path = validation_dir / "source_validation_results.jsonl"
    unresolved_path = validation_dir / "source_validation_unresolved.jsonl"

    if not queue_path.exists():
        raise RuntimeError(f"LinkedIn validation queue not found: {queue_path}")

    validation_dir.mkdir(parents=True, exist_ok=True)

    os.environ["REMOTE_SOURCE_VALIDATION_QUEUE"] = str(queue_path)
    os.environ["REMOTE_SOURCE_VALIDATION_RESULTS"] = str(results_path)
    os.environ["REMOTE_SOURCE_VALIDATION_UNRESOLVED"] = str(unresolved_path)
    os.environ["REMOTE_SOURCE_VALIDATOR_LIMIT"] = "0"

    from tools.validation import remote_source_validator as validator

    print("REMOTE LINKEDIN SOURCE VALIDATION")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Validation queue:          {queue_path}")
    print(f"Results:                   {results_path}")
    print(f"Unresolved:                {unresolved_path}")
    print()
    print("Reusing existing live Remote source validator.")
    print()

    rc = validator.main()

    print()
    if rc == 0:
        print("REMOTE LINKEDIN SOURCE VALIDATION COMPLETE")
        print("No source was scored.")
        print("Excel was NOT touched.")
        print("Watermark was NOT advanced.")
    else:
        print("REMOTE LINKEDIN SOURCE VALIDATION FAILED")
        print("Watermark was NOT advanced.")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
