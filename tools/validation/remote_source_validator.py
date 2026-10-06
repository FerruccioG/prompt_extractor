#!/usr/bin/env python3
"""
remote_source_validator.py

Live validation evidence collector for Candidate <-> Remote Source relationships.

Input:
- data/remote/source_validation_queue.jsonl

Outputs:
- data/remote/source_validation_results.jsonl
- data/remote/source_validation_unresolved.jsonl

Strategy:
1. HTTP fetch the canonical root.
2. Follow redirects and record final URL/host/status/title/meta description.
3. Extract candidate-facing links (jobs/careers/apply/talent/remote/etc.).
4. Use Playwright only when HTTP is weak/blocked or insufficient.
5. Save compact page-text evidence for later semantic scoring.
6. Append results immediately and resume safely.
7. Default pilot size = 10. Set REMOTE_SOURCE_VALIDATOR_LIMIT=0 for full queue.

This stage gathers evidence. It does NOT yet make the final Golden Excel
relationship decision or Candidate <-> Source score.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = Path(os.getenv(
    "REMOTE_SOURCE_VALIDATION_QUEUE",
    ROOT_DIR / "data/remote/source_validation_queue.jsonl",
))
RESULTS_PATH = Path(os.getenv(
    "REMOTE_SOURCE_VALIDATION_RESULTS",
    ROOT_DIR / "data/remote/source_validation_results.jsonl",
))
UNRESOLVED_PATH = Path(os.getenv(
    "REMOTE_SOURCE_VALIDATION_UNRESOLVED",
    ROOT_DIR / "data/remote/source_validation_unresolved.jsonl",
))

DEFAULT_LIMIT = 10
HTTP_TIMEOUT = float(os.getenv("REMOTE_SOURCE_VALIDATOR_HTTP_TIMEOUT", "20"))
BROWSER_TIMEOUT_MS = int(os.getenv("REMOTE_SOURCE_VALIDATOR_BROWSER_TIMEOUT_MS", "25000"))
DELAY_SECONDS = float(os.getenv("REMOTE_SOURCE_VALIDATOR_DELAY_SECONDS", "0.35"))
TEXT_LIMIT = int(os.getenv("REMOTE_SOURCE_VALIDATOR_TEXT_LIMIT", "5000"))

CANDIDATE_TERMS = (
    "job", "jobs", "career", "careers", "hiring", "recruit", "recruitment",
    "talent", "vacancy", "vacancies", "apply", "opportunity", "opportunities",
    "freelance", "contract", "consultant", "remote", "work from home",
    "work-from-home", "distributed", "anywhere",
)

REMOTE_TERMS = (
    "remote", "work from home", "work-from-home", "distributed",
    "work anywhere", "anywhere", "home-based", "home based",
)

JOB_LINK_TERMS = (
    "job", "jobs", "career", "careers", "vacanc", "apply", "recruit",
    "talent", "hiring", "opportunit", "positions", "openings",
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


def host_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def compact_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def strip_html(markup: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", markup or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return compact_space(html.unescape(text))


def extract_title(markup: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", markup or "")
    return compact_space(html.unescape(m.group(1))) if m else ""


def extract_meta_description(markup: str) -> str:
    patterns = (
        r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'(?is)<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
        r'(?is)<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
    )
    for pattern in patterns:
        m = re.search(pattern, markup or "")
        if m:
            return compact_space(html.unescape(m.group(1)))
    return ""


def extract_links(markup: str, base_url: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'(?is)<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        markup or "",
    ):
        href = html.unescape(m.group(1)).strip()
        anchor = strip_html(m.group(2))
        absolute = urljoin(base_url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        key = absolute.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        haystack = f"{absolute} {anchor}".lower()
        if any(term in haystack for term in JOB_LINK_TERMS):
            out.append({"url": key, "text": anchor[:240]})
    return out[:50]


def term_hits(text: str, terms: tuple[str, ...]) -> list[str]:
    lower = (text or "").lower()
    return [term for term in terms if term in lower]


def validation_key(row: dict[str, Any]) -> str:
    return str(row.get("canonical_host", "")).strip().lower()


def processed_keys() -> set[str]:
    done: set[str] = set()
    for path in (RESULTS_PATH, UNRESOLVED_PATH):
        for row in load_jsonl(path):
            key = str(row.get("validation_key", "")).strip().lower()
            if key:
                done.add(key)
    return done


def limit_from_env() -> int:
    raw = os.getenv("REMOTE_SOURCE_VALIDATOR_LIMIT", str(DEFAULT_LIMIT)).strip()
    value = int(raw)
    return max(0, value)


def http_collect(client: httpx.Client, url: str) -> dict[str, Any]:
    try:
        response = client.get(url)
        ctype = response.headers.get("content-type", "")
        markup = response.text if "html" in ctype.lower() else ""
        text = strip_html(markup)[:TEXT_LIMIT]
        return {
            "ok": True,
            "status_code": response.status_code,
            "final_url": str(response.url),
            "redirect_count": len(response.history),
            "content_type": ctype,
            "title": extract_title(markup),
            "meta_description": extract_meta_description(markup),
            "page_text": text,
            "candidate_links": extract_links(markup, str(response.url)),
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "final_url": "",
            "redirect_count": 0,
            "content_type": "",
            "title": "",
            "meta_description": "",
            "page_text": "",
            "candidate_links": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def browser_collect(page, url: str) -> dict[str, Any]:
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_timeout(1200)

        title = ""
        try:
            title = page.title()
        except Exception:
            pass

        description = ""
        try:
            description = page.locator('meta[name="description"]').get_attribute("content") or ""
        except Exception:
            pass

        text = ""
        try:
            text = compact_space(page.locator("body").inner_text())[:TEXT_LIMIT]
        except Exception:
            pass

        links: list[dict[str, str]] = []
        try:
            raw_links = page.locator("a[href]").evaluate_all(
                """els => els.map(e => ({url:e.href, text:(e.innerText||e.textContent||'').trim()}))"""
            )
            seen: set[str] = set()
            for item in raw_links:
                u = str(item.get("url", "")).split("#", 1)[0]
                a = compact_space(str(item.get("text", "")))
                haystack = f"{u} {a}".lower()
                if u.startswith(("http://", "https://")) and any(t in haystack for t in JOB_LINK_TERMS):
                    if u not in seen:
                        seen.add(u)
                        links.append({"url": u, "text": a[:240]})
                if len(links) >= 50:
                    break
        except Exception:
            pass

        return {
            "ok": True,
            "status_code": response.status if response else None,
            "final_url": page.url,
            "title": title,
            "meta_description": compact_space(description),
            "page_text": text,
            "candidate_links": links,
            "error": "",
        }
    except PlaywrightTimeoutError as exc:
        return {
            "ok": False, "status_code": None, "final_url": page.url if page else "",
            "title": "", "meta_description": "", "page_text": "",
            "candidate_links": [], "error": f"PlaywrightTimeoutError: {exc}",
        }
    except Exception as exc:
        return {
            "ok": False, "status_code": None, "final_url": page.url if page else "",
            "title": "", "meta_description": "", "page_text": "",
            "candidate_links": [], "error": f"{type(exc).__name__}: {exc}",
        }


def should_use_browser(http_result: dict[str, Any]) -> bool:
    if not http_result.get("ok"):
        return True
    if http_result.get("status_code") in {401, 403, 406, 429, 503}:
        return True
    if len(str(http_result.get("page_text", ""))) < 250:
        return True
    if not http_result.get("candidate_links"):
        return True
    return False


def process_source(client: httpx.Client, page, row: dict[str, Any]) -> dict[str, Any]:
    root = str(row.get("canonical_root_url", "")).strip()
    if not root:
        root = f"https://{row.get('canonical_host', '')}/"

    http_result = http_collect(client, root)
    browser_result = browser_collect(page, root) if should_use_browser(http_result) else None

    chosen = (
        browser_result
        if browser_result and browser_result.get("ok")
        else http_result
    )

    text_blob = " ".join([
        str(chosen.get("title", "")),
        str(chosen.get("meta_description", "")),
        str(chosen.get("page_text", "")),
        " ".join(
            f"{x.get('text','')} {x.get('url','')}"
            for x in chosen.get("candidate_links", [])
        ),
    ])

    candidate_hits = term_hits(text_blob, CANDIDATE_TERMS)
    remote_hits = term_hits(text_blob, REMOTE_TERMS)
    final_url = str(chosen.get("final_url", "") or "").strip()

    resolved = bool(chosen.get("ok")) and bool(final_url)

    return {
        "validation_key": validation_key(row),
        "canonical_host": row.get("canonical_host"),
        "canonical_root_url": root,
        "prevalidation_score": row.get("prevalidation_score"),
        "prevalidation_reason": row.get("prevalidation_reason"),
        "email_evidence_count": row.get("email_evidence_count", 0),
        "evidence_url_count": row.get("evidence_url_count", 0),
        "discovery_resolution_evidence_count": row.get("discovery_resolution_evidence_count", 0),
        "resolved": resolved,
        "final_url": final_url,
        "final_host": host_of(final_url),
        "http_status": http_result.get("status_code"),
        "http_redirect_count": http_result.get("redirect_count", 0),
        "http_error": http_result.get("error", ""),
        "browser_used": browser_result is not None,
        "browser_status": browser_result.get("status_code") if browser_result else None,
        "browser_error": browser_result.get("error", "") if browser_result else "",
        "page_title": chosen.get("title", ""),
        "meta_description": chosen.get("meta_description", ""),
        "page_text_excerpt": str(chosen.get("page_text", ""))[:TEXT_LIMIT],
        "candidate_links": chosen.get("candidate_links", []),
        "candidate_term_hits": candidate_hits,
        "remote_term_hits": remote_hits,
        "candidate_link_count": len(chosen.get("candidate_links", [])),
        "validated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "resolved" if resolved else "unresolved",
    }


def main() -> int:
    if not INPUT_PATH.exists():
        print(f"Fatal error: input not found: {INPUT_PATH}", file=sys.stderr)
        return 1

    rows = load_jsonl(INPUT_PATH)
    done = processed_keys()
    pending = [row for row in rows if validation_key(row) not in done]

    limit = limit_from_env()
    batch = pending if limit == 0 else pending[:limit]

    print(json.dumps({
        "status": "starting",
        "total_sources": len(rows),
        "already_processed": len(done),
        "pending_sources": len(pending),
        "run_limit": limit,
        "this_run_sources": len(batch),
    }, ensure_ascii=False))
    sys.stdout.flush()

    if not batch:
        print(json.dumps({"status": "ok", "message": "No pending sources."}))
        return 0

    client = httpx.Client(
        follow_redirects=True,
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154 Safari/537.36"
            )
        },
    )

    resolved = 0
    unresolved = 0
    candidate_links_found = 0

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

            for idx, row in enumerate(batch, start=1):
                result = process_source(client, page, row)
                if result["resolved"]:
                    append_jsonl(RESULTS_PATH, result)
                    resolved += 1
                else:
                    append_jsonl(UNRESOLVED_PATH, result)
                    unresolved += 1

                candidate_links_found += int(result.get("candidate_link_count", 0) or 0)

                print(
                    f"[{idx}/{len(batch)}] {result['status']} "
                    f"{result.get('canonical_host','')} -> "
                    f"{result.get('final_url','')} "
                    f"candidate_links={result.get('candidate_link_count',0)} "
                    f"remote_hits={len(result.get('remote_term_hits',[]))}"
                )
                sys.stdout.flush()

                if DELAY_SECONDS > 0:
                    time.sleep(DELAY_SECONDS)

            context.close()
            browser.close()
    finally:
        client.close()

    print(json.dumps({
        "status": "ok",
        "processed_this_run": len(batch),
        "resolved_this_run": resolved,
        "unresolved_this_run": unresolved,
        "candidate_links_found": candidate_links_found,
        "results_output": str(RESULTS_PATH),
        "unresolved_output": str(UNRESOLVED_PATH),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
