#!/usr/bin/env python3
"""
scraper_instagram.py

Reads Instagram queue and:
- visits each URL using Playwright
- dismisses Instagram cookie dialog when present
- dismisses Instagram login/signup modal when present
- saves screenshot(s)
- saves HTML
- logs results

Input:
- data/queues/instagram.jsonl

Output:
- data/instagram/screenshots/
- data/instagram/html/
- data/instagram/results.jsonl

Behavior:
- /p/ posts: carousel-aware, captures one image per slide
- /reel/ posts: single screenshot
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright


BASE_PATH = Path(__file__).resolve().parent.parent
INPUT_JSONL = BASE_PATH / "data/queues/instagram.jsonl"
BASE_DIR = BASE_PATH / "data/instagram"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
HTML_DIR = BASE_DIR / "html"
RESULTS_FILE = BASE_DIR / "results.jsonl"

# Temporary throttle for validation.
# Set to None to process all remaining URLs.
MAX_URLS =  None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_processed_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()

    processed: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                normalized_url = data.get("normalized_url")
                if normalized_url:
                    processed.add(normalized_url)
            except Exception:
                continue
    return processed


def url_to_id(url: str) -> str:
    parts = url.rstrip("/").split("/")
    return parts[-1]


def detect_instagram_content_type(url: str) -> str:
    parts = url.rstrip("/").split("/")
    if len(parts) < 2:
        return "unknown"

    kind = parts[-2].lower()
    if kind == "p":
        return "post"
    if kind in {"reel", "reels"}:
        return "reel"
    return "unknown"


def dismiss_instagram_cookie_dialog(page) -> str | None:
    selectors = [
        "button:has-text('Decline optional cookies')",
        "button:has-text('Allow all cookies')",
        "text='Decline optional cookies'",
        "text='Allow all cookies'",
    ]

    for selector in selectors:
        try:
            page.locator(selector).first.click(timeout=3000)
            page.wait_for_timeout(1500)
            return selector
        except Exception:
            continue

    return None


def dismiss_instagram_login_dialog(page) -> str | None:
    selectors = [
        "button:has-text('Not now')",
        "text='Not now'",
        "svg[aria-label='Close']",
        "button svg[aria-label='Close']",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.click(timeout=3000)
            page.wait_for_timeout(1500)
            return selector
        except Exception:
            continue

    try:
        page.evaluate(
            """
            () => {
                document.querySelectorAll('div[role="dialog"]').forEach(el => el.remove());
                document.body.style.overflow = 'auto';
            }
            """
        )
        return "js_removed_dialog"
    except Exception:
        return None


def try_get_main_article_locator(page):
    candidates = [
        "main article",
        "article",
        "div[role='presentation'] article",
    ]

    for selector in candidates:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                return locator
        except Exception:
            continue

    return None


def capture_article_png_bytes(page) -> bytes:
    article = try_get_main_article_locator(page)
    if article is not None:
        try:
            return article.screenshot()
        except Exception:
            pass

    return page.screenshot(full_page=True)


def save_article_screenshot(page, output_path: Path) -> None:
    article = try_get_main_article_locator(page)
    if article is not None:
        try:
            article.screenshot(path=str(output_path))
            return
        except Exception:
            pass

    page.screenshot(path=str(output_path), full_page=False)


def get_visual_signature(page) -> str:
    """
    Use screenshot bytes hash instead of page text.
    This is much more reliable for image-only carousel slides.
    """
    png_bytes = capture_article_png_bytes(page)
    return hashlib.sha256(png_bytes).hexdigest()


def click_carousel_next(page) -> str | None:
    """
    Try likely selectors for Instagram carousel next arrow.
    """
    selectors = [
        "article button[aria-label='Next']",
        "main article button[aria-label='Next']",
        "button[aria-label='Next']",
        "article svg[aria-label='Next']",
        "button svg[aria-label='Next']",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.click(timeout=3000)
            page.wait_for_timeout(1800)
            return selector
        except Exception:
            continue

    return None


def finalize_slide_names(post_dir: Path, post_id: str, temp_paths: list[Path]) -> list[str]:
    total = len(temp_paths)
    final_paths: list[str] = []

    for idx, temp_path in enumerate(temp_paths, start=1):
        final_name = f"{post_id}_slide_{idx:02d}_of_{total:02d}.png"
        final_path = post_dir / final_name
        temp_path.rename(final_path)
        final_paths.append(str(final_path))

    return final_paths


def capture_carousel_slides(page, post_id: str) -> tuple[list[str], int, list[str]]:
    """
    Capture slide 1..N for a carousel post until no further advancement is possible.
    Saves temp files first, then renames to:
      POSTID_slide_01_of_03.png
      POSTID_slide_02_of_03.png
      ...
    """
    post_dir = SCREENSHOT_DIR / post_id
    post_dir.mkdir(parents=True, exist_ok=True)

    temp_paths: list[Path] = []
    navigation_actions: list[str] = []
    previous_signatures: set[str] = set()

    # Capture first visible slide
    first_signature = get_visual_signature(page)
    previous_signatures.add(first_signature)

    slide_index = 1
    temp_path = post_dir / f"tmp_slide_{slide_index:02d}.png"
    save_article_screenshot(page, temp_path)
    temp_paths.append(temp_path)

    max_slides_guard = 20
    while slide_index < max_slides_guard:
        next_action = click_carousel_next(page)
        if not next_action:
            break

        navigation_actions.append(next_action)

        # Give Instagram one more beat after click
        page.wait_for_timeout(1200)

        current_signature = get_visual_signature(page)

        # If nothing visually changed, stop
        if current_signature in previous_signatures:
            break

        previous_signatures.add(current_signature)
        slide_index += 1

        temp_path = post_dir / f"tmp_slide_{slide_index:02d}.png"
        save_article_screenshot(page, temp_path)
        temp_paths.append(temp_path)

    final_paths = finalize_slide_names(post_dir, post_id, temp_paths)
    return final_paths, len(final_paths), navigation_actions


def capture_single_content(page, post_id: str) -> tuple[list[str], int]:
    screenshot_path = SCREENSHOT_DIR / f"{post_id}.png"
    save_article_screenshot(page, screenshot_path)
    return [str(screenshot_path)], 1


def main() -> int:
    input_path = INPUT_JSONL

    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        return 1

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    rows = load_jsonl(input_path)
    processed_urls = load_processed_urls(RESULTS_FILE)

    print(f"Total URLs: {len(rows)}")
    print(f"Already processed: {len(processed_urls)}")
    print(f"MAX_URLS: {MAX_URLS}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        processed_count = 0

        for i, row in enumerate(rows, start=1):
            url = row["normalized_url"]

            if url in processed_urls:
                continue

            if MAX_URLS is not None and processed_count >= MAX_URLS:
                print(f"Reached MAX_URLS limit ({MAX_URLS}). Stopping early.")
                break

            post_id = url_to_id(url)
            html_path = HTML_DIR / f"{post_id}.html"
            content_type = detect_instagram_content_type(url)

            print(f"[{i}] Processing: {url}")

            cookie_action: str | None = None
            login_action: str | None = None
            slide_paths: list[str] = []
            slide_count = 0
            navigation_actions: list[str] = []

            page = context.new_page()
            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                cookie_action = dismiss_instagram_cookie_dialog(page)
                page.wait_for_timeout(1500)

                login_action = dismiss_instagram_login_dialog(page)
                page.wait_for_timeout(1500)

                if content_type == "post":
                    slide_paths, slide_count, navigation_actions = capture_carousel_slides(page, post_id)
                else:
                    slide_paths, slide_count = capture_single_content(page, post_id)

                html = page.content()
                with html_path.open("w", encoding="utf-8") as f:
                    f.write(html)

                result = {
                    "normalized_url": url,
                    "content_type": content_type,
                    "status": "ok",
                    "slide_count": slide_count,
                    "slide_paths": slide_paths,
                    "html_path": str(html_path),
                    "processed_at": datetime.now(UTC).isoformat(),
                    "cookie_action": cookie_action or "not_found",
                    "login_action": login_action or "not_found",
                    "navigation_actions": navigation_actions,
                }
                append_jsonl(RESULTS_FILE, result)
                processed_count += 1

            except Exception as e:
                result = {
                    "normalized_url": url,
                    "content_type": content_type,
                    "status": "error",
                    "error": str(e),
                    "slide_count": slide_count,
                    "slide_paths": slide_paths,
                    "html_path": str(html_path),
                    "processed_at": datetime.now(UTC).isoformat(),
                    "cookie_action": cookie_action or "not_found",
                    "login_action": login_action or "not_found",
                    "navigation_actions": navigation_actions,
                }
                append_jsonl(RESULTS_FILE, result)
                processed_count += 1
                print("Error:", e)

            finally:
                try:
                    page.close()
                except Exception:
                    pass

        context.close()
        browser.close()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
