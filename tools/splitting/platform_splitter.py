#!/usr/bin/env python3
"""
platform_splitter.py

Read scrape-ready URL queue JSONL and split it into per-platform queues.

Input:
- data/url_queue_scrape_ready.jsonl

Outputs:
- data/queues/instagram.jsonl
- data/queues/linkedin.jsonl
- data/queues/tiktok.jsonl
- data/queues/facebook.jsonl
- data/queues/youtube.jsonl
- data/queues/pinterest.jsonl
- data/queues/unknown.jsonl   (only if needed)

Optional environment variables:
- INPUT_JSONL
- OUTPUT_DIR
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ALLOWED_PLATFORMS = {
    "instagram",
    "linkedin",
    "tiktok",
    "facebook",
    "youtube",
    "pinterest",
    "x",
    "unknown",
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


def main() -> int:
    input_path = Path(os.getenv("INPUT_JSONL", "data/url_queue_scrape_ready.jsonl")).resolve()
    output_dir = Path(os.getenv("OUTPUT_DIR", "data/queues")).resolve()

    if not input_path.exists():
        print(f"Fatal error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        rows = load_jsonl(input_path)
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for row in rows:
            platform = str(row.get("platform", "unknown")).strip().lower() or "unknown"
            if platform not in ALLOWED_PLATFORMS:
                platform = "unknown"
            buckets[platform].append(row)

        summary: dict[str, Any] = {
            "status": "ok",
            "input_rows": len(rows),
            "output_dir": str(output_dir),
            "platform_counts": {},
            "files_written": [],
        }

        output_dir.mkdir(parents=True, exist_ok=True)

        for platform in sorted(buckets.keys()):
            platform_rows = buckets[platform]
            output_path = output_dir / f"{platform}.jsonl"
            write_jsonl(output_path, platform_rows)
            summary["platform_counts"][platform] = len(platform_rows)
            summary["files_written"].append(str(output_path))

        print(json.dumps(summary, ensure_ascii=False))
        return 0

    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
