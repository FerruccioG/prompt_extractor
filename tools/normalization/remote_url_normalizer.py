#!/usr/bin/env python3
"""
remote_url_normalizer.py

Conservative URL normalization for the Remote Opportunities pipeline.

Unlike the original social-media normalizer, this module is designed for
arbitrary web/job/company URLs. It intentionally preserves unknown query
parameters because many job boards encode job IDs, searches, locations and
application state in the query string.

Input:
- data/url_queue.jsonl

Output:
- data/remote/url_queue_normalized.jsonl
"""

from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "fbclid", "gclid", "msclkid", "igshid", "igsh",
    "mc_cid", "mc_eid", "vero_id", "vero_conv", "_hsenc", "_hsmi",
    "mkt_tok", "trk", "trkinfo", "lipi", "li_fat_id", "ref_src",
}

WRAPPER_PARAMS = {
    "url", "u", "target", "dest", "destination", "redirect",
    "redirect_url", "redirect_uri", "r", "to", "link", "href",
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


def unwrap_once(url: str) -> tuple[str, bool]:
    parsed = urlparse(url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() not in WRAPPER_PARAMS:
            continue
        candidate = html.unescape(unquote(value)).strip()
        if candidate.startswith("https://") or candidate.startswith("http://"):
            return candidate, True
    return url, False


def canonicalize(url: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    current = html.unescape(url.strip())

    for _ in range(3):
        current, changed = unwrap_once(current)
        if not changed:
            break
        notes.append("wrapped_target_extracted")

    parsed = urlparse(current)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()

    if not host:
        return "", notes + ["invalid_missing_host"]

    port = parsed.port
    netloc = host
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    elif port:
        notes.append("drop_default_port")

    path = parsed.path or "/"
    while "//" in path:
        path = path.replace("//", "/")

    kept_query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in TRACKING_PARAMS:
            notes.append(f"drop_tracking_param:{key}")
            continue
        kept_query.append((key, value))

    query = urlencode(kept_query, doseq=True)

    if parsed.fragment:
        notes.append("drop_fragment")

    rebuilt = urlunparse((scheme, netloc, path, "", query, ""))
    return rebuilt, list(dict.fromkeys(notes))


def main() -> int:
    input_path = Path(os.getenv("INPUT_JSONL", "data/url_queue.jsonl")).resolve()
    output_path = Path(
        os.getenv("OUTPUT_JSONL", "data/remote/url_queue_normalized.jsonl")
    ).resolve()

    if not input_path.exists():
        print(f"Fatal error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        rows = load_jsonl(input_path)
        output_rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        for row in rows:
            original = str(row.get("url", "")).strip()
            if not original:
                continue

            normalized, notes = canonicalize(original)
            if not normalized or normalized in seen:
                continue

            seen.add(normalized)
            output_rows.append(
                {
                    "original_url": original,
                    "normalized_url": normalized,
                    "source_message_id": row.get("source_message_id"),
                    "source_subject": row.get("source_subject"),
                    "source_from": row.get("source_from"),
                    "email_datetime": row.get("email_datetime"),
                    "gmail_query": row.get("gmail_query"),
                    "landing_type": row.get("landing_type"),
                    "platform": row.get("platform"),
                    "status": "pending",
                    "normalization_notes": notes,
                }
            )

        write_jsonl(output_path, output_rows)
        print(json.dumps({
            "status": "ok",
            "input_rows": len(rows),
            "normalized_unique_rows": len(output_rows),
            "output_jsonl": str(output_path),
        }, ensure_ascii=False))
        return 0

    except Exception as exc:
        print(f"Fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
