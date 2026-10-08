#!/usr/bin/env python3
"""
remote_instagram_reel_frame_harvester.py

Deeper harvest for Instagram reels whose first screenshot did not expose the
promised remote-job/source list.

Strategy:
- read run-scoped needs_deeper_harvest.jsonl
- open only Instagram reel targets
- reuse existing cookie/login-dialog handling
- sample the visible reel/article repeatedly while the video advances
- deduplicate frames by image hash
- save run-scoped frame images and results

No OCR, validation, scoring, Excel publication, or watermark advancement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.scraping.scraper_instagram import (
    dismiss_instagram_cookie_dialog,
    dismiss_instagram_login_dialog,
    try_get_main_article_locator,
)


SAMPLE_INTERVAL_MS = 1800
MAX_SAMPLES = 14
MAX_UNCHANGED_STREAK = 4


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
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


def post_id_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def capture_visible_content(page) -> bytes:
    article = try_get_main_article_locator(page)
    if article is not None:
        try:
            return article.screenshot()
        except Exception:
            pass
    return page.screenshot(full_page=False)


def try_start_video(page) -> str:
    """
    Instagram reels usually autoplay. If not, try a few conservative clicks.
    Failure is non-fatal because sampling can still reveal a useful poster frame.
    """
    selectors = [
        "article video",
        "main video",
        "video",
        "article",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=2000, position={"x": 20, "y": 20})
                page.wait_for_timeout(700)
                return selector
        except Exception:
            continue
    return "not_started_explicitly"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)
    instagram_dir = run_dir / "harvest" / "instagram"
    input_path = instagram_dir / "needs_deeper_harvest.jsonl"
    output_dir = instagram_dir / "reel_frames"
    results_path = instagram_dir / "reel_frame_results.jsonl"

    if not input_path.exists():
        raise RuntimeError(f"Deeper-harvest queue not found: {input_path}")

    rows = read_jsonl(input_path)
    reel_rows = [
        row for row in rows
        if "/reel/" in (row.get("instagram_url") or "")
    ]

    output_dir.mkdir(parents=True, exist_ok=True)

    print("REMOTE INSTAGRAM DEEP REEL HARVEST")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Targets:                   {len(reel_rows)}")
    print(f"Sample interval:           {SAMPLE_INTERVAL_MS} ms")
    print(f"Maximum samples/reel:      {MAX_SAMPLES}")
    print(f"Frame output:              {output_dir}")
    print(f"Results:                   {results_path}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        for index, row in enumerate(reel_rows, start=1):
            url = row["instagram_url"]
            post_id = row.get("post_id") or post_id_from_url(url)
            reel_dir = output_dir / post_id
            reel_dir.mkdir(parents=True, exist_ok=True)

            print(f"[{index}/{len(reel_rows)}] {url}")

            page = context.new_page()
            cookie_action = None
            login_action = None
            start_action = None
            saved_paths: list[str] = []
            seen_hashes: set[str] = set()
            unchanged_streak = 0

            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                cookie_action = dismiss_instagram_cookie_dialog(page)
                page.wait_for_timeout(800)
                login_action = dismiss_instagram_login_dialog(page)
                page.wait_for_timeout(800)

                start_action = try_start_video(page)

                for sample_no in range(1, MAX_SAMPLES + 1):
                    png = capture_visible_content(page)
                    digest = hashlib.sha256(png).hexdigest()

                    if digest in seen_hashes:
                        unchanged_streak += 1
                    else:
                        unchanged_streak = 0
                        seen_hashes.add(digest)
                        frame_no = len(saved_paths) + 1
                        frame_path = reel_dir / f"{post_id}_frame_{frame_no:02d}.png"
                        frame_path.write_bytes(png)
                        saved_paths.append(str(frame_path))

                    if unchanged_streak >= MAX_UNCHANGED_STREAK:
                        break

                    page.wait_for_timeout(SAMPLE_INTERVAL_MS)

                append_jsonl(results_path, {
                    "post_id": post_id,
                    "instagram_url": url,
                    "status": "ok",
                    "unique_frame_count": len(saved_paths),
                    "frame_paths": saved_paths,
                    "cookie_action": cookie_action or "not_found",
                    "login_action": login_action or "not_found",
                    "start_action": start_action,
                    "processed_at": datetime.now(UTC).isoformat(),
                })

                print(f"    unique frames: {len(saved_paths)}")

            except Exception as exc:
                append_jsonl(results_path, {
                    "post_id": post_id,
                    "instagram_url": url,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "unique_frame_count": len(saved_paths),
                    "frame_paths": saved_paths,
                    "cookie_action": cookie_action or "not_found",
                    "login_action": login_action or "not_found",
                    "start_action": start_action or "not_attempted",
                    "processed_at": datetime.now(UTC).isoformat(),
                })
                print(f"    ERROR: {type(exc).__name__}: {exc}")
            finally:
                try:
                    page.close()
                except Exception:
                    pass

        context.close()
        browser.close()

    print()
    print("REMOTE INSTAGRAM DEEP REEL HARVEST COMPLETE")
    print("OCR was NOT executed.")
    print("No source was validated or scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
