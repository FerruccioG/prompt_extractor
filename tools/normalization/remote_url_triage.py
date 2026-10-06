#!/usr/bin/env python3
"""
remote_url_triage.py

Conservative triage for Remote Opportunities URLs.

Safety principle: do not silently discard ambiguous links.

Every normalized URL is written to exactly one bucket:
1. ready      - useful enough for browser resolution
2. quarantine - probably low value, but retained for later review/fallback
3. rejected   - only clearly non-navigational/static resources

This keeps recall high while avoiding sending tens of thousands of obvious
assets and boilerplate links into Playwright.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ASSET_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".css", ".js", ".mjs", ".map", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".wav", ".ogg", ".mp4", ".webm", ".mov", ".avi",
}

HIGH_VALUE_TERMS = {
    "job", "jobs", "career", "careers", "vacancy", "vacancies", "position",
    "positions", "opening", "openings", "apply", "application", "remote",
    "hiring", "talent", "recruit", "opportunit", "contract", "freelance",
    "consult", "work-with-us", "join-us", "join-our-team",
}

LOW_VALUE_TERMS = {
    "unsubscribe", "preferences", "email-preferences", "privacy", "cookie",
    "cookies", "terms", "legal", "help", "support", "login", "signin",
    "sign-in", "register", "signup", "sign-up", "password", "account",
    "notification-settings",
}

TRACKING_HOST_TERMS = {
    "click.", "track.", "tracking.", "pixel.", "analytics.", "email.",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def looks_like_asset(path: str) -> bool:
    lower = path.lower().split("?", 1)[0]
    return any(lower.endswith(ext) for ext in ASSET_EXTENSIONS)


def classify(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "/").lower()
    query = (parsed.query or "").lower()
    combined = f"{host} {path} {query}"

    if not host:
        return "rejected", 0, "invalid_missing_host"

    if looks_like_asset(path):
        return "rejected", 0, "static_asset"

    high_hits = sum(1 for term in HIGH_VALUE_TERMS if term in combined)
    low_hits = sum(1 for term in LOW_VALUE_TERMS if term in combined)

    # Social links remain valuable discovery nodes even when the path does
    # not look like a conventional job URL.
    social_hosts = (
        "instagram.com", "tiktok.com", "facebook.com", "fb.watch",
        "linkedin.com", "youtube.com", "youtu.be", "x.com", "twitter.com",
        "pinterest.com",
    )
    if any(host == d or host.endswith("." + d) for d in social_hosts):
        return "ready", 70 + min(high_hits * 5, 20), "social_discovery_node"

    if high_hits:
        return "ready", min(100, 75 + high_hits * 5), "opportunity_signal"

    # Tracking/click hosts may still redirect to the real opportunity, so
    # they are READY, but lower priority rather than discarded.
    if any(term in host for term in TRACKING_HOST_TERMS):
        return "ready", 45, "possible_redirect_wrapper"

    if low_hits >= 1:
        return "quarantine", 20, "probable_boilerplate_or_account_page"

    # Unknown ordinary web pages are intentionally retained and resolved.
    return "ready", 50, "general_web_candidate"


def main() -> int:
    input_path = Path(
        os.getenv("INPUT_JSONL", "data/remote/url_queue_normalized.jsonl")
    ).resolve()
    ready_path = Path(
        os.getenv("OUTPUT_READY_JSONL", "data/remote/url_queue_ready.jsonl")
    ).resolve()
    quarantine_path = Path(
        os.getenv("OUTPUT_QUARANTINE_JSONL", "data/remote/url_queue_quarantine.jsonl")
    ).resolve()
    rejected_path = Path(
        os.getenv("OUTPUT_REJECTED_JSONL", "data/remote/url_queue_rejected.jsonl")
    ).resolve()

    if not input_path.exists():
        print(f"Fatal error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        rows = load_jsonl(input_path)
        ready: list[dict[str, Any]] = []
        quarantine: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for row in rows:
            url = str(row.get("normalized_url", "")).strip()
            bucket, priority, reason = classify(url)

            output = dict(row)
            output["triage_bucket"] = bucket
            output["triage_priority"] = priority
            output["triage_reason"] = reason

            if bucket == "ready":
                ready.append(output)
            elif bucket == "quarantine":
                quarantine.append(output)
            else:
                rejected.append(output)

        ready.sort(key=lambda r: int(r.get("triage_priority", 0)), reverse=True)

        write_jsonl(ready_path, ready)
        write_jsonl(quarantine_path, quarantine)
        write_jsonl(rejected_path, rejected)

        print(json.dumps({
            "status": "ok",
            "input_rows": len(rows),
            "ready_rows": len(ready),
            "quarantine_rows": len(quarantine),
            "rejected_rows": len(rejected),
            "accounted_for_rows": len(ready) + len(quarantine) + len(rejected),
            "ready_output": str(ready_path),
            "quarantine_output": str(quarantine_path),
            "rejected_output": str(rejected_path),
        }, ensure_ascii=False))
        return 0

    except Exception as exc:
        print(f"Fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
