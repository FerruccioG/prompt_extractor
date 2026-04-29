#!/usr/bin/env python3
"""
url_normalizer.py

Read a raw URL queue JSONL file, normalize social-media URLs into a cleaner,
scraping-ready queue, preserve provenance, and deduplicate globally by
normalized_url.

Input:
- data/url_queue.jsonl

Output:
- data/url_queue_normalized.jsonl

Optional environment variables:
- INPUT_JSONL   (default: data/url_queue.jsonl)
- OUTPUT_JSONL  (default: data/url_queue_normalized.jsonl)
"""

from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse


SOCIAL_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "fb.watch",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "linkedin.com",
    "www.linkedin.com",
    "pinterest.com",
    "www.pinterest.com",
}

# Common params that often contain a wrapped destination URL
WRAPPED_URL_PARAM_NAMES = [
    "url",
    "u",
    "target",
    "dest",
    "destination",
    "redirect",
    "redirect_url",
    "redirect_uri",
    "r",
    "to",
    "link",
    "href",
]

# Tracking params to remove
DROP_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_name",
    "utm_id",
    "utm_reader",
    "utm_viz_id",
    "utm_pubreferrer",
    "utm_swu",
    "fbclid",
    "gclid",
    "igsh",
    "si",
    "feature",
    "mibextid",
    "mc_cid",
    "mc_eid",
    "trk",
    "trkinfo",
    "midtoken",
    "lipi",
    "li_fat_id",
    "guccounter",
    "guce_referrer",
    "guce_referrer_sig",
    "ncid",
    "ref_src",
    "ref_url",
    "s",
    "t",
}

# Query params worth keeping on some platforms
KEEP_QUERY_BY_HOST: dict[str, set[str]] = {
    "youtube.com": {"v", "list"},
    "www.youtube.com": {"v", "list"},
    "m.youtube.com": {"v", "list"},
    "youtu.be": set(),
    "facebook.com": set(),
    "www.facebook.com": set(),
    "m.facebook.com": set(),
    "instagram.com": set(),
    "www.instagram.com": set(),
    "m.instagram.com": set(),
    "twitter.com": set(),
    "www.twitter.com": set(),
    "x.com": set(),
    "www.x.com": set(),
    "linkedin.com": set(),
    "www.linkedin.com": set(),
    "tiktok.com": set(),
    "www.tiktok.com": set(),
    "m.tiktok.com": set(),
    "pinterest.com": set(),
    "www.pinterest.com": set(),
    "fb.watch": set(),
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSONL at {path}:{i}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_social_host(host: str) -> bool:
    return host.lower() in SOCIAL_HOSTS


def host_of(url: str) -> str:
    return (urlparse(url).netloc or "").lower()


def decode_html_entities_once(url: str) -> tuple[str, list[str]]:
    decoded = html.unescape(url)
    notes: list[str] = []
    if decoded != url:
        notes.append("html_entity_decoded")
    return decoded, notes


def try_extract_wrapped_target(url: str) -> tuple[str, list[str]]:
    """
    If a known query parameter contains a full URL to a social destination,
    extract it. Repeat a few times because wrappers can be nested.
    """
    current = url
    notes: list[str] = []

    for _ in range(3):
        parsed = urlparse(current)
        qs = parse_qs(parsed.query, keep_blank_values=True)

        found_target = None
        for key in WRAPPED_URL_PARAM_NAMES:
            if key not in qs:
                continue
            for candidate in qs[key]:
                candidate = html.unescape(candidate)
                candidate = unquote(candidate)
                if candidate.startswith("http://") or candidate.startswith("https://"):
                    if is_social_host(host_of(candidate)):
                        found_target = candidate
                        break
            if found_target:
                break

        if found_target and found_target != current:
            current = found_target
            notes.append("wrapped_target_extracted")
        else:
            break

    return current, notes


def normalize_path(host: str, path: str) -> str:
    """
    Light canonicalization only. Do not over-normalize path semantics.
    """
    path = re_slash(path)

    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        # Keep /p/<id>/, /reel/<id>/, /reels/<id>/ as-is
        return ensure_trailing_slash(path)

    if host in {"twitter.com", "www.twitter.com", "x.com", "www.x.com"}:
        return strip_trailing_slash_unless_root(path)

    if host in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return strip_trailing_slash_unless_root(path)

    if host in {"linkedin.com", "www.linkedin.com"}:
        return strip_trailing_slash_unless_root(path)

    if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        return strip_trailing_slash_unless_root(path)

    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return strip_trailing_slash_unless_root(path)

    if host == "youtu.be":
        return strip_trailing_slash_unless_root(path)

    return strip_trailing_slash_unless_root(path)


def re_slash(path: str) -> str:
    while "//" in path:
        path = path.replace("//", "/")
    return path or "/"


def ensure_trailing_slash(path: str) -> str:
    if path == "/":
        return path
    return path if path.endswith("/") else path + "/"


def strip_trailing_slash_unless_root(path: str) -> str:
    if path == "/":
        return path
    return path.rstrip("/")


def normalize_query(host: str, query: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    qs = parse_qs(query, keep_blank_values=True)
    if not qs:
        return "", notes

    keep_only = KEEP_QUERY_BY_HOST.get(host, set())
    new_qs: dict[str, list[str]] = {}

    for key, values in qs.items():
        lower_key = key.lower()

        if lower_key in DROP_QUERY_PARAMS:
            notes.append(f"drop_param:{key}")
            continue

        if keep_only:
            if key in keep_only:
                new_qs[key] = values
            else:
                notes.append(f"drop_param:{key}")
        else:
            # For hosts where we keep none, drop all params
            notes.append(f"drop_param:{key}")

    if not new_qs:
        return "", notes

    normalized = urlencode(new_qs, doseq=True)
    if normalized != query:
        notes.append("query_normalized")
    return normalized, notes


def canonicalize_url(url: str) -> tuple[str, list[str]]:
    notes: list[str] = []

    # 1) HTML entity decode
    current, decode_notes = decode_html_entities_once(url)
    notes.extend(decode_notes)

    # 2) Unwrap wrappers if they hide a real target
    current, unwrap_notes = try_extract_wrapped_target(current)
    notes.extend(unwrap_notes)

    parsed = urlparse(current)

    # 3) Basic scheme/host normalization
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower()

    # Remove default ports if present
    if host.endswith(":443") and scheme == "https":
        host = host[:-4]
        notes.append("drop_default_port")
    elif host.endswith(":80") and scheme == "http":
        host = host[:-3]
        notes.append("drop_default_port")

    # 4) Normalize query and path
    path = normalize_path(host, parsed.path)
    query, query_notes = normalize_query(host, parsed.query)
    notes.extend(query_notes)

    # 5) Always remove fragments
    fragment = ""
    if parsed.fragment:
        notes.append("drop_fragment")

    # 6) Force https for known social hosts
    if is_social_host(host) and scheme != "https":
        scheme = "https"
        notes.append("force_https")

    rebuilt = urlunparse((scheme, host, path, "", query, fragment))
    return rebuilt, dedupe_notes(notes)


def dedupe_notes(notes: list[str]) -> list[str]:
    seen = set()
    result = []
    for note in notes:
        if note not in seen:
            seen.add(note)
            result.append(note)
    return result


def main() -> int:
    input_path = Path(os.getenv("INPUT_JSONL", "data/url_queue.jsonl")).resolve()
    output_path = Path(os.getenv("OUTPUT_JSONL", "data/url_queue_normalized.jsonl")).resolve()

    if not input_path.exists():
        print(f"Fatal error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        rows = load_jsonl(input_path)

        normalized_rows: list[dict[str, Any]] = []
        seen_normalized_urls: set[str] = set()

        for row in rows:
            original_url = str(row.get("url", "")).strip()
            if not original_url:
                continue

            normalized_url, notes = canonicalize_url(original_url)

            if not normalized_url:
                continue

            if normalized_url in seen_normalized_urls:
                continue

            seen_normalized_urls.add(normalized_url)

            normalized_rows.append(
                {
                    "original_url": original_url,
                    "normalized_url": normalized_url,
                    "source_message_id": row.get("source_message_id"),
                    "source_subject": row.get("source_subject"),
                    "email_datetime": row.get("email_datetime"),
                    "gmail_query": row.get("gmail_query"),
                    "status": "pending",
                    "normalization_notes": notes,
                }
            )

        write_jsonl(output_path, normalized_rows)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "input_rows": len(rows),
                    "normalized_unique_rows": len(normalized_rows),
                    "output_jsonl": str(output_path),
                },
                ensure_ascii=False,
            )
        )
        return 0

    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
