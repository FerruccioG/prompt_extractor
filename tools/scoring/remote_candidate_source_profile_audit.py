#!/usr/bin/env python3
"""
remote_candidate_source_profile_audit.py

Read-only audit of Candidate <-> Remote Source profiling results.

Purpose:
- Inspect whether the profiler is extracting useful evidence before full-corpus run.
- Surface false negatives where a known strong source produced little/no profile signal.
- Show signal coverage by source and category.

Inputs:
- data/remote/candidate_source_profile_results.jsonl
- data/remote/candidate_source_profile_unresolved.jsonl
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

RESULTS = Path(os.getenv(
    "REMOTE_CANDIDATE_SOURCE_PROFILE_RESULTS",
    ROOT_DIR / "data/remote/candidate_source_profile_results.jsonl",
))
UNRESOLVED = Path(os.getenv(
    "REMOTE_CANDIDATE_SOURCE_PROFILE_UNRESOLVED",
    ROOT_DIR / "data/remote/candidate_source_profile_unresolved.jsonl",
))


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
        "source": row.get("source"),
        "relationship_score": row.get("relationship_score"),
        "relationship_class": row.get("relationship_class"),
        "pages_attempted": row.get("pages_attempted"),
        "pages_resolved": row.get("pages_resolved"),
        "profile_signal_group_count": row.get("profile_signal_group_count"),
        "profile_signal_total_hits": row.get("profile_signal_total_hits"),
        "profile_signal_hits": row.get("profile_signal_hits"),
        "remote_hits": row.get("remote_hits"),
        "eu_ireland_hits": row.get("eu_ireland_hits"),
        "global_hits": row.get("global_hits"),
        "contract_hits": row.get("contract_hits"),
        "permanent_hits": row.get("permanent_hits"),
        "cost_friction_hits": row.get("cost_friction_hits"),
        "free_access_hits": row.get("free_access_hits"),
        "auth_friction_hits": row.get("auth_friction_hits"),
        "sample_titles": row.get("sample_titles"),
        "page_urls": row.get("page_urls"),
    }


def latest_by_source(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = str(row.get("source", "")).lower()
        if source:
            latest[source] = row
    return list(latest.values())


def main() -> int:
    rows = latest_by_source(load_jsonl(RESULTS))
    unresolved = latest_by_source(load_jsonl(UNRESOLVED))

    zero_signal = [
        r for r in rows
        if int(r.get("profile_signal_group_count", 0) or 0) == 0
    ]
    weak_signal = [
        r for r in rows
        if 0 < int(r.get("profile_signal_group_count", 0) or 0) <= 1
    ]

    group_counts = Counter()
    for row in rows:
        for group, terms in (row.get("profile_signal_hits", {}) or {}).items():
            if terms:
                group_counts[group] += 1

    payload = {
        "status": "ok",
        "profiled_rows": len(rows),
        "unresolved_rows": len(unresolved),
        "zero_profile_signal_rows": len(zero_signal),
        "one_profile_group_rows": len(weak_signal),
        "signal_group_source_counts": dict(group_counts),
        "profiled_sources": [compact(r) for r in rows],
        "zero_signal_sources": [compact(r) for r in zero_signal],
        "weak_signal_sources": [compact(r) for r in weak_signal],
        "unresolved_sources": unresolved,
    }

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
