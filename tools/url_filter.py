#!/usr/bin/env python3
"""
url_filter.py

Read normalized URL queue JSONL and split it into:
- scrape-ready URLs
- rejected URLs

Input:
- data/url_queue_normalized.jsonl

Outputs:
- data/url_queue_scrape_ready.jsonl
- data/url_queue_rejected.jsonl

Optional environment variables:
- INPUT_JSONL
- OUTPUT_READY_JSONL
- OUTPUT_REJECTED_JSONL
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


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


def detect_platform(host: str) -> str:
    host = host.lower()
    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        return "instagram"
    if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        return "tiktok"
    if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return "x"
    if host in {"facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch"}:
        return "facebook"
    if host in {"linkedin.com", "www.linkedin.com"}:
        return "linkedin"
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return "youtube"
    if host in {"pinterest.com", "www.pinterest.com"}:
        return "pinterest"
    return "unknown"


def looks_like_asset(path: str) -> bool:
    lower = path.lower()
    asset_exts = (
        ".gif", ".jpg", ".jpeg", ".png", ".webp", ".svg", ".ico",
        ".css", ".js", ".woff", ".woff2", ".ttf", ".map", ".xml"
    )
    return lower.endswith(asset_exts)


def path_segments(path: str) -> list[str]:
    return [seg for seg in path.split("/") if seg]


def accept_instagram(path: str) -> tuple[bool, str]:
    segs = path_segments(path)
    if len(segs) >= 2 and segs[0] in {"p", "reel", "reels"}:
        return True, "accepted_instagram_content"
    return False, "rejected_instagram_non_content"


def accept_tiktok(path: str) -> tuple[bool, str]:
    segs = path_segments(path)
    if len(segs) >= 3 and segs[0].startswith("@") and segs[1] == "video":
        return True, "accepted_tiktok_video"
    return False, "rejected_tiktok_non_video"


def accept_x(path: str) -> tuple[bool, str]:
    segs = path_segments(path)
    if len(segs) >= 3 and segs[1] == "status":
        return True, "accepted_x_status"
    return False, "rejected_x_non_status"


def accept_facebook(host: str, path: str) -> tuple[bool, str]:
    segs = path_segments(path)

    if host == "fb.watch":
        return True, "accepted_facebook_watch"

    if "reel" in segs or "watch" in segs:
        return True, "accepted_facebook_reel_or_watch"

    if "posts" in segs:
        return True, "accepted_facebook_post"

    if len(segs) >= 2 and segs[0] not in {"help", "privacy", "settings", "policies"}:
        # permissive fallback for visible facebook content URLs
        return True, "accepted_facebook_generic_content"

    return False, "rejected_facebook_non_content"


def accept_linkedin(path: str) -> tuple[bool, str]:
    segs = path_segments(path)

    # Hard rejects
    if not segs:
        return False, "rejected_linkedin_empty_path"

    if segs[0] in {"comm", "help", "psettings", "emimp", "company", "school", "learning"}:
        return False, "rejected_linkedin_tracking_or_non_content"

    if segs[0] == "posts":
        return True, "accepted_linkedin_post"

    if segs[0] == "pulse":
        return True, "accepted_linkedin_pulse"

    if len(segs) >= 2 and segs[0] == "feed" and segs[1] == "update":
        return True, "accepted_linkedin_feed_update"

    return False, "rejected_linkedin_non_content"


def accept_youtube(host: str, path: str, query: str) -> tuple[bool, str]:
    segs = path_segments(path)
    qs = parse_qs(query)

    if host == "youtu.be" and len(segs) >= 1:
        return True, "accepted_youtube_shortlink"

    if segs and segs[0] == "shorts" and len(segs) >= 2:
        return True, "accepted_youtube_shorts"

    if path.rstrip("/") == "/watch" and "v" in qs:
        return True, "accepted_youtube_watch"

    return False, "rejected_youtube_non_content"


def accept_pinterest(path: str) -> tuple[bool, str]:
    segs = path_segments(path)
    if len(segs) >= 2 and segs[0] == "pin":
        return True, "accepted_pinterest_pin"
    return False, "rejected_pinterest_non_pin"


def classify_url(url: str) -> tuple[bool, str, str]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path or "/"
    query = parsed.query or ""

    platform = detect_platform(host)

    if platform == "unknown":
        return False, "unknown", "rejected_unknown_platform"

    if looks_like_asset(path):
        return False, platform, "rejected_asset_url"

    if platform == "instagram":
        ok, reason = accept_instagram(path)
        return ok, platform, reason

    if platform == "tiktok":
        ok, reason = accept_tiktok(path)
        return ok, platform, reason

    if platform == "x":
        ok, reason = accept_x(path)
        return ok, platform, reason

    if platform == "facebook":
        ok, reason = accept_facebook(host, path)
        return ok, platform, reason

    if platform == "linkedin":
        ok, reason = accept_linkedin(path)
        return ok, platform, reason

    if platform == "youtube":
        ok, reason = accept_youtube(host, path, query)
        return ok, platform, reason

    if platform == "pinterest":
        ok, reason = accept_pinterest(path)
        return ok, platform, reason

    return False, platform, "rejected_unhandled_platform"


def build_output_row(row: dict[str, Any], platform: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "original_url": row.get("original_url"),
        "normalized_url": row.get("normalized_url"),
        "platform": platform,
        "source_message_id": row.get("source_message_id"),
        "source_subject": row.get("source_subject"),
        "email_datetime": row.get("email_datetime"),
        "gmail_query": row.get("gmail_query"),
        "status": status,
        "filter_reason": reason,
        "normalization_notes": row.get("normalization_notes", []),
    }


def main() -> int:
    input_path = Path(os.getenv("INPUT_JSONL", "data/url_queue_normalized.jsonl")).resolve()
    output_ready = Path(os.getenv("OUTPUT_READY_JSONL", "data/url_queue_scrape_ready.jsonl")).resolve()
    output_rejected = Path(os.getenv("OUTPUT_REJECTED_JSONL", "data/url_queue_rejected.jsonl")).resolve()

    if not input_path.exists():
        print(f"Fatal error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        rows = load_jsonl(input_path)

        ready_rows: list[dict[str, Any]] = []
        rejected_rows: list[dict[str, Any]] = []

        for row in rows:
            normalized_url = str(row.get("normalized_url", "")).strip()
            if not normalized_url:
                rejected_rows.append(build_output_row(row, "unknown", "rejected", "rejected_missing_normalized_url"))
                continue

            accepted, platform, reason = classify_url(normalized_url)

            if accepted:
                ready_rows.append(build_output_row(row, platform, "pending", reason))
            else:
                rejected_rows.append(build_output_row(row, platform, "rejected", reason))

        write_jsonl(output_ready, ready_rows)
        write_jsonl(output_rejected, rejected_rows)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "input_rows": len(rows),
                    "scrape_ready_rows": len(ready_rows),
                    "rejected_rows": len(rejected_rows),
                    "output_ready_jsonl": str(output_ready),
                    "output_rejected_jsonl": str(output_rejected),
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
