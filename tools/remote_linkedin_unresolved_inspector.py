#!/usr/bin/env python3
"""
remote_linkedin_unresolved_inspector.py

Human-readable inspection of LinkedIn targets that still lack a resolved
candidate-source domain after harvest/OCR/email-provenance processing.

Input:
  <run>/harvest/linkedin/dedupe/still_needs_resolution.jsonl

Read-only diagnostic. No network calls, validation, scoring, Excel changes,
or watermark advancement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def latest_run_dir(root: Path) -> Path:
    runs_dir = root / "data" / "remote" / "refresh_runs"
    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"No refresh run directories found under {runs_dir}")
    return candidates[0]


def compact(value: Any, limit: int = 900) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + " ..."


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    path = run_dir / "harvest" / "linkedin" / "dedupe" / "still_needs_resolution.jsonl"

    if not path.exists():
        raise RuntimeError(f"LinkedIn unresolved file not found: {path}")

    rows = read_jsonl(path)

    print("REMOTE LINKEDIN UNRESOLVED INSPECTION")
    print("=" * 72)
    print(f"Run directory:       {run_dir}")
    print(f"Unresolved targets:  {len(rows)}")
    print()

    for i, row in enumerate(rows, start=1):
        print("=" * 72)
        print(f"[{i}/{len(rows)}]")
        print(f"LinkedIn URL:        {row.get('linkedin_url')}")
        print(f"Final URL:           {row.get('final_linkedin_url')}")
        print(f"Kind:                {row.get('content_kind')}")
        print(f"Harvest status:      {row.get('harvest_status')}")
        print(f"Access reason:       {row.get('access_reason')}")
        print(f"Message UID:         {row.get('source_message_uid')}")
        print(f"Email subject:       {compact(row.get('source_subject'), 300)}")
        print(f"Observed names:      {row.get('candidate_names_observed') or []}")
        print(f"Page title:          {compact(row.get('page_title'), 500)}")
        print(f"Reason:              {row.get('reason')}")
        print()
        print("EMAIL EVIDENCE")
        print(compact(row.get('email_text_excerpt'), 1200) or "[NONE]")
        print()
        print("PAGE TEXT")
        print(compact(row.get('page_text_excerpt'), 1200) or "[NONE]")
        print()
        print("OCR TEXT")
        print(compact(row.get('ocr_text_excerpt'), 1200) or "[NONE]")
        print()

    print("=" * 72)
    print("Read-only inspection complete.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
