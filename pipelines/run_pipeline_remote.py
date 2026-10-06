#!/usr/bin/env python3
"""
run_pipeline_remote.py

Remote Opportunities variant of the Prompt Extractor pipeline.

Key difference from the original Instagram prompt pipeline:
- Gmail ingestion is fixed to: subject:Remote
- The original Prompt pipeline remains unchanged.

Current stages intentionally reuse the proven extraction stack:
1. remote_email_reader.py
2. url_normalizer.py
3. url_filter.py
4. platform_splitter.py
5. scraper_instagram.py
6. ocr_extractor.py
7. text_group_builder.py
8. text_manipulator_prep.py

This is Phase 1. A later phase can replace the Instagram-only scraper with
a generic browser resolver for Instagram/TikTok/Facebook/LinkedIn/web pages.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT_DIR / "tools"


def load_dotenv_if_present() -> None:
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return

    for raw_line in env_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_dotenv_if_present()

PIPELINE: List[str] = [
    "remote_email_reader.py",
    "url_normalizer.py",
    "url_filter.py",
    "platform_splitter.py",
    "scraper_instagram.py",
    "ocr_extractor.py",
    "text_group_builder.py",
    "text_manipulator_prep.py",
]

REQUIRED_ENV_VARS = [
    "EMAIL_ADDRESS",
    "EMAIL_APP_PASSWORD",
]

REMOTE_DIRS_TO_RESET = [
    ROOT_DIR / "data/instagram/screenshots",
    ROOT_DIR / "data/instagram/html",
]

REMOTE_FILES_TO_REMOVE = [
    ROOT_DIR / "data/email_url_audit.jsonl",
    ROOT_DIR / "data/url_queue.jsonl",
    ROOT_DIR / "data/url_queue_normalized.jsonl",
    ROOT_DIR / "data/url_queue_scrape_ready.jsonl",
    ROOT_DIR / "data/url_queue_rejected.jsonl",
    ROOT_DIR / "data/instagram/results.jsonl",
    ROOT_DIR / "data/instagram/ocr_raw.jsonl",
    ROOT_DIR / "data/instagram/ocr_grouped.jsonl",
    ROOT_DIR / "data/instagram/text_manipulator_input.jsonl",
]

VALID_MODES = {"CLEAN", "INCREMENTAL"}
DEFAULT_MODE = "CLEAN"


@dataclass
class StageResult:
    stage: str
    script: str
    returncode: int
    duration_seconds: float
    status: str


def now_utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def get_pipeline_mode() -> str:
    mode = os.getenv("PIPELINE_MODE", DEFAULT_MODE).strip().upper()
    if mode not in VALID_MODES:
        raise RuntimeError(
            f"Invalid PIPELINE_MODE='{mode}'. Valid values: {sorted(VALID_MODES)}"
        )
    return mode


def preflight_check_required_env() -> list[str]:
    return [name for name in REQUIRED_ENV_VARS if not os.getenv(name, "").strip()]


def clean_remote_artifacts() -> dict:
    cleaned_dirs = []
    removed_files = []

    for path in REMOTE_DIRS_TO_RESET:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        cleaned_dirs.append(str(path))

    for path in REMOTE_FILES_TO_REMOVE:
        if path.exists():
            path.unlink()
            removed_files.append(str(path))

    return {"cleaned_dirs": cleaned_dirs, "removed_files": removed_files}


def run_stage(script_name: str) -> StageResult:
    script_path = TOOLS_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"Stage script not found: {script_path}")

    print("\n" + "=" * 80)
    print(f"STARTING STAGE: {script_name}")
    print(f"SCRIPT PATH: {script_path}")
    print(f"STARTED_AT: {now_utc_iso()}")
    print("=" * 80)
    sys.stdout.flush()

    started = time.time()
    process = subprocess.Popen(
        [sys.executable, str(script_path)],
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")

    process.wait()
    duration = round(time.time() - started, 2)
    status = "ok" if process.returncode == 0 else "failed"

    return StageResult(
        stage=script_name.replace(".py", ""),
        script=str(script_path),
        returncode=process.returncode,
        duration_seconds=duration,
        status=status,
    )


def print_json_summary(payload: dict) -> None:
    print("\nFINAL SUMMARY:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    sys.stdout.flush()


def main() -> int:
    overall_started = time.time()
    results: List[StageResult] = []

    try:
        mode = get_pipeline_mode()

        print("Remote Opportunities pipeline started.")
        print("Gmail filter: subject:Remote")
        print(f"Root directory: {ROOT_DIR}")
        print(f"Python executable: {sys.executable}")
        print(f"Pipeline mode: {mode}")
        print(f"Total stages: {len(PIPELINE)}")
        print(f"Started at: {now_utc_iso()}")
        sys.stdout.flush()

        missing_env = preflight_check_required_env()
        if missing_env:
            print_json_summary({
                "status": "failed",
                "failed_stage": "preflight",
                "error": "Missing required environment variables",
                "missing_env": missing_env,
                "total_duration_seconds": round(time.time() - overall_started, 2),
                "stages": [],
            })
            return 1

        cleanup_info = None
        if mode == "CLEAN":
            cleanup_info = clean_remote_artifacts()

        for script_name in PIPELINE:
            result = run_stage(script_name)
            results.append(result)

            if result.returncode != 0:
                print_json_summary({
                    "status": "failed",
                    "failed_stage": result.stage,
                    "pipeline_mode": mode,
                    "gmail_query": "subject:Remote",
                    "cleanup": cleanup_info,
                    "total_duration_seconds": round(time.time() - overall_started, 2),
                    "stages": [asdict(r) for r in results],
                })
                return result.returncode

        print_json_summary({
            "status": "ok",
            "message": "Remote pipeline completed.",
            "pipeline_mode": mode,
            "gmail_query": "subject:Remote",
            "cleanup": cleanup_info,
            "total_duration_seconds": round(time.time() - overall_started, 2),
            "stages": [asdict(r) for r in results],
        })
        return 0

    except Exception as exc:
        print_json_summary({
            "status": "failed",
            "failed_stage": "orchestrator",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "total_duration_seconds": round(time.time() - overall_started, 2),
            "stages": [asdict(r) for r in results],
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
