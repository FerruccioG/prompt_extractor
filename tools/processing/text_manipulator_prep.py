#!/usr/bin/env python3
"""
text_manipulator_prep.py

Prepares grouped OCR records for the future Text_Manipulator_AI_Agent.

Input:
- data/instagram/ocr_grouped.jsonl

Output:
- data/instagram/text_manipulator_input.jsonl

Behavior:
- keeps only availability_status == "available"
- preserves grouped OCR text
- keeps enough metadata for traceability
- does no cleaning, no interpretation, no AI work
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_PATH = Path(__file__).resolve().parent.parent
INPUT_JSONL = BASE_PATH / "data/instagram/ocr_grouped.jsonl"
OUTPUT_JSONL = BASE_PATH / "data/instagram/text_manipulator_input.jsonl"


def ensure_output_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_start_marker(path: Path) -> None:
    append_jsonl(
        path,
        {
            "event": "text_manipulator_prep_started",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


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


def normalize_slides(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []
    for slide in value:
        if not isinstance(slide, dict):
            continue
        normalized.append(
            {
                "slide_index": slide.get("slide_index"),
                "slide_filename": slide.get("slide_filename"),
                "source_image": slide.get("source_image"),
                "ocr_text_raw": slide.get("ocr_text_raw", ""),
            }
        )
    return normalized


def normalize_source_images(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    output: list[str] = []
    seen: set[str] = set()

    for item in value:
        if item is None:
            continue
        s = str(item)
        if not s:
            continue
        if s not in seen:
            seen.add(s)
            output.append(s)

    return output


def main() -> int:
    ensure_output_file(OUTPUT_JSONL)
    write_start_marker(OUTPUT_JSONL)

    if not INPUT_JSONL.exists():
        print(f"Input file not found: {INPUT_JSONL}")
        return 1

    rows = load_jsonl(INPUT_JSONL)
    output_rows: list[dict[str, Any]] = []

    skipped_non_data_rows = 0
    skipped_unavailable_rows = 0
    skipped_missing_post_id_rows = 0

    for row in rows:
        # Skip diagnostic/bootstrap/event rows
        if row.get("event"):
            skipped_non_data_rows += 1
            continue

        post_id = row.get("post_id")
        if not post_id:
            skipped_missing_post_id_rows += 1
            continue

        if row.get("availability_status") != "available":
            skipped_unavailable_rows += 1
            continue

        slides = normalize_slides(row.get("slides", []))
        source_images = normalize_source_images(row.get("source_images", []))

        output_row = {
            "post_id": post_id,
            "content_type": row.get("content_type"),
            "slide_total": row.get("slide_total"),
            "slide_count_found": row.get("slide_count_found"),
            "all_ocr_status_ok": row.get("all_ocr_status_ok"),
            "source_images": source_images,
            "slides": slides,
            "ocr_text_combined_raw": row.get("ocr_text_combined_raw", ""),
            "availability_status": row.get("availability_status"),
            "prep_status": "ready_for_text_manipulator_agent",
            "prepared_at": datetime.now(UTC).isoformat(),
        }

        output_rows.append(output_row)

    # Overwrite the startup marker with final prepared rows only
    write_jsonl(OUTPUT_JSONL, output_rows)

    print(
        json.dumps(
            {
                "status": "ok",
                "input_rows": len(rows),
                "prepared_rows": len(output_rows),
                "skipped_non_data_rows": skipped_non_data_rows,
                "skipped_unavailable_rows": skipped_unavailable_rows,
                "skipped_missing_post_id_rows": skipped_missing_post_id_rows,
                "output_jsonl": str(OUTPUT_JSONL),
            },
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
