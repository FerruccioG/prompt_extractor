#!/usr/bin/env python3
"""
remote_linkedin_ocr.py

Run-scoped full-frame OCR for LinkedIn screenshots harvested by Remote Golden refresh.

Input:
  <run>/harvest/linkedin/screenshots/

Output:
  <run>/harvest/linkedin/ocr_raw.jsonl

This stage performs OCR only. It does not infer candidate-source identities,
validate sources, score candidates, update Excel, or advance the watermark.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.vision import ocr_extractor as ocr


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


def configure_run_paths(run_dir: Path) -> dict[str, Path]:
    linkedin_dir = run_dir / "harvest" / "linkedin"

    paths = {
        "screenshots": linkedin_dir / "screenshots",
        "output": linkedin_dir / "ocr_raw.jsonl",
    }

    ocr.SCREENSHOTS_DIR = paths["screenshots"]
    ocr.OUTPUT_JSONL = paths["output"]

    # Job/company/source names can appear anywhere on LinkedIn's rendered page,
    # especially on job cards, group posts, and access-limited screens.
    ocr.TOP_REGION_RATIO = 1.0
    ocr.MAX_IMAGES = None

    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Refresh run directory. Defaults to latest run.",
    )
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)
    paths = configure_run_paths(run_dir)

    if not paths["screenshots"].exists():
        raise RuntimeError(
            f"LinkedIn screenshots directory not found: {paths['screenshots']}"
        )

    print("REMOTE LINKEDIN OCR")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Screenshots:               {paths['screenshots']}")
    print(f"OCR output:                {paths['output']}")
    print("OCR region:                full image (100%)")
    print()
    print("Reusing existing Prompt Extractor OCR engine.")
    print()

    rc = ocr.main()

    print()
    if rc == 0:
        print("REMOTE LINKEDIN OCR COMPLETE")
        print("No candidate source identity was inferred.")
        print("No source was validated or scored.")
        print("Excel was NOT touched.")
        print("Watermark was NOT advanced.")
    else:
        print("REMOTE LINKEDIN OCR FAILED")
        print("Watermark was NOT advanced.")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
