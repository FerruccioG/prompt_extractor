#!/usr/bin/env python3
"""
remote_linkedin_harvester.py

Run-scoped LinkedIn harvesting for Remote Golden refresh runs.

Input:
  <run>/harvest/linkedin_queue.jsonl

Outputs:
  <run>/harvest/linkedin/screenshots/
  <run>/harvest/linkedin/html/
  <run>/harvest/linkedin/results.jsonl

Strategy:
- use Playwright/Chromium already present in Prompt Extractor;
- navigate each canonical LinkedIn job/post/group URL;
- preserve final URL, page title, visible text, HTML, and full-page screenshot;
- attempt to dismiss common cookie/sign-in overlays without authenticating;
- classify obvious access walls separately from ordinary successful harvests;
- never treat access failure as source-quality failure.

This stage performs harvesting only. It does not infer final source identities,
validate candidate sources, score, update Excel, or advance the watermark.
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

NAV_TIMEOUT_MS = 45000
SETTLE_MS = 4500
TEXT_LIMIT = 40000

ACCESS_WALL_MARKERS = (
    "sign in to linkedin",
    "join linkedin",
    "authwall",
    "login",
    "session_redirect",
    "checkpoint",
)


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
    tail = re.sub(r"[^A-Za-z0-9_-]+", "_", url.rstrip("/").split("/")[-1])[:48]
    return f"{tail or 'linkedin'}_{digest}"


def dismiss_overlays(page) -> list[str]:
    clicked: list[str] = []
    labels = [
        "Accept cookies",
        "Accept",
        "Agree",
        "Allow all",
        "Dismiss",
        "Not now",
        "Close",
        "Maybe later",
    ]
    for label in labels:
        try:
            locator = page.get_by_role(
                "button",
                name=re.compile(f"^{re.escape(label)}$", re.I),
            )
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=1500)
                clicked.append(label)
                page.wait_for_timeout(500)
        except Exception:
            pass
    return clicked


def visible_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000)[:TEXT_LIMIT]
    except Exception:
        return ""


def infer_content_kind(url: str) -> str:
    lower = (url or "").lower()
    if "/jobs/view/" in lower or "/jobs/" in lower:
        return "job"
    if "/feed/update/" in lower or "/posts/" in lower:
        return "post"
    if "/groups/" in lower:
        return "group"
    return "linkedin"


def access_state(final_url: str, title: str, text: str) -> tuple[str, str]:
    blob = f"{final_url} {title} {text}".lower()
    for marker in ACCESS_WALL_MARKERS:
        if marker in blob:
            return "access_limited", marker
    if text.strip():
        return "ok", ""
    return "unresolved", "no_visible_text"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)
    harvest_dir = run_dir / "harvest"
    input_path = harvest_dir / "linkedin_queue.jsonl"
    out_dir = harvest_dir / "linkedin"
    screenshot_dir = out_dir / "screenshots"
    html_dir = out_dir / "html"
    results_path = out_dir / "results.jsonl"

    if not input_path.exists():
        raise RuntimeError(f"LinkedIn queue not found: {input_path}")

    rows = read_jsonl(input_path)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)

    print("REMOTE LINKEDIN HARVEST")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Input queue:               {input_path}")
    print(f"Targets:                   {len(rows)}")
    print(f"Screenshots:               {screenshot_dir}")
    print(f"HTML:                      {html_dir}")
    print(f"Results:                   {results_path}")
    print()

    ok = 0
    limited = 0
    unresolved = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154 Safari/537.36"
            ),
        )

        for idx, row in enumerate(rows, start=1):
            url = str(
                row.get("canonical_url")
                or row.get("normalized_url")
                or row.get("raw_url")
                or ""
            ).strip()
            slug = slug_for(url)
            page = context.new_page()

            result: dict[str, Any] = {
                **row,
                "requested_url": url,
                "content_kind": infer_content_kind(url),
                "status": "unresolved",
                "access_reason": "",
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
                page.wait_for_timeout(800)

                result["final_url"] = page.url
                result["page_title"] = page.title()
                result["visible_text"] = visible_text(page)

                html_path = html_dir / f"{slug}.html"
                html_path.write_text(page.content(), encoding="utf-8", errors="ignore")
                result["html_path"] = str(html_path)

                full_path = screenshot_dir / f"{slug}.png"
                page.screenshot(path=str(full_path), full_page=True)
                result["full_screenshot"] = str(full_path)

                state, reason = access_state(
                    result["final_url"],
                    result["page_title"],
                    result["visible_text"],
                )
                result["status"] = state
                result["access_reason"] = reason

                if state == "ok":
                    ok += 1
                elif state == "access_limited":
                    limited += 1
                else:
                    unresolved += 1

            except Exception as exc:
                result["status"] = "unresolved"
                result["access_reason"] = f"{type(exc).__name__}: {exc}"
                unresolved += 1
            finally:
                append_jsonl(results_path, result)
                print(
                    f"[{idx}/{len(rows)}] {result['status']} "
                    f"{result['content_kind']} "
                    f"{url} -> {result.get('final_url','')}"
                )
                sys.stdout.flush()
                page.close()

        context.close()
        browser.close()

    print()
    print("REMOTE LINKEDIN HARVEST COMPLETE")
    print(f"Targets processed:         {len(rows)}")
    print(f"Harvested with text:       {ok}")
    print(f"Access-limited:            {limited}")
    print(f"Unresolved/errors:         {unresolved}")
    print("OCR was NOT executed.")
    print("No candidate source identity was inferred.")
    print("No source was validated or scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
