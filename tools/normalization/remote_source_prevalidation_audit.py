#!/usr/bin/env python3
"""
remote_source_prevalidation_audit.py

Audit the source prevalidation buckets before expensive live validation.

Reads:
- data/remote/source_validation_queue.jsonl
- data/remote/source_semantic_review.jsonl
- data/remote/source_infrastructure_noise.jsonl

Prints:
- reason counts per bucket
- score bands
- top evidence-heavy sources
- low-scoring validation-queue tail
- representative semantic-review and infrastructure-noise samples

This script is read-only.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

QUEUE = Path(os.getenv(
    "REMOTE_SOURCE_VALIDATION_QUEUE",
    ROOT_DIR / "data/remote/source_validation_queue.jsonl",
))
REVIEW = Path(os.getenv(
    "REMOTE_SOURCE_SEMANTIC_REVIEW",
    ROOT_DIR / "data/remote/source_semantic_review.jsonl",
))
NOISE = Path(os.getenv(
    "REMOTE_SOURCE_INFRASTRUCTURE_NOISE",
    ROOT_DIR / "data/remote/source_infrastructure_noise.jsonl",
))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def evidence_total(row: dict[str, Any]) -> int:
    return (
        int(row.get("evidence_url_count", 0) or 0)
        + int(row.get("discovery_resolution_evidence_count", 0) or 0)
        + int(row.get("email_evidence_count", 0) or 0)
    )


def score_band(score: int) -> str:
    if score >= 90:
        return "90-100"
    if score >= 75:
        return "75-89"
    if score >= 60:
        return "60-74"
    if score >= 40:
        return "40-59"
    if score >= 20:
        return "20-39"
    return "0-19"


def compact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "host": row.get("canonical_host"),
        "score": row.get("prevalidation_score"),
        "reason": row.get("prevalidation_reason"),
        "email_evidence": row.get("email_evidence_count", 0),
        "direct_urls": row.get("evidence_url_count", 0),
        "resolved_discovery": row.get("discovery_resolution_evidence_count", 0),
        "root": row.get("canonical_root_url"),
    }


def summarize(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "bucket": name,
        "count": len(rows),
        "reason_counts": dict(Counter(
            str(r.get("prevalidation_reason", ""))
            for r in rows
        ).most_common()),
        "score_bands": dict(Counter(
            score_band(int(r.get("prevalidation_score", 0) or 0))
            for r in rows
        ).most_common()),
    }


def main() -> int:
    queue = load_jsonl(QUEUE)
    review = load_jsonl(REVIEW)
    noise = load_jsonl(NOISE)

    top_queue = sorted(
        queue,
        key=lambda r: (-evidence_total(r), -int(r.get("prevalidation_score", 0) or 0)),
    )[:25]

    low_queue = sorted(
        queue,
        key=lambda r: (int(r.get("prevalidation_score", 0) or 0), -evidence_total(r)),
    )[:25]

    top_review = sorted(
        review,
        key=lambda r: (-evidence_total(r), -int(r.get("prevalidation_score", 0) or 0)),
    )[:25]

    payload = {
        "status": "ok",
        "summaries": [
            summarize("validation_queue", queue),
            summarize("semantic_review", review),
            summarize("infrastructure_noise", noise),
        ],
        "top_validation_queue_by_evidence": [compact(r) for r in top_queue],
        "lowest_scoring_validation_queue": [compact(r) for r in low_queue],
        "top_semantic_review_by_evidence": [compact(r) for r in top_review],
        "infrastructure_noise": [compact(r) for r in noise],
    }

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
