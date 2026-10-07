#!/usr/bin/env python3
"""Merge Golden baseline with audited Recall P1 promotions.

Inputs:
- data/remote/source_records.jsonl
- data/remote/recall_p1_promote_now_refreshed.jsonl

Outputs:
- data/remote/source_records_recall_p1_expanded.jsonl
- data/remote/source_records_recall_p1_merge_summary.json

No workbook is modified. This produces the candidate 44-row Golden dataset
for dry-run publication and integrity validation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

BASELINE_PATH = ROOT_DIR / "data/remote/source_records.jsonl"
PROMOTIONS_PATH = ROOT_DIR / "data/remote/recall_p1_promote_now_refreshed.jsonl"
OUTPUT_PATH = ROOT_DIR / "data/remote/source_records_recall_p1_expanded.jsonl"
SUMMARY_PATH = ROOT_DIR / "data/remote/source_records_recall_p1_merge_summary.json"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def score(row: dict[str, Any]) -> int:
    return int(row.get("Candidate ↔ Source Relationship Score", row.get("Quality Score", 0)) or 0)


def main() -> int:
    baseline = load_jsonl(BASELINE_PATH)
    promotions = load_jsonl(PROMOTIONS_PATH)

    merged: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []

    for row in baseline:
        source = str(row.get("Source", "")).strip().lower()
        if not source:
            raise RuntimeError("Baseline row missing Source")
        if source in merged:
            duplicates.append(source)
        merged[source] = dict(row)

    baseline_sources = set(merged)
    promotion_sources: list[str] = []
    overlaps: list[str] = []

    for row in promotions:
        source = str(row.get("Source", "")).strip().lower()
        if not source:
            raise RuntimeError("Promotion row missing Source")
        promotion_sources.append(source)
        if source in baseline_sources:
            overlaps.append(source)
            continue
        merged[source] = dict(row)

    rows = list(merged.values())
    rows.sort(key=lambda r: (-score(r), str(r.get("Source", ""))))

    write_jsonl(OUTPUT_PATH, rows)

    tier_counts = {
        tier: sum(1 for r in rows if r.get("Relationship Tier") == tier)
        for tier in ("A", "B", "C", "Review", "Exclude")
    }

    summary = {
        "status": "ok",
        "baseline_rows": len(baseline),
        "promotion_rows": len(promotions),
        "promotion_sources": sorted(promotion_sources),
        "baseline_duplicates": sorted(set(duplicates)),
        "promotion_overlaps_with_baseline": sorted(overlaps),
        "expanded_rows": len(rows),
        "net_new_rows": len(rows) - len(baseline),
        "tier_counts": tier_counts,
        "output": str(OUTPUT_PATH),
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
