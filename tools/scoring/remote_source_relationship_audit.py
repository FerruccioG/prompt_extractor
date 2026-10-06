#!/usr/bin/env python3
"""
remote_source_relationship_audit.py

Read-only audit of scored Candidate <-> Remote Source relationships.

Inputs:
- data/remote/source_relationship_scored.jsonl
- data/remote/source_relationship_shortlist.jsonl
- data/remote/source_relationship_review.jsonl
- data/remote/source_relationship_excluded.jsonl

Prints compact summaries for human inspection before Excel publication.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

SCORED = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_SCORED",
    ROOT_DIR / "data/remote/source_relationship_scored.jsonl",
))
SHORTLIST = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_SHORTLIST",
    ROOT_DIR / "data/remote/source_relationship_shortlist.jsonl",
))
REVIEW = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_REVIEW",
    ROOT_DIR / "data/remote/source_relationship_review.jsonl",
))
EXCLUDED = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_EXCLUDED",
    ROOT_DIR / "data/remote/source_relationship_excluded.jsonl",
))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
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
        "url": row.get("URL"),
        "score": row.get("Relationship Score"),
        "class": row.get("Relationship Class"),
        "verification": row.get("Verification Status"),
        "candidate_links": row.get("Candidate Link Count", 0),
        "remote_hits": row.get("Remote Term Hits", 0),
        "candidate_hits": row.get("Candidate Term Hits", 0),
        "email_evidence": row.get("Email Evidence Count", 0),
        "discovery_evidence": row.get("Discovery Evidence Count", 0),
        "direct_evidence_urls": row.get("Direct Evidence URL Count", 0),
        "aliases": row.get("Original Hosts", []),
        "final_url": row.get("Final URL"),
        "recommended_use": row.get("Recommended Use"),
        "reasons": row.get("Scoring Reasons", []),
        "title": row.get("Page Title"),
    }


def main() -> int:
    scored = load_jsonl(SCORED)
    shortlist = load_jsonl(SHORTLIST)
    review = load_jsonl(REVIEW)
    excluded = load_jsonl(EXCLUDED)

    strong = [r for r in scored if r.get("Relationship Class") == "strong"]
    promising = [r for r in scored if r.get("Relationship Class") == "promising"]

    blocked_known = [
        r for r in scored
        if r.get("Verification Status") == "known_source_live_blocked"
    ]
    redirects = [
        r for r in scored
        if any(str(reason).startswith("redirected_to:") for reason in (r.get("Scoring Reasons", []) or []))
    ]

    payload = {
        "status": "ok",
        "counts": {
            "scored": len(scored),
            "strong": len(strong),
            "promising": len(promising),
            "review": len(review),
            "excluded": len(excluded),
            "shortlist": len(shortlist),
            "known_source_live_blocked": len(blocked_known),
            "redirect_flagged": len(redirects),
        },
        "verification_status_counts": dict(Counter(
            str(r.get("Verification Status", "")) for r in scored
        )),
        "strong_relationships": [compact(r) for r in strong],
        "promising_relationships": [compact(r) for r in promising],
        "manual_review_relationships": [compact(r) for r in review],
        "known_source_live_blocked": [compact(r) for r in blocked_known],
        "redirect_flagged": [compact(r) for r in redirects],
        "top_excluded_by_score": [
            compact(r) for r in sorted(
                excluded,
                key=lambda r: (-int(r.get("Relationship Score", 0) or 0), str(r.get("Source", ""))),
            )[:20]
        ],
    }

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
