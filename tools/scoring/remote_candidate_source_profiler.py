#!/usr/bin/env python3
"""
remote_candidate_source_profiler.py

Deep evidence profiler for Candidate <-> Remote Source relationships.

Purpose
-------
The generic relationship scorer proves that a source is candidate-facing.
This profiler asks the next, user-specific question:

    "How useful is this source for a senior Ireland/EU-based
     AI/data/platform/BI/database/FDE candidate seeking remote opportunity?"

It crawls only the strong/promising/review relationship universe, not the
entire discovery corpus.

Inputs
------
- data/remote/source_relationship_scored.jsonl
- data/remote/source_validation_results.jsonl

Outputs
-------
- data/remote/candidate_source_profile_results.jsonl
- data/remote/candidate_source_profile_unresolved.jsonl

Behavior
--------
- resumable append-only processing;
- default 10-source pilot;
- set REMOTE_PROFILE_LIMIT=0 for the full eligible set;
- examines the validated landing page plus a small number of candidate-facing
  links from that source;
- extracts evidence for specialization fit, geography, remote strength,
  contract/freelance signals, and candidate cost/access friction;
- gathers evidence only. Final scoring is a later stage.
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
from urllib.parse import urlparse

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]

SCORED_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_SCORED",
    ROOT_DIR / "data/remote/source_relationship_scored.jsonl",
))
VALIDATION_PATH = Path(os.getenv(
    "REMOTE_SOURCE_VALIDATION_RESULTS",
    ROOT_DIR / "data/remote/source_validation_results.jsonl",
))
OUTPUT_PATH = Path(os.getenv(
    "REMOTE_CANDIDATE_SOURCE_PROFILE_RESULTS",
    ROOT_DIR / "data/remote/candidate_source_profile_results.jsonl",
))
UNRESOLVED_PATH = Path(os.getenv(
    "REMOTE_CANDIDATE_SOURCE_PROFILE_UNRESOLVED",
    ROOT_DIR / "data/remote/candidate_source_profile_unresolved.jsonl",
))

DEFAULT_LIMIT = 10
HTTP_TIMEOUT = float(os.getenv("REMOTE_PROFILE_HTTP_TIMEOUT", "20"))
PER_SOURCE_LINK_LIMIT = int(os.getenv("REMOTE_PROFILE_LINK_LIMIT", "6"))
PER_SOURCE_DELAY = float(os.getenv("REMOTE_PROFILE_DELAY_SECONDS", "0.35"))
TEXT_LIMIT = int(os.getenv("REMOTE_PROFILE_TEXT_LIMIT", "6000"))

# Candidate profile signals deliberately reflect the project's target market:
# senior AI/data/platform/FDE/BI/database work.
PROFILE_SIGNAL_GROUPS = {
    "ai_agentic": (
        "artificial intelligence", " ai ", "machine learning", "generative ai",
        "genai", "llm", "large language model", "agentic", "ai engineer",
        "ai architect", "ai platform", "prompt engineering",
    ),
    "forward_deployment_solutions": (
        "forward deployed", "forward deployment", "solutions architect",
        "solution architect", "customer engineer", "field engineer",
        "implementation engineer", "technical consultant",
    ),
    "data_engineering_architecture": (
        "data engineer", "data engineering", "data architect",
        "data platform", "data warehouse", "data warehousing", "etl",
        "analytics engineer", "data integration",
    ),
    "bi_powerbi": (
        "power bi", "business intelligence", "bi developer", "bi consultant",
        "reporting", "semantic model",
    ),
    "database_sql": (
        "sql server", "sql developer", "database engineer",
        "database administrator", "dba", "t-sql", "oracle", "mongodb",
        "nosql", "postgres", "postgresql",
    ),
    "cloud_azure": (
        "azure", "azure data factory", "synapse", "cloud architect",
        "cloud engineer", "aws", "gcp",
    ),
    "financial_services": (
        "financial services", "banking", "investment bank", "capital markets",
        "risk", "fintech", "trading", "pre-trade",
    ),
    "seniority": (
        "senior", "lead", "principal", "staff", "architect", "manager",
        "head of", "director",
    ),
}

REMOTE_TERMS = (
    "remote", "fully remote", "100% remote", "work from home",
    "work from anywhere", "distributed", "home-based", "home based",
)

EU_IRELAND_TERMS = (
    "ireland", "dublin", "europe", "european union", " eu ", "emea",
    "uk & ireland", "ireland & uk", "europe & uk",
)

GLOBAL_TERMS = (
    "worldwide", "global", "anywhere", "international", "across the world",
)

CONTRACT_TERMS = (
    "contract", "contractor", "freelance", "consulting", "consultant",
    "day rate", "daily rate", "interim",
)

PERMANENT_TERMS = (
    "permanent", "full-time", "full time", "employee", "employment",
)

COST_FRICTION_TERMS = (
    "subscription", "membership", "premium", "paid plan", "pricing",
    "upgrade", "monthly", "annual plan",
)

FREE_ACCESS_TERMS = (
    "free", "no fee", "free to apply", "free account",
)

AUTH_FRICTION_TERMS = (
    "sign in", "log in", "login", "create account", "register",
)

JOBISH_LINK_TERMS = (
    "job", "career", "vacanc", "opening", "apply", "remote", "talent",
    "contract", "freelance", "position",
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


def strip_html(markup: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", markup or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_text(client: httpx.Client, url: str) -> dict[str, Any]:
    try:
        response = client.get(url)
        ctype = response.headers.get("content-type", "")
        text = ""
        title = ""
        if "html" in ctype.lower():
            markup = response.text
            text = strip_html(markup)[:TEXT_LIMIT]
            m = re.search(r"(?is)<title[^>]*>(.*?)</title>", markup)
            if m:
                title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()
        return {
            "ok": True,
            "status": response.status_code,
            "url": str(response.url),
            "title": title,
            "text": text,
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": "",
            "title": "",
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def hit_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    lower = f" {text.lower()} "
    found: list[str] = []
    for term in terms:
        if term in lower and term not in found:
            found.append(term)
    return found


def profile_hits(text: str) -> dict[str, list[str]]:
    return {
        group: hit_terms(text, terms)
        for group, terms in PROFILE_SIGNAL_GROUPS.items()
    }


def validation_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(VALIDATION_PATH):
        host = str(row.get("canonical_host", "")).lower()
        if host:
            index[host] = row
    return index


def eligible_rows() -> list[dict[str, Any]]:
    return [
        row for row in load_jsonl(SCORED_PATH)
        if row.get("Relationship Class") in {"strong", "promising", "review"}
    ]


def processed_sources() -> set[str]:
    done: set[str] = set()
    for path in (OUTPUT_PATH, UNRESOLVED_PATH):
        for row in load_jsonl(path):
            source = str(row.get("source", "")).lower()
            if source:
                done.add(source)
    return done


def choose_links(source_row: dict[str, Any], validation: dict[str, Any] | None) -> list[str]:
    validation = validation or {}
    links = validation.get("candidate_links", []) or []
    source_host = str(source_row.get("Source", "")).lower()

    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for item in links:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        anchor = str(item.get("text", "")).strip()
        if not url.startswith(("http://", "https://")):
            continue
        key = url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)

        haystack = f"{key} {anchor}".lower()
        score = sum(1 for term in JOBISH_LINK_TERMS if term in haystack)
        if host_of(key) == source_host:
            score += 2
        if "remote" in haystack:
            score += 2
        scored.append((score, key))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [url for _, url in scored[:PER_SOURCE_LINK_LIMIT]]


def aggregate_evidence(pages: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(
        f"{p.get('title','')} {p.get('text','')}"
        for p in pages
        if p.get("ok")
    )

    p_hits = profile_hits(text)

    return {
        "profile_signal_hits": p_hits,
        "profile_signal_group_count": sum(1 for vals in p_hits.values() if vals),
        "profile_signal_total_hits": sum(len(vals) for vals in p_hits.values()),
        "remote_hits": hit_terms(text, REMOTE_TERMS),
        "eu_ireland_hits": hit_terms(text, EU_IRELAND_TERMS),
        "global_hits": hit_terms(text, GLOBAL_TERMS),
        "contract_hits": hit_terms(text, CONTRACT_TERMS),
        "permanent_hits": hit_terms(text, PERMANENT_TERMS),
        "cost_friction_hits": hit_terms(text, COST_FRICTION_TERMS),
        "free_access_hits": hit_terms(text, FREE_ACCESS_TERMS),
        "auth_friction_hits": hit_terms(text, AUTH_FRICTION_TERMS),
    }


def main() -> int:
    rows = eligible_rows()
    validation = validation_index()
    done = processed_sources()
    pending = [r for r in rows if str(r.get("Source", "")).lower() not in done]

    limit = max(0, int(os.getenv("REMOTE_PROFILE_LIMIT", str(DEFAULT_LIMIT))))
    batch = pending if limit == 0 else pending[:limit]

    print(json.dumps({
        "status": "starting",
        "eligible_relationships": len(rows),
        "already_processed": len(done),
        "pending_relationships": len(pending),
        "run_limit": limit,
        "this_run_relationships": len(batch),
    }, ensure_ascii=False))
    sys.stdout.flush()

    if not batch:
        print(json.dumps({"status": "ok", "message": "No pending relationships."}))
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
    pages_fetched = 0

    try:
        for idx, row in enumerate(batch, start=1):
            source = str(row.get("Source", "")).lower()
            source_validation = validation.get(source)

            seed_url = str(row.get("Final URL", "") or row.get("URL", "")).strip()
            urls = [seed_url] if seed_url else []
            for url in choose_links(row, source_validation):
                if url not in urls:
                    urls.append(url)

            pages: list[dict[str, Any]] = []
            for url in urls[: 1 + PER_SOURCE_LINK_LIMIT]:
                result = fetch_text(client, url)
                pages.append(result)
                pages_fetched += 1

            good_pages = [p for p in pages if p.get("ok") and p.get("text")]
            if good_pages:
                evidence = aggregate_evidence(good_pages)
                output = {
                    "source": source,
                    "relationship_score": row.get("Relationship Score"),
                    "relationship_class": row.get("Relationship Class"),
                    "verification_status": row.get("Verification Status"),
                    "pages_attempted": len(pages),
                    "pages_resolved": len(good_pages),
                    "page_urls": [p.get("url") for p in pages if p.get("url")],
                    **evidence,
                    "sample_titles": [p.get("title") for p in good_pages if p.get("title")][:8],
                    "profiled_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status": "profiled",
                }
                append_jsonl(OUTPUT_PATH, output)
                resolved += 1
                print(
                    f"[{idx}/{len(batch)}] profiled {source} "
                    f"pages={len(good_pages)} "
                    f"profile_groups={output['profile_signal_group_count']} "
                    f"remote={len(output['remote_hits'])} "
                    f"eu_ie={len(output['eu_ireland_hits'])} "
                    f"contract={len(output['contract_hits'])}"
                )
            else:
                output = {
                    "source": source,
                    "relationship_score": row.get("Relationship Score"),
                    "relationship_class": row.get("Relationship Class"),
                    "pages_attempted": len(pages),
                    "errors": [p.get("error") for p in pages if p.get("error")],
                    "profiled_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status": "unresolved",
                }
                append_jsonl(UNRESOLVED_PATH, output)
                unresolved += 1
                print(f"[{idx}/{len(batch)}] unresolved {source} pages={len(pages)}")

            sys.stdout.flush()
            if PER_SOURCE_DELAY > 0:
                time.sleep(PER_SOURCE_DELAY)

    finally:
        client.close()

    print(json.dumps({
        "status": "ok",
        "processed_this_run": len(batch),
        "profiled_this_run": resolved,
        "unresolved_this_run": unresolved,
        "pages_fetched_this_run": pages_fetched,
        "results_output": str(OUTPUT_PATH),
        "unresolved_output": str(UNRESOLVED_PATH),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
