#!/usr/bin/env python3
"""
remote_candidate_source_final_audit.py

Read-only audit of final Candidate <-> Remote Source scoring before Excel load.

Inputs:
- data/remote/source_records.jsonl
- data/remote/source_records_review.jsonl
- data/remote/source_records_excluded.jsonl

Prints:
- tier counts
- top included relationships
- bottom included relationships
- all review rows
- all excluded rows
- dimension averages by tier
- suspicious cases for manual inspection
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

INCLUDED = Path(os.getenv(
    "REMOTE_SOURCE_RECORDS",
    ROOT_DIR / "data/remote/source_records.jsonl",
))
REVIEW = Path(os.getenv(
    "REMOTE_SOURCE_RECORDS_REVIEW",
    ROOT_DIR / "data/remote/source_records_review.jsonl",
))
EXCLUDED = Path(os.getenv(
    "REMOTE_SOURCE_RECORDS_EXCLUDED",
    ROOT_DIR / "data/remote/source_records_excluded.jsonl",
))

DIMENSIONS = (
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
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows

def compact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": row.get("Source"),
        "score": row.get("Candidate ↔ Source Relationship Score"),
        "tier": row.get("Relationship Tier"),
        "category": row.get("Category"),
        "candidate_fit": row.get("Candidate Fit"),
        "remote_strength": row.get("Remote Strength"),
        "ireland_eu": row.get("Ireland/EU Accessibility"),
        "opportunity_density": row.get("Opportunity Density"),
        "specialization_fit": row.get("Specialization Fit"),
        "hidden_potential": row.get("Hidden Potential"),
        "directness": row.get("Directness"),
        "trust_risk": row.get("Trust/Risk"),
        "candidate_cost": row.get("Candidate Cost"),
        "earning_potential": row.get("Earning Potential"),
        "signal_quality": row.get("Signal Quality"),
        "freshness_activity": row.get("Freshness/Activity"),
        "generic_relationship_score": row.get("Generic Relationship Score"),
        "verification_status": row.get("Verification Status"),
        "profile_signal_groups": row.get("Profile Signal Groups"),
        "email_evidence": row.get("Email Evidence Count"),
        "direct_evidence_urls": row.get("Direct Evidence URL Count"),
        "discovery_evidence": row.get("Discovery Evidence Count"),
        "recommended_use": row.get("Recommended Use"),
        "notes": row.get("Notes"),
        "verification_url": row.get("Evidence/Verification URL"),
    }

def dimension_averages(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for dim in DIMENSIONS:
        vals = [
            float(r.get(dim))
            for r in rows
            if isinstance(r.get(dim), (int, float))
        ]
        if vals:
            out[dim] = round(mean(vals), 1)
    return out

def main() -> int:
    included = load_jsonl(INCLUDED)
    review = load_jsonl(REVIEW)
    excluded = load_jsonl(EXCLUDED)
    all_rows = included + review + excluded

    tier_counts = Counter(str(r.get("Relationship Tier", "")) for r in all_rows)
    by_tier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        by_tier[str(row.get("Relationship Tier", ""))].append(row)

    suspicious: list[dict[str, Any]] = []
    for row in all_rows:
        score = int(row.get("Candidate ↔ Source Relationship Score", 0) or 0)
        cand = int(row.get("Candidate Fit", 0) or 0)
        remote = int(row.get("Remote Strength", 0) or 0)
        geo = int(row.get("Ireland/EU Accessibility", 0) or 0)
        direct = int(row.get("Directness", 0) or 0)
        signal = int(row.get("Signal Quality", 0) or 0)
        source = str(row.get("Source", ""))

        flags = []
        if score >= 60 and cand < 30:
            flags.append("high_total_low_candidate_fit")
        if score >= 60 and remote < 35:
            flags.append("high_total_low_remote_strength")
        if score >= 60 and geo < 30:
            flags.append("high_total_low_ireland_eu")
        if score >= 60 and direct < 45:
            flags.append("high_total_low_directness")
        if score >= 60 and signal < 35:
            flags.append("high_total_low_signal_quality")
        if score < 45 and cand >= 70 and remote >= 60:
            flags.append("low_total_despite_strong_fit_remote")

        if flags:
            item = compact(row)
            item["audit_flags"] = flags
            suspicious.append(item)

    included_sorted = sorted(
        included,
        key=lambda r: (-int(r.get("Candidate ↔ Source Relationship Score", 0) or 0), str(r.get("Source", ""))),
    )
    review_sorted = sorted(
        review,
        key=lambda r: (-int(r.get("Candidate ↔ Source Relationship Score", 0) or 0), str(r.get("Source", ""))),
    )
    excluded_sorted = sorted(
        excluded,
        key=lambda r: (-int(r.get("Candidate ↔ Source Relationship Score", 0) or 0), str(r.get("Source", ""))),
    )

    payload = {
        "status": "ok",
        "counts": {
            "included": len(included),
            "review": len(review),
            "excluded": len(excluded),
            "total_scored": len(all_rows),
            "tiers": dict(tier_counts),
            "suspicious_cases": len(suspicious),
        },
        "dimension_averages_by_tier": {
            tier: dimension_averages(rows)
            for tier, rows in sorted(by_tier.items())
        },
        "top_20_included": [compact(r) for r in included_sorted[:20]],
        "bottom_15_included": [compact(r) for r in included_sorted[-15:]],
        "review_rows": [compact(r) for r in review_sorted],
        "excluded_rows": [compact(r) for r in excluded_sorted],
        "suspicious_cases": suspicious,
    }

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
