#!/usr/bin/env python3
"""
remote_tiktok_harvester.py

Run-scoped TikTok harvesting for Remote Golden refresh runs.

Input:
  <run_dir>/harvest/tiktok_queue.jsonl

Outputs:
  <run_dir>/harvest/tiktok/screenshots/
  <run_dir>/harvest/tiktok/html/
  <run_dir>/harvest/tiktok/results.jsonl

Strategy:
- use Playwright/Chromium already present in Prompt Extractor;
- resolve TikTok short links through normal browser navigation;
- save initial/final HTML and visible page text;
- capture a full-page screenshot;
- when a <video> element is available, attempt autoplay and capture a
  sequence of frames at approximately 2-second intervals, up to 16 frames;
- preserve failures as evidence instead of converting them into exclusion.

This stage performs harvesting only. It does not OCR, validate candidate
sources, score, update Excel, or advance the refresh watermark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parents[1]

FRAME_INTERVAL_SECONDS = 2.0
MAX_VIDEO_FRAMES = 16
NAV_TIMEOUT_MS = 45000
SETTLE_MS = 5000


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
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


def slug_for(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    tail = re.sub(r"[^A-Za-z0-9_-]+", "_", url.rstrip("/").split("/")[-1])[:40]
    return f"{tail or 'tiktok'}_{digest}"


def dismiss_overlays(page) -> list[str]:
    clicked: list[str] = []
    labels = [
        "Accept all",
        "Accept",
        "Allow all",
        "Agree",
        "Continue",
        "Not now",
        "Maybe later",
        "Close",
    ]
    for label in labels:
        try:
            locator = page.get_by_role("button", name=re.compile(f"^{re.escape(label)}$", re.I))
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=1500)
                clicked.append(label)
                page.wait_for_timeout(600)
        except Exception:
            pass
    return clicked


def visible_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000)[:30000]
    except Exception:
        return ""


def capture_video_frames(page, screenshot_dir: Path, slug: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "video_found": False,
        "play_action": "not_attempted",
        "duration_seconds": None,
        "frames_captured": 0,
        "frame_paths": [],
    }

    try:
        video = page.locator("video").first
        if video.count() == 0:
            return result

        result["video_found"] = True

        state = video.evaluate(
            """v => ({
                paused: v.paused,
                duration: Number.isFinite(v.duration) ? v.duration : null,
                currentTime: v.currentTime
            })"""
        )
        result["duration_seconds"] = state.get("duration")

        try:
            play_outcome = video.evaluate(
                """async v => {
                    if (!v.paused) return 'already_playing';
                    try { await v.play(); return 'play_requested'; }
                    catch (e) { return 'play_failed:' + String(e); }
                }"""
            )
            result["play_action"] = play_outcome
        except Exception as exc:
            result["play_action"] = f"play_exception:{type(exc).__name__}:{exc}"

        page.wait_for_timeout(700)

        # Capture the rendered page rather than the video element alone because
        # TikTok often overlays critical source names/captions around the video.
        seen_hashes: set[str] = set()
        for idx in range(MAX_VIDEO_FRAMES):
            path = screenshot_dir / f"{slug}_frame_{idx:02d}.png"
            page.screenshot(path=str(path), full_page=False)

            data = path.read_bytes()
            digest = hashlib.sha1(data).hexdigest()
            if digest in seen_hashes:
                path.unlink(missing_ok=True)
            else:
                seen_hashes.add(digest)
                result["frame_paths"].append(str(path))
                result["frames_captured"] += 1

            try:
                ended = bool(video.evaluate("v => v.ended"))
                duration = video.evaluate("v => Number.isFinite(v.duration) ? v.duration : null")
                current = video.evaluate("v => v.currentTime")
                if duration and current >= max(0, float(duration) - 0.25):
                    break
                if ended:
                    break
            except Exception:
                pass

            page.wait_for_timeout(int(FRAME_INTERVAL_SECONDS * 1000))

    except Exception as exc:
        result["video_error"] = f"{type(exc).__name__}: {exc}"

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)
    harvest_dir = run_dir / "harvest"
    input_path = harvest_dir / "tiktok_queue.jsonl"
    out_dir = harvest_dir / "tiktok"
    screenshot_dir = out_dir / "screenshots"
    html_dir = out_dir / "html"
    results_path = out_dir / "results.jsonl"

    if not input_path.exists():
        raise RuntimeError(f"TikTok queue not found: {input_path}")

    rows = read_jsonl(input_path)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)

    print("REMOTE TIKTOK HARVEST")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Input queue:               {input_path}")
    print(f"Targets:                   {len(rows)}")
    print(f"Screenshots:               {screenshot_dir}")
    print(f"HTML:                      {html_dir}")
    print(f"Results:                   {results_path}")
    print()

    ok = 0
    unresolved = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154 Safari/537.36"
            ),
        )

        for idx, row in enumerate(rows, start=1):
            url = str(row.get("canonical_url") or row.get("normalized_url") or row.get("raw_url") or "").strip()
            slug = slug_for(url)
            page = context.new_page()

            result: dict[str, Any] = {
                **row,
                "requested_url": url,
                "status": "unresolved",
                "final_url": "",
                "page_title": "",
                "visible_text": "",
                "overlay_actions": [],
                "full_screenshot": "",
                "html_path": "",
                "harvested_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(SETTLE_MS)
                result["overlay_actions"] = dismiss_overlays(page)
                page.wait_for_timeout(1000)

                result["final_url"] = page.url
                result["page_title"] = page.title()
                result["visible_text"] = visible_text(page)

                html_path = html_dir / f"{slug}.html"
                html_path.write_text(page.content(), encoding="utf-8", errors="ignore")
                result["html_path"] = str(html_path)

                full_path = screenshot_dir / f"{slug}_full.png"
                page.screenshot(path=str(full_path), full_page=True)
                result["full_screenshot"] = str(full_path)

                result.update(capture_video_frames(page, screenshot_dir, slug))

                has_evidence = bool(
                    result["visible_text"].strip()
                    or result.get("frames_captured", 0)
                    or result["final_url"]
                )
                result["status"] = "ok" if has_evidence else "unresolved"
                if has_evidence:
                    ok += 1
                else:
                    unresolved += 1

            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"
                unresolved += 1
            finally:
                append_jsonl(results_path, result)
                print(
                    f"[{idx}/{len(rows)}] {result['status']} "
                    f"{url} -> {result.get('final_url','')} "
                    f"video={result.get('video_found', False)} "
                    f"frames={result.get('frames_captured', 0)}"
                )
                sys.stdout.flush()
                page.close()

        context.close()
        browser.close()

    print()
    print("REMOTE TIKTOK HARVEST COMPLETE")
    print(f"Targets processed:         {len(rows)}")
    print(f"Harvested with evidence:   {ok}")
    print(f"Unresolved/access issues:  {unresolved}")
    print("OCR was NOT executed.")
    print("No source was validated or scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
