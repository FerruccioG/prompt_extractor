#!/usr/bin/env python3
"""
remote_instagram_harvester.py

Run-scoped Instagram harvesting for Remote Golden refresh runs.

Reuses the existing Prompt Extractor Instagram Playwright harvester while
redirecting all input/output paths into the selected refresh run.

Input:
  <run_dir>/harvest/instagram_queue.jsonl

Outputs:
  <run_dir>/harvest/instagram/screenshots/
  <run_dir>/harvest/instagram/html/
  <run_dir>/harvest/instagram/results.jsonl

This stage performs browser harvesting only. It does not run OCR, validate
sources, score candidates, update Excel, or advance the refresh watermark.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.scraping import scraper_instagram as instagram


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
    harvest_dir = run_dir / "harvest"
    instagram_dir = harvest_dir / "instagram"

    paths = {
        "input": harvest_dir / "instagram_queue.jsonl",
        "base": instagram_dir,
        "screenshots": instagram_dir / "screenshots",
        "html": instagram_dir / "html",
        "results": instagram_dir / "results.jsonl",
    }

    instagram.INPUT_JSONL = paths["input"]
    instagram.BASE_DIR = paths["base"]
    instagram.SCREENSHOT_DIR = paths["screenshots"]
    instagram.HTML_DIR = paths["html"]
    instagram.RESULTS_FILE = paths["results"]
    instagram.MAX_URLS = None

    return paths


def main() -> int:
    root = ROOT_DIR

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Refresh run directory. Defaults to latest run.",
    )
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    paths = configure_run_paths(run_dir)

    if not paths["input"].exists():
        raise RuntimeError(f"Instagram queue not found: {paths['input']}")

    print("REMOTE INSTAGRAM HARVEST")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Input queue:               {paths['input']}")
    print(f"Screenshots:               {paths['screenshots']}")
    print(f"HTML:                      {paths['html']}")
    print(f"Results:                   {paths['results']}")
    print()
    print("Reusing existing Prompt Extractor Instagram harvester.")
    print("Carousel posts will traverse slides; reels use the current single-capture behavior.")
    print()

    rc = instagram.main()

    print()
    if rc == 0:
        print("REMOTE INSTAGRAM HARVEST COMPLETE")
        print("OCR was NOT executed.")
        print("No source was validated or scored.")
        print("Excel was NOT touched.")
        print("Watermark was NOT advanced.")
    else:
        print("REMOTE INSTAGRAM HARVEST FAILED")
        print("Watermark was NOT advanced.")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
