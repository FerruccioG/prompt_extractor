#!/usr/bin/env python3
"""Consolidate all Recall Expansion P3 gate outputs.

Combines the four validated P3 batches, canonicalizes known aliases, collapses
duplicates, preserves provenance, and produces a single P3 survivor universe.

Inputs:
- data/remote/recall_p3_batch01_targeted_review.jsonl
- data/remote/recall_p3_batch02_targeted_review.jsonl
- data/remote/recall_p3_batch03_targeted_review.jsonl
- data/remote/recall_p3_batch04_targeted_review.jsonl
- corresponding indirect / overlap / rejected files

Outputs:
- data/remote/recall_p3_targeted_review_consolidated.jsonl
- data/remote/recall_p3_indirect_keep_consolidated.jsonl
- data/remote/recall_p3_baseline_overlap_consolidated.jsonl
- data/remote/recall_p3_rejected_or_deferred_consolidated.jsonl
- data/remote/recall_p3_consolidation_summary.json

No Golden files are modified.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]

KINDS = {
    "targeted_review": "targeted_review",
    "indirect_keep": "indirect_keep",
    "baseline_overlap": "baseline_overlap",
    "rejected_or_deferred": "rejected_or_deferred",
}

INPUTS = {
    kind: [
        ROOT_DIR / f"data/remote/recall_p3_batch{batch:02d}_{suffix}.jsonl"
        for batch in range(1, 5)
    ]
    for kind, suffix in KINDS.items()
}

OUTPUTS = {
    "targeted_review": ROOT_DIR / "data/remote/recall_p3_targeted_review_consolidated.jsonl",
    "indirect_keep": ROOT_DIR / "data/remote/recall_p3_indirect_keep_consolidated.jsonl",
    "baseline_overlap": ROOT_DIR / "data/remote/recall_p3_baseline_overlap_consolidated.jsonl",
    "rejected_or_deferred": ROOT_DIR / "data/remote/recall_p3_rejected_or_deferred_consolidated.jsonl",
}

SUMMARY_PATH = ROOT_DIR / "data/remote/recall_p3_consolidation_summary.json"

HOST_ALIASES = {
    "angel.co": "wellfound.com",
    "remotive.io": "remotive.com",
    "thisisgcs.com": "gcsrecruitment.com",
    "gcstechtalent.com": "gcsrecruitment.com",
    "robertwalters.co.uk": "robertwalters.com",
    "online.robertwalters.com": "robertwalters.com",
    "api.hireez.com": "hireez.com",
}


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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def host_of(value: str) -> str:
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = (parsed.hostname or "").lower().strip(".")
        if host.startswith("www."):
            host = host[4:]
        return HOST_ALIASES.get(host, host)
    except Exception:
        return ""


def identity(row: dict[str, Any]) -> str:
    raw = str(
        row.get("recall_canonical_identity")
        or row.get("canonical_host")
        or row.get("canonical_root_url")
        or ""
    ).strip().lower()
    return host_of(raw) or raw


def merge_group(rows: list[dict[str, Any]], canonical: str, kind: str) -> dict[str, Any]:
    # Keep the strongest evidence-bearing representative, but preserve all
    # aliases/batches/provenance.
    def strength(r: dict[str, Any]) -> tuple[int, int, int]:
        v = r.get("p3_validation", {}) or {}
        return (
            int(v.get("candidate_link_count", 0) or 0),
            int(r.get("recall_score", 0) or 0),
            int(r.get("recall_group_evidence_total", 0) or 0),
        )

    representative = max(rows, key=strength)
    out = dict(representative)

    aliases = sorted({
        str(
            r.get("recall_canonical_identity")
            or r.get("canonical_host")
            or ""
        ).strip().lower()
        for r in rows
        if str(r.get("recall_canonical_identity") or r.get("canonical_host") or "").strip()
    })

    batches = sorted({
        str(r.get("p3_batch") or "")
        for r in rows
        if str(r.get("p3_batch") or "")
    })

    reasons = sorted({
        str(r.get("p3_gate_reason") or r.get("p3_batch01_gate_reason") or "").strip()
        for r in rows
        if str(r.get("p3_gate_reason") or r.get("p3_batch01_gate_reason") or "").strip()
    })

    out["p3_consolidated_identity"] = canonical
    out["p3_consolidated_bucket"] = kind
    out["p3_aliases"] = aliases
    out["p3_duplicate_rows_collapsed"] = max(0, len(rows) - 1)
    out["p3_consolidated_reasons"] = reasons
    out["p3_source_rows_merged"] = len(rows)
    if batches:
        out["p3_batches"] = batches
    return out


def consolidate(kind: str) -> tuple[list[dict[str, Any]], int]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_count = 0

    for batch_no, path in enumerate(INPUTS[kind], start=1):
        for row in load_jsonl(path):
            raw_count += 1
            r = dict(row)
            r["p3_batch"] = batch_no
            key = identity(r)
            if not key:
                key = f"unknown:{batch_no}:{raw_count}"
            groups[key].append(r)

    consolidated = [
        merge_group(rows, canonical, kind)
        for canonical, rows in groups.items()
    ]
    consolidated.sort(key=lambda r: (
        -int((r.get("p3_validation", {}) or {}).get("candidate_link_count", 0) or 0),
        str(r.get("p3_consolidated_identity", "")),
    ))
    return consolidated, raw_count


def main() -> int:
    outputs: dict[str, list[dict[str, Any]]] = {}
    raw_counts: dict[str, int] = {}
    duplicate_collapses: dict[str, int] = {}

    for kind in KINDS:
        rows, raw_count = consolidate(kind)
        outputs[kind] = rows
        raw_counts[kind] = raw_count
        duplicate_collapses[kind] = raw_count - len(rows)
        write_jsonl(OUTPUTS[kind], rows)

    raw_total = sum(raw_counts.values())
    unique_total = sum(len(v) for v in outputs.values())

    summary = {
        "status": "ok",
        "p3_raw_rows_accounted": raw_total,
        "expected_p3_rows": 117,
        "raw_count_matches_expected": raw_total == 117,
        "raw_bucket_counts": raw_counts,
        "consolidated_bucket_counts": {
            k: len(v) for k, v in outputs.items()
        },
        "duplicates_collapsed_by_bucket": duplicate_collapses,
        "consolidated_total_rows": unique_total,
        "targeted_review_unique": len(outputs["targeted_review"]),
        "targeted_review_identities": [
            r.get("p3_consolidated_identity")
            for r in outputs["targeted_review"]
        ],
        "baseline_overlap_identities": [
            r.get("p3_consolidated_identity")
            for r in outputs["baseline_overlap"]
        ],
        "outputs": {k: str(v) for k, v in OUTPUTS.items()},
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if raw_total == 117 else 2


if __name__ == "__main__":
    raise SystemExit(main())
