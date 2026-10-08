#!/usr/bin/env python3
"""
remote_instagram_ocr.py

Run-scoped OCR for Instagram evidence harvested by Remote Golden refresh.

Reuses the existing Prompt Extractor OCR engine, but scans the FULL screenshot
for Remote Opportunities because job/source names can appear anywhere in a
social-media frame.

Input:
  <run_dir>/harvest/instagram/screenshots/

Output:
  <run_dir>/harvest/instagram/ocr_raw.jsonl

This stage performs OCR only. It does not validate sources, score candidates,
update Excel, or advance the refresh watermark.
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
    instagram_dir = run_dir / "harvest" / "instagram"

    paths = {
        "screenshots": instagram_dir / "screenshots",
        "output": instagram_dir / "ocr_raw.jsonl",
    }

    ocr.SCREENSHOTS_DIR = paths["screenshots"]
    ocr.OUTPUT_JSONL = paths["output"]

    # Prompt Extractor historically OCRs only the upper 55%.
    # Remote job/source cards may place names and URLs anywhere in the frame,
    # so Remote Golden deliberately scans the complete screenshot.
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
            f"Instagram screenshots directory not found: {paths['screenshots']}"
        )

    print("REMOTE INSTAGRAM OCR")
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
        print("REMOTE INSTAGRAM OCR COMPLETE")
        print("No source was validated or scored.")
        print("Excel was NOT touched.")
        print("Watermark was NOT advanced.")
    else:
        print("REMOTE INSTAGRAM OCR FAILED")
        print("Watermark was NOT advanced.")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
