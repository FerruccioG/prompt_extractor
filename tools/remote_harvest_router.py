#!/usr/bin/env python3
"""
remote_harvest_router.py

Build run-scoped harvest queues from a Remote Golden refresh run.

Input:
  <run_dir>/actionable_url_queue.jsonl

Outputs:
  <run_dir>/harvest/instagram_queue.jsonl
  <run_dir>/harvest/tiktok_queue.jsonl
  <run_dir>/harvest/linkedin_queue.jsonl
  <run_dir>/harvest/unclassified_queue.jsonl
  <run_dir>/harvest/harvest_manifest.json

This router does not browse, scrape, OCR, validate, score, or publish.
It only partitions already-classified actionable targets into
platform-specific queues for the next harvest stages.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


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


def normalize_router_row(row: dict) -> dict:
    canonical_url = row.get("canonical_url") or row.get("url")
    return {
        "normalized_url": canonical_url,
        "canonical_url": canonical_url,
        "raw_url": row.get("url"),
        "platform": row.get("platform"),
        "classification_reason": row.get("classification_reason"),
        "source_message_uid": row.get("source_message_uid"),
        "source_subject": row.get("source_subject"),
        "intake_channel": row.get("intake_channel"),
        "email_datetime_utc": row.get("email_datetime_utc"),
        "status": "pending",
    }


def route_row(row: dict) -> str:
    platform = (row.get("platform") or "").strip().lower()
    reason = (row.get("classification_reason") or "").strip().lower()

    if platform == "instagram":
        return "instagram"

    if platform == "tiktok":
        return "tiktok"

    if platform == "linkedin" or reason.startswith("linkedin_"):
        return "linkedin"

    return "unclassified"


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Refresh run directory. Defaults to latest run.",
    )
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    input_path = run_dir / "actionable_url_queue.jsonl"

    if not input_path.exists():
        raise RuntimeError(f"Actionable queue not found: {input_path}")

    harvest_dir = run_dir / "harvest"
    harvest_dir.mkdir(parents=True, exist_ok=True)

    rows = read_jsonl(input_path)

    buckets: dict[str, list[dict]] = {
        "instagram": [],
        "tiktok": [],
        "linkedin": [],
        "unclassified": [],
    }

    seen: dict[str, set[str]] = {name: set() for name in buckets}

    for row in rows:
        bucket = route_row(row)
        routed = normalize_router_row(row)
        key = routed["canonical_url"] or routed["raw_url"]

        if key in seen[bucket]:
            continue

        seen[bucket].add(key)
        buckets[bucket].append(routed)

    paths = {
        "instagram": harvest_dir / "instagram_queue.jsonl",
        "tiktok": harvest_dir / "tiktok_queue.jsonl",
        "linkedin": harvest_dir / "linkedin_queue.jsonl",
        "unclassified": harvest_dir / "unclassified_queue.jsonl",
    }

    for name, path in paths.items():
        write_jsonl(path, buckets[name])

    manifest = {
        "status": "ok",
        "run_dir": str(run_dir),
        "input_actionable_count": len(rows),
        "instagram_count": len(buckets["instagram"]),
        "tiktok_count": len(buckets["tiktok"]),
        "linkedin_count": len(buckets["linkedin"]),
        "unclassified_count": len(buckets["unclassified"]),
        "queues": {name: str(path) for name, path in paths.items()},
        "harvest_executed": False,
    }

    manifest_path = harvest_dir / "harvest_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print("REMOTE HARVEST ROUTER OK")
    print(f"Run directory:              {run_dir}")
    print(f"Input actionable targets:   {len(rows)}")
    print(f"Instagram targets:          {len(buckets['instagram'])}")
    print(f"TikTok targets:             {len(buckets['tiktok'])}")
    print(f"LinkedIn targets:           {len(buckets['linkedin'])}")
    print(f"Unclassified targets:       {len(buckets['unclassified'])}")
    print(f"Harvest manifest:           {manifest_path}")
    print()
    print("No browser harvesting was executed.")
    print("No OCR was executed.")
    print("No watermark was advanced.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
