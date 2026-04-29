#!/usr/bin/env python3
"""
Text Manipulator AI Agent - lightweight handoff packager
--------------------------------------------------------
Consumes:
    data/instagram/text_manipulator_input.jsonl

Produces:
    data/instagram/text_manipulator_output.jsonl

Purpose:
    Prepare full Instagram carousel/post OCR context for the real AI Agent.
    This script does NOT perform semantic extraction, prompt detection,
    classification, cleaning, or confidence scoring.

Design notes:
    - One input post/carousel = one output row
    - Preserve all slide text and ordering
    - Preserve source metadata
    - Add only operational handoff metadata
    - Leave all semantic decisions to the AI Agent
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "instagram"
INPUT_JSONL = DATA_DIR / "text_manipulator_input.jsonl"
OUTPUT_JSONL = DATA_DIR / "text_manipulator_output.jsonl"


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

SOCIAL_MEDIA_DEFAULT = "instagram"
SOURCE_SYSTEM = "text_manipulator_agent_handoff_v2"
HANDOFF_STATUS = "ready_for_ai_semantic_extraction"


# ---------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    yield obj
            except json.JSONDecodeError:
                print(
                    json.dumps(
                        {
                            "status": "warning",
                            "stage": "read_input",
                            "line_num": line_num,
                            "message": "Skipping invalid JSON line",
                        },
                        ensure_ascii=False,
                    )
                )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def normalize_text_for_handoff(text: Any) -> str:
    """
    Preserve OCR text as-is as much as possible, only normalize line endings
    and trim outer whitespace so the AI Agent receives stable input.
    """
    if text is None:
        return ""
    value = str(text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value.strip()


def sanitize_slides(slides: Any) -> list[dict[str, Any]]:
    """
    Keep slide structure intact. No semantic filtering.
    Only ensure consistent field presence and ordering.
    """
    if not isinstance(slides, list):
        return []

    sanitized: list[dict[str, Any]] = []

    for idx, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            continue

        slide_index = slide.get("slide_index")
        if not isinstance(slide_index, int):
            try:
                slide_index = int(slide_index)
            except (TypeError, ValueError):
                slide_index = idx

        sanitized.append(
            {
                "slide_index": slide_index,
                "slide_filename": str(slide.get("slide_filename") or ""),
                "source_image": str(slide.get("source_image") or ""),
                "ocr_text_raw": normalize_text_for_handoff(slide.get("ocr_text_raw")),
            }
        )

    sanitized.sort(key=lambda s: s["slide_index"])
    return sanitized


def build_handoff_record(row: dict[str, Any]) -> dict[str, Any]:
    now_iso = utc_now_iso()

    slides = sanitize_slides(row.get("slides"))
    source_images = row.get("source_images")
    if not isinstance(source_images, list):
        source_images = []

    return {
        "post_id": str(row.get("post_id") or "").strip(),
        "prompt_social_media": SOCIAL_MEDIA_DEFAULT,
        "content_type": str(row.get("content_type") or "unknown"),
        "slide_total": int(row.get("slide_total") or len(slides) or 0),
        "slide_count_found": int(row.get("slide_count_found") or len(slides)),
        "source_images": [str(item) for item in source_images],
        "slides": slides,
        "ocr_text_combined_raw": normalize_text_for_handoff(row.get("ocr_text_combined_raw")),
        "availability_status": str(row.get("availability_status") or "unknown"),
        "source_post_all_ocr_status_ok": bool(row.get("all_ocr_status_ok", False)),
        "source_post_prep_status": row.get("prep_status"),
        "handoff_status": HANDOFF_STATUS,
        "producer": SOURCE_SYSTEM,
        "prepared_at": now_iso,
    }


# ---------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------

def process_input_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    output_rows: list[dict[str, Any]] = []

    stats = {
        "input_rows": 0,
        "posts_skipped_missing_post_id": 0,
        "posts_skipped_unavailable": 0,
        "posts_skipped_invalid_slides": 0,
        "written_handoff_rows": 0,
    }

    for row in rows:
        stats["input_rows"] += 1

        post_id = str(row.get("post_id") or "").strip()
        if not post_id:
            stats["posts_skipped_missing_post_id"] += 1
            continue

        availability_status = str(row.get("availability_status") or "").strip().lower()
        if availability_status == "unavailable":
            stats["posts_skipped_unavailable"] += 1
            continue

        slides = sanitize_slides(row.get("slides"))
        if not slides:
            stats["posts_skipped_invalid_slides"] += 1
            continue

        handoff_row = build_handoff_record(row)
        output_rows.append(handoff_row)

    stats["written_handoff_rows"] = len(output_rows)
    return output_rows, stats


def main() -> None:
    if not INPUT_JSONL.exists():
        print(
            json.dumps(
                {
                    "status": "error",
                    "stage": "startup",
                    "message": f"Input file not found: {INPUT_JSONL}",
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1)

    input_rows = list(iter_jsonl(INPUT_JSONL))
    output_rows, stats = process_input_rows(input_rows)
    written = write_jsonl(OUTPUT_JSONL, output_rows)

    print(
        json.dumps(
            {
                "status": "ok",
                "input_rows": stats["input_rows"],
                "posts_skipped_missing_post_id": stats["posts_skipped_missing_post_id"],
                "posts_skipped_unavailable": stats["posts_skipped_unavailable"],
                "posts_skipped_invalid_slides": stats["posts_skipped_invalid_slides"],
                "written_rows": written,
                "output_jsonl": str(OUTPUT_JSONL),
                "handoff_status": HANDOFF_STATUS,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
