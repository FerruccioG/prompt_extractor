#!/usr/bin/env python3
"""
MongoDB Loader for Prompt Extractor

Consumes:
    data/instagram/text_manipulator_output.jsonl

Performs:
    MongoDB upserts into:
    prompt_extractor.prompt_library

Behavior:
- One JSON document per line
- Upsert by prompt_unique_id
- Sets created_at on first insert
- Always updates updated_at on each run
- Keeps pipeline idempotent across re-runs
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_JSONL = BASE_DIR / "data" / "instagram" / "text_manipulator_output.jsonl"

MONGO_URI = os.getenv("PROMPT_EXTRACTOR_MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DB_NAME = os.getenv("PROMPT_EXTRACTOR_MONGO_DB", "prompt_extractor")
MONGO_COLLECTION_NAME = os.getenv("PROMPT_EXTRACTOR_MONGO_COLLECTION", "prompt_library")


MANDATORY_FIELDS = {
    "prompt_unique_id",
    "prompt_text_original",
    "prompt_text_clean",
    "prompt_type",
    "target_engine",
    "output_modality",
    "prompt_size_chars",
    "record_status",
}

OPTIONAL_TOP_LEVEL_DEFAULTS = {
    "prompt_title": None,
    "carousel_title": None,
    "prompt_date": None,
    "prompt_social_media": None,
    "prompt_carrousel_id": None,
    "prompt_carrousel_page_num": None,
    "model": "all_models",
    "comments": None,
    "extraction_confidence": None,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_collection() -> Collection:
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB_NAME]
    return db[MONGO_COLLECTION_NAME]


def ensure_indexes(collection: Collection) -> None:
    collection.create_index([("prompt_unique_id", 1)], unique=True)
    collection.create_index("prompt_type")
    collection.create_index("target_engine")
    collection.create_index("output_modality")
    collection.create_index("record_status")
    collection.create_index("extraction_confidence")


def normalize_document(raw_doc: dict[str, Any], line_number: int) -> dict[str, Any]:
    if not isinstance(raw_doc, dict):
        raise ValueError(f"Line {line_number}: JSON row is not an object")

    missing = [field for field in MANDATORY_FIELDS if field not in raw_doc or raw_doc[field] in (None, "")]
    if missing:
        raise ValueError(f"Line {line_number}: missing mandatory field(s): {', '.join(sorted(missing))}")

    doc = dict(raw_doc)

    for key, default_value in OPTIONAL_TOP_LEVEL_DEFAULTS.items():
        doc.setdefault(key, default_value)

    if not isinstance(doc["prompt_size_chars"], int):
        try:
            doc["prompt_size_chars"] = int(doc["prompt_size_chars"])
        except Exception as exc:
            raise ValueError(
                f"Line {line_number}: prompt_size_chars must be integer-compatible"
            ) from exc

    if doc["model"] in (None, "", []):
        doc["model"] = "all_models"

    now_iso = utc_now_iso()

    # Always refresh updated_at on every run
    doc["updated_at"] = now_iso

    # created_at will be set only on first insert using $setOnInsert
    # so remove it from the normal update payload if present
    doc.pop("created_at", None)

    return doc


def upsert_document(collection: Collection, doc: dict[str, Any]) -> tuple[bool, bool]:
    prompt_unique_id = doc["prompt_unique_id"]
    now_iso = doc["updated_at"]

    update_payload = {"$set": doc, "$setOnInsert": {"created_at": now_iso}}

    result = collection.update_one(
        {"prompt_unique_id": prompt_unique_id},
        update_payload,
        upsert=True,
    )

    inserted = result.upserted_id is not None
    updated = not inserted and result.matched_count > 0
    return inserted, updated


def load_jsonl(input_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with input_path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Line {line_number}: invalid JSON") from exc

            normalized = normalize_document(obj, line_number)
            rows.append(normalized)

    return rows


def main() -> int:
    input_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_INPUT_JSONL.resolve()

    try:
        collection = get_collection()
        ensure_indexes(collection)
        documents = load_jsonl(input_path)
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "stage": "startup",
            "message": str(exc),
        }, ensure_ascii=False))
        return 1

    inserted_count = 0
    updated_count = 0
    failed_count = 0
    failures: list[dict[str, Any]] = []

    for idx, doc in enumerate(documents, start=1):
        try:
            inserted, updated = upsert_document(collection, doc)
            if inserted:
                inserted_count += 1
            elif updated:
                updated_count += 1
        except PyMongoError as exc:
            failed_count += 1
            failures.append({
                "row_number": idx,
                "prompt_unique_id": doc.get("prompt_unique_id"),
                "error": str(exc),
            })

    status = "ok" if failed_count == 0 else "partial"

    print(json.dumps({
        "status": status,
        "input_jsonl": str(input_path),
        "mongo_uri": MONGO_URI,
        "database": MONGO_DB_NAME,
        "collection": MONGO_COLLECTION_NAME,
        "total_rows_read": len(documents),
        "inserted": inserted_count,
        "updated": updated_count,
        "failed": failed_count,
        "failures": failures[:20],  # keep output tight
    }, ensure_ascii=False, indent=2))

    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
