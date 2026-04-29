#!/usr/bin/env python3
"""
text_group_builder.py

Groups raw OCR rows into post-level records.

Input:
- data/instagram/ocr_raw.jsonl

Output:
- data/instagram/ocr_grouped.jsonl

Behavior:
- groups by post_id
- preserves slide order
- keeps per-slide raw OCR
- concatenates combined raw OCR text
- flags likely unavailable posts
- does no cleaning or interpretation beyond simple availability detection
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_PATH = Path(__file__).resolve().parent.parent
INPUT_JSONL = BASE_PATH / "data/instagram/ocr_raw.jsonl"
OUTPUT_JSONL = BASE_PATH / "data/instagram/ocr_grouped.jsonl"

UNAVAILABLE_PATTERNS = [
    r"post isn.?t available",
    r"profile isn.?t available",
    r"link may he broken",
    r"link may be broken",
    r"profile may have been removed",
    r"log in",
]


def ensure_output_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def write_start_marker(path: Path) -> None:
    append_jsonl(
        path,
        {
            "event": "text_group_builder_started",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON at {path}:{i}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def detect_availability_status(text: str) -> str:
    normalized = text.lower()
    for pattern in UNAVAILABLE_PATTERNS:
        if re.search(pattern, normalized):
            return "unavailable"
    return "available"


def normalize_content_type(values: list[str]) -> str:
    unique = sorted({v for v in values if v})
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return "unknown"
    return "mixed"


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def main() -> int:
    ensure_output_file(OUTPUT_JSONL)
    write_start_marker(OUTPUT_JSONL)

    if not INPUT_JSONL.exists():
        print(f"Input file not found: {INPUT_JSONL}")
        return 1

    rows = load_jsonl(INPUT_JSONL)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    skipped_non_data_rows = 0
    skipped_missing_post_id_rows = 0

    for row in rows:
        # Skip diagnostic/bootstrap/event rows from earlier stages
        if row.get("event"):
            skipped_non_data_rows += 1
            continue

        post_id = row.get("post_id")
        if not post_id:
            skipped_missing_post_id_rows += 1
            continue

        grouped[str(post_id)].append(row)

    output_rows: list[dict[str, Any]] = []

    for post_id in sorted(grouped.keys()):
        items = grouped[post_id]

        items_sorted = sorted(
            items,
            key=lambda x: (
                safe_int(x.get("slide_index"), 999999),
                str(x.get("slide_filename", "")),
            ),
        )

        content_type = normalize_content_type(
            [str(x.get("content_type", "")) for x in items_sorted]
        )

        slide_total_values = [
            safe_int(x.get("slide_total"), 1)
            for x in items_sorted
            if str(x.get("ocr_status", "")) == "ok"
        ]
        slide_total = max(slide_total_values) if slide_total_values else len(items_sorted)

        slides: list[dict[str, Any]] = []
        combined_parts: list[str] = []
        source_images_seen: set[str] = set()
        source_images: list[str] = []
        ocr_statuses: list[str] = []

        for item in items_sorted:
            slide_index = safe_int(item.get("slide_index"), 1)
            item_slide_total = safe_int(item.get("slide_total"), 1)
            slide_filename = str(item.get("slide_filename", ""))
            source_image = str(item.get("source_image", ""))
            ocr_text_raw = str(item.get("ocr_text_raw", ""))
            ocr_status = str(item.get("ocr_status", "unknown"))

            slides.append(
                {
                    "slide_index": slide_index,
                    "slide_filename": slide_filename,
                    "source_image": source_image,
                    "ocr_status": ocr_status,
                    "ocr_text_raw": ocr_text_raw,
                }
            )

            if ocr_text_raw.strip():
                combined_parts.append(
                    f"[SLIDE {slide_index:02d}/{item_slide_total:02d}]\n{ocr_text_raw.strip()}"
                )

            if source_image and source_image not in source_images_seen:
                source_images_seen.add(source_image)
                source_images.append(source_image)

            ocr_statuses.append(ocr_status)

        combined_text = "\n\n".join(combined_parts)
        availability_status = detect_availability_status(combined_text)

        output_row = {
            "post_id": post_id,
            "content_type": content_type,
            "slide_total": slide_total,
            "slide_count_found": len(slides),
            "slides": slides,
            "source_images": source_images,
            "ocr_text_combined_raw": combined_text,
            "availability_status": availability_status,
            "all_ocr_status_ok": all(status == "ok" for status in ocr_statuses) if ocr_statuses else False,
            "grouped_at": datetime.now(UTC).isoformat(),
        }

        output_rows.append(output_row)

    # Overwrite the startup marker with final grouped output only
    write_jsonl(OUTPUT_JSONL, output_rows)

    print(
        json.dumps(
            {
                "status": "ok",
                "input_rows": len(rows),
                "grouped_posts": len(output_rows),
                "skipped_non_data_rows": skipped_non_data_rows,
                "skipped_missing_post_id_rows": skipped_missing_post_id_rows,
                "output_jsonl": str(OUTPUT_JSONL),
            },
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
