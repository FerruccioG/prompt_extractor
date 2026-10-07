#!/usr/bin/env python3
"""Build a scorer-compatible relationship file for Recall Expansion P1 profiling.

Inputs:
- data/remote/recall_p1_profile_queue.jsonl
- data/remote/recall_p1_validation_results.jsonl
- data/remote/recall_p1_validation_unresolved.jsonl

Output:
- data/remote/recall_p1_profile_relationships.jsonl

This adapter lets the existing candidate-specific profiler be reused without
modifying the Golden baseline or original relationship-scoring artifacts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

PROFILE_QUEUE_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_PROFILE_QUEUE",
    ROOT_DIR / "data/remote/recall_p1_profile_queue.jsonl",
))
RESULTS_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_VALIDATION_RESULTS",
    ROOT_DIR / "data/remote/recall_p1_validation_results.jsonl",
))
UNRESOLVED_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_VALIDATION_UNRESOLVED",
    ROOT_DIR / "data/remote/recall_p1_validation_unresolved.jsonl",
))
OUTPUT_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_PROFILE_RELATIONSHIPS",
    ROOT_DIR / "data/remote/recall_p1_profile_relationships.jsonl",
))


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


def validation_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in (RESULTS_PATH, UNRESOLVED_PATH):
        for row in load_jsonl(path):
            key = str(row.get("validation_key", "") or row.get("canonical_host", "")).lower()
            if key:
                index[key] = row
    return index


def main() -> int:
    queue = load_jsonl(PROFILE_QUEUE_PATH)
    validation = validation_index()

    output: list[dict[str, Any]] = []
    missing_validation: list[str] = []

    for row in queue:
        source = str(
            row.get("recall_canonical_identity")
            or row.get("canonical_host")
            or ""
        ).lower()
        original_host = str(row.get("canonical_host", "")).lower()
        v = validation.get(original_host)

        if v is None:
            missing_validation.append(source)
            v = {}

        resolved = bool(v.get("resolved"))
        final_url = str(v.get("final_url", "") or "")
        root_url = str(row.get("canonical_root_url", "") or "")
        candidate_links = int(v.get("candidate_link_count", 0) or 0)

        # This is only a temporary generic relationship score used by the
        # existing profiler schema. Final multidimensional scoring happens later.
        generic_score = 55
        generic_score += min(20, candidate_links // 3)
        generic_score += min(10, len(v.get("candidate_term_hits", []) or []) * 2)
        generic_score += min(10, len(v.get("remote_term_hits", []) or []) * 4)
        if resolved:
            generic_score += 5
        generic_score = max(0, min(100, generic_score))

        output.append({
            "Source": source,
            "URL": root_url or (f"https://{source}/" if source else ""),
            "Original Host": original_host,
            "Final URL": final_url,
            "Final Host": v.get("final_host", ""),
            "Relationship Score": generic_score,
            "Relationship Class": "promising",
            "Verification Status": "live_verified" if resolved else "live_unresolved",
            "Candidate Link Count": candidate_links,
            "Candidate Term Hits": len(v.get("candidate_term_hits", []) or []),
            "Remote Term Hits": len(v.get("remote_term_hits", []) or []),
            "Email Evidence Count": int(row.get("email_evidence_count", 0) or 0),
            "Discovery Evidence Count": int(row.get("discovery_resolution_evidence_count", 0) or 0),
            "Direct Evidence URL Count": int(row.get("evidence_url_count", 0) or 0),
            "Prevalidation Score": int(row.get("prevalidation_score", 0) or 0),
            "Prevalidation Reason": row.get("prevalidation_reason"),
            "Recommended Use": "Recall Expansion P1 candidate: profile before Golden comparison",
            "Scoring Reasons": [
                "recall_expansion_p1",
                str(row.get("p1_gate_reason", "")),
            ],
            "Page Title": v.get("page_title", ""),
            "Meta Description": v.get("meta_description", ""),
            "Candidate Links": v.get("candidate_links", []),
        })

    output.sort(key=lambda r: (
        -int(r.get("Relationship Score", 0) or 0),
        str(r.get("Source", "")),
    ))
    write_jsonl(OUTPUT_PATH, output)

    print(json.dumps({
        "status": "ok",
        "profile_queue_input": len(queue),
        "relationships_written": len(output),
        "validation_records_missing": missing_validation,
        "output": str(OUTPUT_PATH),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
