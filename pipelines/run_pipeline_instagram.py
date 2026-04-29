#!/usr/bin/env python3
"""
run_pipeline_instagram.py

Production-grade orchestrator for the Prompt Extractor Instagram pipeline.

Pipeline order:
1. email_reader.py
2. url_normalizer.py
3. url_filter.py
4. platform_splitter.py
5. scraper_instagram.py
6. ocr_extractor.py
7. text_group_builder.py
8. text_manipulator_prep.py

Features:
- CLEAN / INCREMENTAL mode
- preflight environment validation
- structured stage timing + summary
- fail-fast behavior
- deterministic cleanup of Instagram-specific downstream artifacts

Usage:
    python3 tools/run_pipeline_instagram.py

Optional environment variables:
    PIPELINE_MODE=CLEAN|INCREMENTAL
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


ROOT_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT_DIR / "tools"

PIPELINE: List[str] = [
    "email_reader.py",
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
    "GMAIL_QUERY",
]

# Only downstream Instagram-specific artifacts are cleaned.
INSTAGRAM_DIRS_TO_RESET = [
    ROOT_DIR / "data/instagram/screenshots",
    ROOT_DIR / "data/instagram/html",
]

INSTAGRAM_FILES_TO_REMOVE = [
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
    missing: list[str] = []
    for name in REQUIRED_ENV_VARS:
        value = os.getenv(name, "").strip()
        if not value:
            missing.append(name)
    return missing


def clean_instagram_artifacts() -> dict:
    cleaned_dirs: list[str] = []
    removed_files: list[str] = []

    for path in INSTAGRAM_DIRS_TO_RESET:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        cleaned_dirs.append(str(path))

    for path in INSTAGRAM_FILES_TO_REMOVE:
        if path.exists():
            path.unlink()
            removed_files.append(str(path))

    return {
        "cleaned_dirs": cleaned_dirs,
        "removed_files": removed_files,
    }


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

    print("\n" + "-" * 80)
    print(
        f"STAGE COMPLETE: {script_name} | "
        f"status={status} | returncode={process.returncode} | duration_seconds={duration}"
    )
    print("-" * 80)
    sys.stdout.flush()

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

        print("Prompt Extractor Instagram pipeline started.")
        print(f"Root directory: {ROOT_DIR}")
        print(f"Python executable: {sys.executable}")
        print(f"Pipeline mode: {mode}")
        print(f"Total stages: {len(PIPELINE)}")
        print(f"Started at: {now_utc_iso()}")
        sys.stdout.flush()

        missing_env = preflight_check_required_env()
        if missing_env:
            print_json_summary(
                {
                    "status": "failed",
                    "failed_stage": "preflight",
                    "error": "Missing required environment variables",
                    "missing_env": missing_env,
                    "total_duration_seconds": round(time.time() - overall_started, 2),
                    "stages": [],
                }
            )
            return 1

        cleanup_info = None
        if mode == "CLEAN":
            print("\n[CLEAN MODE] Resetting Instagram downstream artifacts...")
            cleanup_info = clean_instagram_artifacts()
            print(json.dumps(cleanup_info, indent=2))
            sys.stdout.flush()
        else:
            print("\n[INCREMENTAL MODE] Preserving existing Instagram downstream artifacts.")
            sys.stdout.flush()

        for script_name in PIPELINE:
            result = run_stage(script_name)
            results.append(result)

            if result.returncode != 0:
                print_json_summary(
                    {
                        "status": "failed",
                        "failed_stage": result.stage,
                        "pipeline_mode": mode,
                        "cleanup": cleanup_info,
                        "total_duration_seconds": round(time.time() - overall_started, 2),
                        "stages": [asdict(r) for r in results],
                    }
                )
                return result.returncode

        print_json_summary(
            {
                "status": "ok",
                "message": (
                    "Pipeline completed through text_manipulator_prep.py. "
                    "Next input ready: data/instagram/text_manipulator_input.jsonl"
                ),
                "pipeline_mode": mode,
                "cleanup": cleanup_info,
                "total_duration_seconds": round(time.time() - overall_started, 2),
                "stages": [asdict(r) for r in results],
            }
        )
        return 0

    except Exception as exc:
        print_json_summary(
            {
                "status": "failed",
                "failed_stage": "orchestrator",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "total_duration_seconds": round(time.time() - overall_started, 2),
                "stages": [asdict(r) for r in results],
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
