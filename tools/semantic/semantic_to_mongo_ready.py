#!/usr/bin/env python3
"""
Semantic → Mongo Ready Transformer

Consumes:
    data/instagram/text_manipulator_semantic_output.jsonl

Produces:
    data/instagram/mongo_ready_prompts.jsonl

Purpose:
    Flatten semantic output (post-level with nested prompts)
    into MongoDB-ready prompt documents (one row per prompt).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_JSONL = BASE_DIR / "data" / "instagram" / "text_manipulator_semantic_output.jsonl"
OUTPUT_JSONL = BASE_DIR / "data" / "instagram" / "mongo_ready_prompts.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            yield json.loads(raw)


def build_prompt_unique_id(post_id: str, page_num: int) -> str:
    return f"{post_id}__p{page_num}"


def transform() -> list[dict[str, Any]]:
    output_rows: list[dict[str, Any]] = []

    for post in iter_jsonl(INPUT_JSONL):
        post_id = post.get("post_id")
        content_type = post.get("content_type")
        carousel_title = post.get("carrousel_title")

        prompts = post.get("prompts", [])

        for p in prompts:
            page_num = p["prompt_carrousel_page_num"]

            doc = {
                # --- identity ---
                "prompt_unique_id": build_prompt_unique_id(post_id, page_num),

                # --- core text ---
                "prompt_text_original": p["prompt_text_raw"],
                "prompt_text_clean": p["prompt_text_clean"],

                # --- classification ---
                "prompt_type": p["prompt_type"],
                "target_engine": p["target_engine"],
                "output_modality": p["output_modality"],

                # --- metadata ---
                "prompt_title": p.get("prompt_title"),
                "prompt_date": p.get("prompt_date"),
                "prompt_social_media": "instagram",
                "prompt_carrousel_id": post_id,
                "prompt_carrousel_page_num": page_num,
                "carousel_title": carousel_title,

                # --- quality ---
                "comments": p.get("comments"),
                "extraction_confidence": p.get("extraction_confidence"),

                # --- system fields ---
                "prompt_size_chars": len(p["prompt_text_clean"]),
                "record_status": "active",
                "model": "all_models",

                # timestamps handled by loader
            }

            output_rows.append(doc)

    return output_rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def main() -> None:
    if not INPUT_JSONL.exists():
        raise FileNotFoundError(f"Input not found: {INPUT_JSONL}")

    rows = transform()
    written = write_jsonl(OUTPUT_JSONL, rows)

    print(json.dumps({
        "status": "ok",
        "input_posts": sum(1 for _ in iter_jsonl(INPUT_JSONL)),
        "output_prompts": written,
        "output_file": str(OUTPUT_JSONL),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
