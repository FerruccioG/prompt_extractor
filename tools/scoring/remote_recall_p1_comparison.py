#!/usr/bin/env python3
"""Compare Recall Expansion P1 scored candidates against the Golden baseline.

Inputs:
- data/remote/source_records.jsonl
- data/remote/recall_p1_source_records.jsonl
- data/remote/recall_p1_source_records_excluded.jsonl

Outputs:
- data/remote/recall_p1_comparison.jsonl
- data/remote/recall_p1_promotion_candidates.jsonl
- data/remote/recall_p1_comparison_summary.json

This is read-only with respect to the Golden workbook and baseline JSONL.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

BASELINE_PATH = Path(os.getenv(
    "REMOTE_RECALL_BASELINE_RECORDS",
    ROOT_DIR / "data/remote/source_records.jsonl",
))
RECALL_PATH = Path(os.getenv(
    "REMOTE_RECALL_SCORED_RECORDS",
    ROOT_DIR / "data/remote/recall_p1_source_records.jsonl",
))
EXCLUDED_PATH = Path(os.getenv(
    "REMOTE_RECALL_EXCLUDED_RECORDS",
    ROOT_DIR / "data/remote/recall_p1_source_records_excluded.jsonl",
))

COMPARISON_PATH = Path(os.getenv(
    "REMOTE_RECALL_COMPARISON_OUTPUT",
    ROOT_DIR / "data/remote/recall_p1_comparison.jsonl",
))
PROMOTION_PATH = Path(os.getenv(
    "REMOTE_RECALL_PROMOTION_OUTPUT",
    ROOT_DIR / "data/remote/recall_p1_promotion_candidates.jsonl",
))
SUMMARY_PATH = Path(os.getenv(
    "REMOTE_RECALL_COMPARISON_SUMMARY",
    ROOT_DIR / "data/remote/recall_p1_comparison_summary.json",
))

KEY_DIMS = (
    "Candidate ↔ Source Relationship Score",
    "Candidate Fit",
    "Remote Strength",
    "Ireland/EU Accessibility",
    "Opportunity Density",
    "Specialization Fit",
    "Hidden Potential",
    "Directness",
    "Trust/Risk",
    "Candidate Cost",
    "Earning Potential",
    "Signal Quality",
    "Freshness/Activity",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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


def tier_rank(tier: str) -> int:
    return {"A": 4, "B": 3, "C": 2, "Review": 1, "Exclude": 0}.get(tier, -1)


def baseline_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = sorted(score(r) for r in rows)
    by_tier: dict[str, list[int]] = {}
    for r in rows:
        by_tier.setdefault(str(r.get("Relationship Tier", "")), []).append(score(r))

    def median(values: list[int]) -> float:
        if not values:
            return 0.0
        vals = sorted(values)
        n = len(vals)
        mid = n // 2
        return float(vals[mid]) if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0

    return {
        "count": len(rows),
        "min_score": min(scores) if scores else 0,
        "median_score": median(scores),
        "max_score": max(scores) if scores else 0,
        "tier_counts": {k: len(v) for k, v in sorted(by_tier.items())},
        "tier_medians": {k: median(v) for k, v in sorted(by_tier.items())},
    }


def main() -> int:
    baseline = load_jsonl(BASELINE_PATH)
    recall = load_jsonl(RECALL_PATH)
    excluded = load_jsonl(EXCLUDED_PATH)

    baseline_sources = {str(r.get("Source", "")).lower() for r in baseline}
    stats = baseline_stats(baseline)

    comparison: list[dict[str, Any]] = []
    promotions: list[dict[str, Any]] = []

    for row in recall:
        source = str(row.get("Source", "")).lower()
        tier = str(row.get("Relationship Tier", ""))
        relationship_score = score(row)

        baseline_overlap = source in baseline_sources
        qualifies = (
            not baseline_overlap
            and tier in {"A", "B", "C"}
            and relationship_score >= 45
        )

        # Promotion confidence is deliberately tier-sensitive.
        if tier == "A":
            recommendation = "promote_high_confidence"
        elif tier == "B":
            recommendation = "promote"
        elif tier == "C":
            recommendation = "promote_selective"
        else:
            recommendation = "hold"

        if baseline_overlap:
            recommendation = "duplicate_hold"

        out = dict(row)
        out["Baseline Overlap"] = baseline_overlap
        out["Golden Baseline Median Score"] = stats["median_score"]
        out["Promotion Recommendation"] = recommendation
        out["Promotion Eligible"] = qualifies

        comparison.append(out)
        if qualifies:
            promotions.append(out)

    comparison.sort(key=lambda r: (
        -tier_rank(str(r.get("Relationship Tier", ""))),
        -score(r),
        str(r.get("Source", "")),
    ))
    promotions.sort(key=lambda r: (
        -tier_rank(str(r.get("Relationship Tier", ""))),
        -score(r),
        str(r.get("Source", "")),
    ))

    write_jsonl(COMPARISON_PATH, comparison)
    write_jsonl(PROMOTION_PATH, promotions)

    summary = {
        "status": "ok",
        "golden_baseline": stats,
        "recall_included_scored": len(recall),
        "recall_excluded_scored": len(excluded),
        "promotion_candidates": len(promotions),
        "promotion_by_tier": {
            tier: sum(1 for r in promotions if r.get("Relationship Tier") == tier)
            for tier in ("A", "B", "C")
        },
        "baseline_overlaps": sum(1 for r in comparison if r.get("Baseline Overlap")),
        "ranked_candidates": [
            {
                "source": r.get("Source"),
                "score": score(r),
                "tier": r.get("Relationship Tier"),
                "candidate_fit": r.get("Candidate Fit"),
                "remote_strength": r.get("Remote Strength"),
                "ireland_eu": r.get("Ireland/EU Accessibility"),
                "opportunity_density": r.get("Opportunity Density"),
                "specialization_fit": r.get("Specialization Fit"),
                "recommendation": r.get("Promotion Recommendation"),
            }
            for r in comparison
        ],
        "excluded_sources": [
            {
                "source": r.get("Source"),
                "score": score(r),
                "tier": r.get("Relationship Tier"),
            }
            for r in excluded
        ],
        "comparison_output": str(COMPARISON_PATH),
        "promotion_output": str(PROMOTION_PATH),
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
