#!/usr/bin/env python3
"""
remote_discovery_resolver.py

Resolve Remote Opportunities discovery nodes into durable source evidence.

Designed for:
- social posts/pages
- click/redirect wrappers
- tracking links
- ambiguous discovery URLs

Strategy:
1. Fast HTTP resolution first (cheap, follows redirects).
2. For social nodes or weak HTTP results, use Playwright/Chromium.
3. Extract final URL, title, and external HTTP links from the rendered DOM.
4. Never discard failures: unresolved nodes are written explicitly.
5. Resume safely across long runs using an append-only results file.
6. Default to a 10-node pilot. Set REMOTE_RESOLVER_LIMIT=0 for full corpus.

Inputs:
- data/remote/discovery_nodes.jsonl

Outputs:
- data/remote/discovery_resolution_results.jsonl
- data/remote/discovery_resolution_unresolved.jsonl
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = Path(
    os.getenv("REMOTE_DISCOVERY_NODES", ROOT_DIR / "data/remote/discovery_nodes.jsonl")
)
RESULTS_PATH = Path(
    os.getenv(
        "REMOTE_DISCOVERY_RESULTS",
        ROOT_DIR / "data/remote/discovery_resolution_results.jsonl",
    )
)
UNRESOLVED_PATH = Path(
    os.getenv(
        "REMOTE_DISCOVERY_UNRESOLVED",
        ROOT_DIR / "data/remote/discovery_resolution_unresolved.jsonl",
    )
)

DEFAULT_LIMIT = 10
HTTP_TIMEOUT_SECONDS = float(os.getenv("REMOTE_HTTP_TIMEOUT", "20"))
BROWSER_TIMEOUT_MS = int(os.getenv("REMOTE_BROWSER_TIMEOUT_MS", "25000"))
PER_NODE_DELAY = float(os.getenv("REMOTE_RESOLVER_DELAY_SECONDS", "0.35"))

STATIC_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".wav", ".ogg", ".mp4", ".webm", ".mov", ".avi",
)

SOCIAL_BASE_DOMAINS = (
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "fb.watch",
    "linkedin.com",
    "youtube.com",
    "youtu.be",
    "x.com",
    "twitter.com",
    "pinterest.com",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def rewrite_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def canonical_host(host: str) -> str:
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def root_url(url: str) -> str:
    host = canonical_host(host_of(url))
    return f"https://{host}/" if host else ""


def is_social_host(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in SOCIAL_BASE_DOMAINS)


def is_static_url(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return path.endswith(STATIC_EXTENSIONS)


def normalize_href(href: str) -> str:
    return (href or "").strip()


def external_links(links: list[str], page_url: str) -> list[str]:
    page_host = canonical_host(host_of(page_url))
    seen: set[str] = set()
    result: list[str] = []

    for href in links:
        href = normalize_href(href)
        if not (href.startswith("http://") or href.startswith("https://")):
            continue
        if is_static_url(href):
            continue

        h = canonical_host(host_of(href))
        if not h or h == page_host:
            continue

        # Do not turn cross-links between social platforms into canonical
        # sources yet; they remain evidence rather than durable source IDs.
        if is_social_host(h):
            continue

        if href not in seen:
            seen.add(href)
            result.append(href)

    return result[:50]


def node_key(row: dict[str, Any]) -> str:
    url = str(row.get("normalized_url", "")).strip()
    msg = str(row.get("source_message_id", "")).strip()
    return f"{msg}|{url}"


def existing_processed_keys() -> set[str]:
    processed: set[str] = set()
    for path in (RESULTS_PATH, UNRESOLVED_PATH):
        for row in load_jsonl(path):
            key = str(row.get("node_key", "")).strip()
            if key:
                processed.add(key)
    return processed


def limit_from_env() -> int:
    raw = os.getenv("REMOTE_RESOLVER_LIMIT", str(DEFAULT_LIMIT)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"REMOTE_RESOLVER_LIMIT must be an integer, got: {raw}")
    return max(0, value)


def http_resolve(client: httpx.Client, url: str) -> dict[str, Any]:
    try:
        response = client.get(url)
        final_url = str(response.url)
        ctype = response.headers.get("content-type", "")
        title = ""
        if "text/html" in ctype.lower():
            match = re.search(
                r"<title[^>]*>(.*?)</title>",
                response.text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if match:
                title = re.sub(r"\s+", " ", match.group(1)).strip()

        return {
            "ok": True,
            "status_code": response.status_code,
            "final_url": final_url,
            "title": title,
            "content_type": ctype,
            "redirect_count": len(response.history),
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "final_url": "",
            "title": "",
            "content_type": "",
            "redirect_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def browser_resolve(page, url: str) -> dict[str, Any]:
    try:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=BROWSER_TIMEOUT_MS,
        )
        try:
            page.wait_for_timeout(1200)
        except Exception:
            pass

        final_url = page.url
        title = ""
        try:
            title = page.title()
        except Exception:
            pass

        try:
            hrefs = page.locator("a[href]").evaluate_all(
                "(els) => els.map(e => e.href)"
            )
        except Exception:
            hrefs = []

        return {
            "ok": True,
            "status_code": response.status if response else None,
            "final_url": final_url,
            "title": title,
            "external_links": external_links(hrefs, final_url or url),
            "error": "",
        }
    except PlaywrightTimeoutError as exc:
        return {
            "ok": False,
            "status_code": None,
            "final_url": page.url if page else "",
            "title": "",
            "external_links": [],
            "error": f"PlaywrightTimeoutError: {exc}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "final_url": page.url if page else "",
            "title": "",
            "external_links": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def should_use_browser(row: dict[str, Any], http_result: dict[str, Any]) -> bool:
    url = str(row.get("normalized_url", ""))
    host = host_of(url)
    node_type = str(row.get("discovery_node_type", ""))

    if node_type == "social" or is_social_host(host):
        return True

    if not http_result.get("ok"):
        return True

    status = http_result.get("status_code")
    if status in {401, 403, 429}:
        return True

    return False


def process_node(client: httpx.Client, page, row: dict[str, Any]) -> dict[str, Any]:
    url = str(row.get("normalized_url", "")).strip()
    http_result = http_resolve(client, url)

    browser_result = None
    if should_use_browser(row, http_result):
        browser_result = browser_resolve(page, url)

    chosen = browser_result if browser_result and browser_result.get("ok") else http_result

    final_url = str(chosen.get("final_url", "") or "").strip()
    final_host = canonical_host(host_of(final_url))
    ext_links = (
        browser_result.get("external_links", [])
        if browser_result
        else []
    )

    candidate_roots: list[str] = []
    seen_roots: set[str] = set()

    if final_url and final_host and not is_social_host(final_host):
        root = root_url(final_url)
        if root and root not in seen_roots:
            seen_roots.add(root)
            candidate_roots.append(root)

    for link in ext_links:
        root = root_url(link)
        if root and root not in seen_roots:
            seen_roots.add(root)
            candidate_roots.append(root)

    resolved = bool(chosen.get("ok")) and bool(final_url)

    return {
        "node_key": node_key(row),
        "original_url": row.get("original_url"),
        "normalized_url": url,
        "source_message_id": row.get("source_message_id"),
        "source_subject": row.get("source_subject"),
        "source_from": row.get("source_from"),
        "email_datetime": row.get("email_datetime"),
        "discovery_node_type": row.get("discovery_node_type"),
        "triage_priority": row.get("triage_priority"),
        "resolved": resolved,
        "final_url": final_url,
        "final_host": final_host,
        "canonical_source_roots": candidate_roots,
        "external_links": ext_links,
        "page_title": chosen.get("title", ""),
        "http_status": http_result.get("status_code"),
        "http_redirect_count": http_result.get("redirect_count", 0),
        "http_error": http_result.get("error", ""),
        "browser_used": browser_result is not None,
        "browser_status": browser_result.get("status_code") if browser_result else None,
        "browser_error": browser_result.get("error", "") if browser_result else "",
        "status": "resolved" if resolved else "unresolved",
        "resolved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main() -> int:
    if not INPUT_PATH.exists():
        print(f"Fatal error: input not found: {INPUT_PATH}", file=sys.stderr)
        return 1

    nodes = load_jsonl(INPUT_PATH)
    processed = existing_processed_keys()
    pending = [row for row in nodes if node_key(row) not in processed]

    limit = limit_from_env()
    batch = pending if limit == 0 else pending[:limit]

    print(
        json.dumps(
            {
                "status": "starting",
                "total_nodes": len(nodes),
                "already_processed": len(processed),
                "pending_nodes": len(pending),
                "run_limit": limit,
                "this_run_nodes": len(batch),
            },
            ensure_ascii=False,
        )
    )
    sys.stdout.flush()

    if not batch:
        print(json.dumps({"status": "ok", "message": "No pending nodes."}))
        return 0

    resolved_count = 0
    unresolved_count = 0
    roots_found = 0

    client = httpx.Client(
        follow_redirects=True,
        timeout=HTTP_TIMEOUT_SECONDS,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154 Safari/537.36"
            )
        },
    )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/154 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()

            for index, row in enumerate(batch, start=1):
                result = process_node(client, page, row)

                if result["resolved"]:
                    append_jsonl(RESULTS_PATH, result)
                    resolved_count += 1
                    roots_found += len(result.get("canonical_source_roots", []))
                else:
                    append_jsonl(UNRESOLVED_PATH, result)
                    unresolved_count += 1

                print(
                    f"[{index}/{len(batch)}] "
                    f"{result['status']} "
                    f"{result.get('normalized_url', '')} "
                    f"-> {result.get('final_url', '')} "
                    f"roots={len(result.get('canonical_source_roots', []))}"
                )
                sys.stdout.flush()

                if PER_NODE_DELAY > 0:
                    time.sleep(PER_NODE_DELAY)

            context.close()
            browser.close()

    finally:
        client.close()

    print(
        json.dumps(
            {
                "status": "ok",
                "processed_this_run": len(batch),
                "resolved_this_run": resolved_count,
                "unresolved_this_run": unresolved_count,
                "canonical_source_roots_found": roots_found,
                "results_output": str(RESULTS_PATH),
                "unresolved_output": str(UNRESOLVED_PATH),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
