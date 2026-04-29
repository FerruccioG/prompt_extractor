#!/usr/bin/env python3
"""
Semantic Extraction Agent wrapper
---------------------------------
Consumes:
    data/instagram/text_manipulator_output.jsonl

Loads runtime prompts from:
    prompts/semantic_agent_system.txt
    prompts/semantic_agent_user.txt

Produces:
    data/instagram/text_manipulator_semantic_output.jsonl

Purpose:
    Thin orchestration wrapper around the AI semantic extraction stage.
    Python does not decide what is or is not a prompt.
    Python only:
      - reads input rows
      - loads prompt templates
      - sends one full carousel/post at a time to the AI
      - validates returned JSON structure
      - writes output JSONL

Notes:
    - This version is tuned for native Ollama /api/generate
    - It intentionally reduces payload size for safer testing
    - It intentionally defaults to a very small row limit for bring-up
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from openai import OpenAI

import requests


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "instagram"
PROMPTS_DIR = BASE_DIR / "prompts"

INPUT_JSONL = DATA_DIR / "text_manipulator_output.jsonl"
OUTPUT_JSONL = DATA_DIR / "text_manipulator_semantic_output.jsonl"

SYSTEM_PROMPT_FILE = PROMPTS_DIR / "semantic_agent_system.txt"
USER_PROMPT_FILE = PROMPTS_DIR / "semantic_agent_user.txt"


# ---------------------------------------------------------------------
# Runtime config (native Ollama)
# ---------------------------------------------------------------------

API_URL = os.getenv("SEMANTIC_AGENT_API_URL", "http://127.0.0.1:11434/api/generate")
MODEL_NAME = os.getenv("SEMANTIC_AGENT_MODEL", "gemma4:e4b")

TEMPERATURE = float(os.getenv("SEMANTIC_AGENT_TEMPERATURE", "0"))
MAX_TOKENS = int(os.getenv("SEMANTIC_AGENT_MAX_TOKENS", "1200"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("SEMANTIC_AGENT_TIMEOUT_SECONDS", "180"))
MAX_RETRIES = int(os.getenv("SEMANTIC_AGENT_MAX_RETRIES", "2"))
RETRY_SLEEP_SECONDS = int(os.getenv("SEMANTIC_AGENT_RETRY_SLEEP_SECONDS", "3"))

# Safe bring-up default: process only one row unless explicitly overridden
# Use:
#   export SEMANTIC_AGENT_ROW_LIMIT=0   -> process all rows
#   export SEMANTIC_AGENT_ROW_LIMIT=5   -> process first 5 rows
ROW_LIMIT = int(os.getenv("SEMANTIC_AGENT_ROW_LIMIT", "1"))


# ---------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Required prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(
                    json.dumps(
                        {
                            "status": "warning",
                            "stage": "read_input",
                            "line_num": line_num,
                            "message": f"Skipping invalid JSON line: {exc}",
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
                continue

            if isinstance(obj, dict):
                yield obj


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def build_user_prompt(template: str, handoff_row: dict[str, Any]) -> str:
    """
    Reduce payload size while preserving semantic context.
    Keep only the fields the AI actually needs.
    """
    reduced_row = {
        "post_id": handoff_row.get("post_id"),
        "prompt_social_media": handoff_row.get("prompt_social_media"),
        "content_type": handoff_row.get("content_type"),
        "slide_total": handoff_row.get("slide_total"),
        "slide_count_found": handoff_row.get("slide_count_found"),
        "slides": handoff_row.get("slides", []),
        "ocr_text_combined_raw": handoff_row.get("ocr_text_combined_raw"),
        "availability_status": handoff_row.get("availability_status"),
    }
    payload = json.dumps(reduced_row, ensure_ascii=False, indent=2)
    return template.replace("{{HANDOFF_ROW_JSON}}", payload)


def strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text


def extract_json_object(text: str) -> str | None:
    """
    Fallback extractor for models that wrap JSON in extra prose.
    Returns the first outermost JSON object found, or None.
    """
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0).strip()
    return None


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

def validate_prompt_object(prompt: dict[str, Any]) -> None:
    required_keys = {
        "prompt_carrousel_page_num",
        "prompt_title",
        "prompt_date",
        "prompt_text_raw",
        "prompt_text_clean",
        "prompt_type",
        "target_engine",
        "output_modality",
        "comments",
        "extraction_confidence",
        "extraction_confidence_score",
    }

    # --- surgical fallback for small models ---
    prompt.setdefault("extraction_confidence", "medium")
    prompt.setdefault("extraction_confidence_score", 50)
    # ----------------------------------------

    missing = required_keys - set(prompt.keys())
    if missing:
        raise ValueError(f"Prompt object missing keys: {sorted(missing)}")

    if not isinstance(prompt["prompt_carrousel_page_num"], int):
        raise ValueError("prompt_carrousel_page_num must be int")

    if prompt["prompt_title"] is not None and not isinstance(prompt["prompt_title"], str):
        raise ValueError("prompt_title must be string or null")

    if prompt["prompt_date"] is not None and not isinstance(prompt["prompt_date"], str):
        raise ValueError("prompt_date must be string or null")

    if not isinstance(prompt["prompt_text_raw"], str) or not prompt["prompt_text_raw"].strip():
        raise ValueError("prompt_text_raw must be non-empty string")

    if not isinstance(prompt["prompt_text_clean"], str) or not prompt["prompt_text_clean"].strip():
        raise ValueError("prompt_text_clean must be non-empty string")

    if not isinstance(prompt["prompt_type"], str):
        raise ValueError("prompt_type must be string")

    if not isinstance(prompt["target_engine"], list) or not all(isinstance(x, str) for x in prompt["target_engine"]):
        raise ValueError("target_engine must be list[str]")

    if not isinstance(prompt["output_modality"], list) or not all(isinstance(x, str) for x in prompt["output_modality"]):
        raise ValueError("output_modality must be list[str]")

    if not isinstance(prompt["comments"], list) or not all(isinstance(x, str) for x in prompt["comments"]):
        raise ValueError("comments must be list[str]")

    if prompt["extraction_confidence"] not in {"high", "medium", "low"}:
        raise ValueError("extraction_confidence must be high|medium|low")

    if not isinstance(prompt["extraction_confidence_score"], int):
        raise ValueError("extraction_confidence_score must be int")

    if not (0 <= prompt["extraction_confidence_score"] <= 100):
        raise ValueError("extraction_confidence_score must be between 0 and 100")


def validate_semantic_output(obj: dict[str, Any], handoff_row: dict[str, Any]) -> dict[str, Any]:
    required_top_keys = {
        "post_id",
        "prompt_social_media",
        "content_type",
        "carrousel_title",
        "prompt_count_detected",
        "prompts",
    }
    missing = required_top_keys - set(obj.keys())
    if missing:
        raise ValueError(f"Semantic output missing keys: {sorted(missing)}")

    # Surgical fix:
    # enforce source-of-truth identity from pipeline input rather than failing
    obj["post_id"] = handoff_row.get("post_id")
    obj["prompt_social_media"] = "instagram"
    obj["content_type"] = handoff_row.get("content_type")

    if obj["carrousel_title"] is not None and not isinstance(obj["carrousel_title"], str):
        raise ValueError("carrousel_title must be string or null")

    if not isinstance(obj["prompt_count_detected"], int):
        raise ValueError("prompt_count_detected must be int")

    if not isinstance(obj["prompts"], list):
        raise ValueError("prompts must be list")

    obj["prompt_count_detected"] = len(obj["prompts"])

    valid_prompts = []

    for prompt in obj["prompts"]:
        if not isinstance(prompt, dict):
           continue

        raw_text = str(prompt.get("prompt_text_raw") or "").strip()
        clean_text = str(prompt.get("prompt_text_clean") or "").strip()

        if not raw_text or not clean_text:
            continue

        validate_prompt_object(prompt)
        valid_prompts.append(prompt)

    obj["prompts"] = valid_prompts
    obj["prompt_count_detected"] = len(valid_prompts)


    return obj


# ---------------------------------------------------------------------
# AI call (direct OpenAI Chat Completions)
# ---------------------------------------------------------------------


def call_ai(system_prompt: str, user_prompt: str) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME.replace("openai/", ""),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=TEMPERATURE,
                max_completion_tokens=MAX_TOKENS,
           )

            content = response.choices[0].message.content
            if not content:
                raise ValueError("OpenAI returned empty content")

            return content.strip()

        except Exception as exc:
            last_error = exc
            if attempt <= MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)

    raise RuntimeError(f"AI request failed after retries: {last_error}")


# ---------------------------------------------------------------------
# Process one row
# ---------------------------------------------------------------------

def process_row(
    handoff_row: dict[str, Any],
    system_prompt: str,
    user_template: str,
) -> dict[str, Any]:
    user_prompt = build_user_prompt(user_template, handoff_row)

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            raw = call_ai(system_prompt, user_prompt)
            raw = strip_markdown_fences(raw)

            json_candidate = extract_json_object(raw) or raw

            try:
                parsed = json.loads(json_candidate)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"AI returned invalid JSON for post_id={handoff_row.get('post_id')}: {exc}\n"
                    f"Raw response:\n{raw[:4000]}"
                ) from exc

            validated = validate_semantic_output(parsed, handoff_row)
            validated["semantic_producer"] = f"semantic_extraction_agent::{MODEL_NAME}"
            validated["semantic_extracted_at"] = utc_now_iso()

            return validated

        except Exception as exc:
            last_error = exc
            if attempt <= MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)

    raise last_error if last_error is not None else RuntimeError("Unknown processing error")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

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

    try:
        system_prompt = read_text_file(SYSTEM_PROMPT_FILE)
        user_template = read_text_file(USER_PROMPT_FILE)
    except FileNotFoundError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "stage": "startup",
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1)

    input_rows = list(iter_jsonl(INPUT_JSONL))
    if ROW_LIMIT > 0:
        input_rows = input_rows[:ROW_LIMIT]

    output_rows: list[dict[str, Any]] = []

    stats = {
        "input_rows": 0,
        "processed_rows": 0,
        "failed_rows": 0,
    }

    for row in input_rows:
        stats["input_rows"] += 1
        post_id = row.get("post_id")

        try:
            result = process_row(row, system_prompt, user_template)
            output_rows.append(result)
            stats["processed_rows"] += 1
        except Exception as e:
            stats["failed_rows"] += 1
            print(
                json.dumps(
                    {
                        "status": "warning",
                        "stage": "semantic_extraction",
                        "post_id": post_id,
                        "error": str(e),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )

    written = write_jsonl(OUTPUT_JSONL, output_rows)

    print(
        json.dumps(
            {
                "status": "ok",
                "input_rows": stats["input_rows"],
                "processed_rows": stats["processed_rows"],
                "failed_rows": stats["failed_rows"],
                "written_rows": written,
                "output_jsonl": str(OUTPUT_JSONL),
                "model": MODEL_NAME,
                "api_url": API_URL,
                "row_limit": ROW_LIMIT,
                "max_tokens": MAX_TOKENS,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
