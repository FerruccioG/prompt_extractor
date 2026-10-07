#!/usr/bin/env python3
"""Targeted verification for Recall Expansion P3 hold candidates.

Focuses on P3 sources that scored into A/B/C but missed immediate-promotion
floors because one or two dimensions (typically remote or Ireland/EU evidence)
were weak or likely under-extracted.

Input:
- data/remote/recall_p3_hold.jsonl

Outputs:
- data/remote/recall_p3_targeted_results.jsonl
- data/remote/recall_p3_targeted_unresolved.jsonl
- data/remote/recall_p3_targeted_summary.json
"""

from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = ROOT_DIR / "data/remote/recall_p3_hold.jsonl"
RESULTS_PATH = ROOT_DIR / "data/remote/recall_p3_targeted_results.jsonl"
UNRESOLVED_PATH = ROOT_DIR / "data/remote/recall_p3_targeted_unresolved.jsonl"
SUMMARY_PATH = ROOT_DIR / "data/remote/recall_p3_targeted_summary.json"

TARGET_URLS: dict[str, list[str]] = {
    "infinityquest.co.uk": [
        "https://i-q.co/",
        "https://i-q.co/jobs/",
    ],
    "powertofly.com": [
        "https://powertofly.com/jobs/",
        "https://powertofly.com/jobs/?location=Remote",
    ],
    "robertwalters.com": [
        "https://www.robertwalters.com/jobs.html",
        "https://www.robertwalters.co.uk/jobs.html",
    ],
    "themuse.com": [
        "https://www.themuse.com/search/jobs",
        "https://www.themuse.com/search/jobs/location/remote-flexible",
    ],
    "fiverr.com": [
        "https://www.fiverr.com/",
        "https://www.fiverr.com/categories/programming-tech",
    ],
    "intuition-it.com": [
        "https://www.intuition-it.com/",
        "https://www.intuition-it.com/jobs/",
    ],
    "cloudpeeps.com": [
        "https://www.cloudpeeps.com/",
    ],
    "cv-library.co.uk": [
        "https://www.cv-library.co.uk/",
        "https://www.cv-library.co.uk/remote-jobs",
    ],
}

REMOTE_TERMS = (
    "remote", "fully remote", "100% remote", "work from home",
    "work from anywhere", "distributed", "home-based", "home based",
)
EU_IRELAND_TERMS = (
    "ireland", "dublin", "europe", "european union", " eu ", "emea",
    "uk & ireland", "ireland & uk", "europe & uk", "united kingdom", " uk ",
)
PROFILE_TERMS = (
    "artificial intelligence", "machine learning", "generative ai", "agentic",
    "data engineer", "data engineering", "data architect", "data platform",
    "power bi", "business intelligence", "sql", "mongodb", "database",
    "azure", "cloud architect", "cloud engineer", "financial services",
    "banking", "fintech", "risk", "senior", "lead", "principal", "architect",
)
CONTRACT_TERMS = (
    "contract", "contractor", "freelance", "consulting", "consultant",
    "day rate", "daily rate", "interim",
)

HTTP_TIMEOUT = 20.0
BROWSER_TIMEOUT_MS = 25000
TEXT_LIMIT = 7000


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def strip_html(markup: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", markup or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return compact(html.unescape(text))


def hits(text: str, terms: tuple[str, ...]) -> list[str]:
    lower = text.lower()
    lower = re.sub(r"[_/?:=&.%+\\-]+", " ", lower)
    lower = re.sub(r"\s+", " ", lower)
    lower = f" {lower} "
    return [term for term in terms if term in lower]


def fetch_http(client: httpx.Client, url: str) -> dict[str, Any]:
    try:
        response = client.get(url)
        ctype = response.headers.get("content-type", "")
        markup = response.text if "html" in ctype.lower() else ""
        title = ""
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", markup)
        if m:
            title = compact(html.unescape(m.group(1)))
        return {
            "requested_url": url,
            "ok": True,
            "status": response.status_code,
            "final_url": str(response.url),
            "title": title,
            "text": strip_html(markup)[:TEXT_LIMIT],
            "method": "http",
            "error": "",
        }
    except Exception as exc:
        return {
            "requested_url": url, "ok": False, "status": None,
            "final_url": "", "title": "", "text": "",
            "method": "http", "error": f"{type(exc).__name__}: {exc}",
        }


def fetch_browser(page, url: str) -> dict[str, Any]:
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_timeout(1200)
        try:
            title = page.title()
        except Exception:
            title = ""
        try:
            text = compact(page.locator("body").inner_text())[:TEXT_LIMIT]
        except Exception:
            text = ""
        return {
            "requested_url": url,
            "ok": True,
            "status": response.status if response else None,
            "final_url": page.url,
            "title": title,
            "text": text,
            "method": "browser",
            "error": "",
        }
    except PlaywrightTimeoutError as exc:
        return {
            "requested_url": url, "ok": False, "status": None,
            "final_url": page.url if page else "", "title": "", "text": "",
            "method": "browser", "error": f"PlaywrightTimeoutError: {exc}",
        }
    except Exception as exc:
        return {
            "requested_url": url, "ok": False, "status": None,
            "final_url": page.url if page else "", "title": "", "text": "",
            "method": "browser", "error": f"{type(exc).__name__}: {exc}",
        }


def weak(result: dict[str, Any]) -> bool:
    if not result.get("ok"):
        return True
    if result.get("status") in {401, 403, 406, 429, 503}:
        return True
    return len(str(result.get("text", ""))) < 250


def main() -> int:
    holds = load_jsonl(INPUT_PATH)
    rows = [r for r in holds if str(r.get("Source", "")).lower() in TARGET_URLS]

    done = {
        str(r.get("source", "")).lower()
        for p in (RESULTS_PATH, UNRESOLVED_PATH)
        for r in load_jsonl(p)
        if r.get("source")
    }
    pending = [r for r in rows if str(r.get("Source", "")).lower() not in done]

    print(json.dumps({
        "status": "starting",
        "hold_sources": len(holds),
        "targeted_sources": len(rows),
        "already_processed": len(done),
        "pending_sources": len(pending),
        "sources": [r.get("Source") for r in rows],
    }, ensure_ascii=False))

    client = httpx.Client(
        follow_redirects=True,
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 Chrome/154 Safari/537.36"},
    )

    resolved = unresolved = pages_attempted = 0

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 Chrome/154 Safari/537.36",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()

            for idx, row in enumerate(pending, start=1):
                source = str(row.get("Source", "")).lower()
                pages: list[dict[str, Any]] = []

                for url in TARGET_URLS[source]:
                    result = fetch_http(client, url)
                    pages_attempted += 1
                    if weak(result):
                        b = fetch_browser(page, url)
                        pages_attempted += 1
                        if b.get("ok") and len(str(b.get("text", ""))) >= len(str(result.get("text", ""))):
                            result = b
                    pages.append(result)

                evidence = " ".join(
                    f"{p.get('requested_url','')} {p.get('final_url','')} {p.get('title','')} {p.get('text','')}"
                    for p in pages
                )
                output = {
                    "source": source,
                    "target_urls": TARGET_URLS[source],
                    "pages": pages,
                    "resolved_pages": sum(1 for p in pages if p.get("ok") and p.get("final_url")),
                    "remote_hits": hits(evidence, REMOTE_TERMS),
                    "eu_ireland_hits": hits(evidence, EU_IRELAND_TERMS),
                    "profile_hits": hits(evidence, PROFILE_TERMS),
                    "contract_hits": hits(evidence, CONTRACT_TERMS),
                    "validated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }

                if output["resolved_pages"] > 0:
                    output["status"] = "resolved"
                    append_jsonl(RESULTS_PATH, output)
                    resolved += 1
                else:
                    output["status"] = "unresolved"
                    append_jsonl(UNRESOLVED_PATH, output)
                    unresolved += 1

                print(
                    f"[{idx}/{len(pending)}] {output['status']} {source} "
                    f"pages={output['resolved_pages']} "
                    f"remote={len(output['remote_hits'])} "
                    f"eu_ie={len(output['eu_ireland_hits'])} "
                    f"profile={len(output['profile_hits'])} "
                    f"contract={len(output['contract_hits'])}"
                )

            context.close()
            browser.close()
    finally:
        client.close()

    summary = {
        "status": "ok",
        "hold_sources": len(holds),
        "targeted_sources": len(rows),
        "processed_this_run": len(pending),
        "resolved_this_run": resolved,
        "unresolved_this_run": unresolved,
        "pages_attempted_this_run": pages_attempted,
        "results_output": str(RESULTS_PATH),
        "unresolved_output": str(UNRESOLVED_PATH),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
