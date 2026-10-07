#!/usr/bin/env python3
"""Targeted verification for Recall Expansion P1 sources with evidence gaps.

Purpose
-------
The generic root-page validator under-extracted remote/geographic evidence for
some strategic sources. This script visits source-specific candidate-facing
pages (jobs, talent, remote, Europe/Ireland where practical) and records compact
evidence for a second-pass profile refresh.

Input:
- data/remote/recall_p1_targeted_verification.jsonl

Outputs:
- data/remote/recall_p1_targeted_verification_results.jsonl
- data/remote/recall_p1_targeted_verification_unresolved.jsonl
- data/remote/recall_p1_targeted_verification_summary.json

No Golden workbook or baseline files are modified.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_TARGETED_INPUT",
    ROOT_DIR / "data/remote/recall_p1_targeted_verification.jsonl",
))
RESULTS_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_TARGETED_RESULTS",
    ROOT_DIR / "data/remote/recall_p1_targeted_verification_results.jsonl",
))
UNRESOLVED_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_TARGETED_UNRESOLVED",
    ROOT_DIR / "data/remote/recall_p1_targeted_verification_unresolved.jsonl",
))
SUMMARY_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_TARGETED_SUMMARY",
    ROOT_DIR / "data/remote/recall_p1_targeted_verification_summary.json",
))

HTTP_TIMEOUT = float(os.getenv("REMOTE_TARGETED_HTTP_TIMEOUT", "20"))
BROWSER_TIMEOUT_MS = int(os.getenv("REMOTE_TARGETED_BROWSER_TIMEOUT_MS", "25000"))
DELAY_SECONDS = float(os.getenv("REMOTE_TARGETED_DELAY_SECONDS", "0.35"))
TEXT_LIMIT = int(os.getenv("REMOTE_TARGETED_TEXT_LIMIT", "7000"))

TARGET_URLS: dict[str, list[str]] = {
    "a.team": [
        "https://www.a.team/",
        "https://www.a.team/talent",
    ],
    "catalant.com": [
        "https://catalant.com/",
        "https://catalant.com/experts/",
    ],
    "linkedin.com": [
        "https://www.linkedin.com/jobs/",
        "https://www.linkedin.com/jobs/remote-jobs/",
        "https://www.linkedin.com/jobs/jobs-in-ireland/",
    ],
    "builtin.com": [
        "https://builtin.com/jobs/remote",
        "https://builtin.com/jobs/remote/data",
        "https://builtin.com/jobs/remote/ai",
    ],
    "dice.com": [
        "https://www.dice.com/",
        "https://www.dice.com/jobs?q=remote",
        "https://www.dice.com/jobs?q=data+engineer&location=Remote",
    ],
    "ycombinator.com": [
        "https://www.ycombinator.com/jobs",
        "https://www.ycombinator.com/jobs/role/software-engineer",
    ],
    "openjobseu.com": [
        "https://www.openjobseu.com/",
        "https://www.openjobseu.com/jobs",
    ],
    "malt.com": [
        "https://www.malt.com/",
        "https://www.malt.com/c/freelancers",
    ],
}

REMOTE_TERMS = (
    "remote", "fully remote", "100% remote", "work from home",
    "work from anywhere", "distributed", "home-based", "home based",
)

EU_IRELAND_TERMS = (
    "ireland", "dublin", "europe", "european union", " eu ", "emea",
    "uk & ireland", "ireland & uk", "europe & uk",
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


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line:
                value = json.loads(line)
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
            "error": "",
            "method": "http",
        }
    except Exception as exc:
        return {
            "requested_url": url,
            "ok": False,
            "status": None,
            "final_url": "",
            "title": "",
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
            "method": "http",
        }


def fetch_browser(page, url: str) -> dict[str, Any]:
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_timeout(1200)
        title = ""
        text = ""
        try:
            title = page.title()
        except Exception:
            pass
        try:
            text = compact(page.locator("body").inner_text())[:TEXT_LIMIT]
        except Exception:
            pass
        return {
            "requested_url": url,
            "ok": True,
            "status": response.status if response else None,
            "final_url": page.url,
            "title": title,
            "text": text,
            "error": "",
            "method": "browser",
        }
    except PlaywrightTimeoutError as exc:
        return {
            "requested_url": url,
            "ok": False,
            "status": None,
            "final_url": page.url if page else "",
            "title": "",
            "text": "",
            "error": f"PlaywrightTimeoutError: {exc}",
            "method": "browser",
        }
    except Exception as exc:
        return {
            "requested_url": url,
            "ok": False,
            "status": None,
            "final_url": page.url if page else "",
            "title": "",
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
            "method": "browser",
        }


def weak(result: dict[str, Any]) -> bool:
    if not result.get("ok"):
        return True
    if result.get("status") in {401, 403, 406, 429, 503}:
        return True
    return len(str(result.get("text", ""))) < 250


def processed_sources() -> set[str]:
    done: set[str] = set()
    for path in (RESULTS_PATH, UNRESOLVED_PATH):
        for row in load_jsonl(path):
            source = str(row.get("source", "")).lower()
            if source:
                done.add(source)
    return done


def main() -> int:
    rows = load_jsonl(INPUT_PATH)
    done = processed_sources()
    pending = [r for r in rows if str(r.get("Source", "")).lower() not in done]

    print(json.dumps({
        "status": "starting",
        "input_sources": len(rows),
        "already_processed": len(done),
        "pending_sources": len(pending),
    }, ensure_ascii=False))

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
    pages_attempted = 0

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

            for idx, row in enumerate(pending, start=1):
                source = str(row.get("Source", "")).lower()
                urls = TARGET_URLS.get(source, [str(row.get("URL", ""))])

                pages: list[dict[str, Any]] = []
                for url in urls:
                    if not url:
                        continue
                    result = fetch_http(client, url)
                    pages_attempted += 1
                    if weak(result):
                        browser_result = fetch_browser(page, url)
                        pages_attempted += 1
                        if browser_result.get("ok") and len(str(browser_result.get("text", ""))) >= len(str(result.get("text", ""))):
                            result = browser_result
                    pages.append(result)

                evidence_text = " ".join(
                    f"{p.get('requested_url','')} {p.get('final_url','')} {p.get('title','')} {p.get('text','')}"
                    for p in pages
                )

                output = {
                    "source": source,
                    "target_urls": urls,
                    "pages": pages,
                    "resolved_pages": sum(1 for p in pages if p.get("ok") and p.get("final_url")),
                    "remote_hits": hits(evidence_text, REMOTE_TERMS),
                    "eu_ireland_hits": hits(evidence_text, EU_IRELAND_TERMS),
                    "profile_hits": hits(evidence_text, PROFILE_TERMS),
                    "contract_hits": hits(evidence_text, CONTRACT_TERMS),
                    "final_hosts": sorted({
                        host_of(str(p.get("final_url", "")))
                        for p in pages if p.get("final_url")
                    }),
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

                if DELAY_SECONDS > 0:
                    time.sleep(DELAY_SECONDS)

            context.close()
            browser.close()
    finally:
        client.close()

    summary = {
        "status": "ok",
        "input_sources": len(rows),
        "processed_this_run": len(pending),
        "resolved_this_run": resolved,
        "unresolved_this_run": unresolved,
        "pages_attempted_this_run": pages_attempted,
        "results_output": str(RESULTS_PATH),
        "unresolved_output": str(UNRESOLVED_PATH),
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
