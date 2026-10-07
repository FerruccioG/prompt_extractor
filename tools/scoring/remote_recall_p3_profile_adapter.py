#!/usr/bin/env python3
"""Build scorer-compatible relationship rows for consolidated Recall P3 survivors.

Inputs:
- data/remote/recall_p3_targeted_review_consolidated.jsonl
- validation result/unresolved files from all four P3 batches

Output:
- data/remote/recall_p3_profile_relationships.jsonl

This adapter preserves consolidated identities/aliases and reuses the existing
candidate-specific profiler without touching Golden artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

PROFILE_QUEUE_PATH = ROOT_DIR / "data/remote/recall_p3_targeted_review_consolidated.jsonl"
OUTPUT_PATH = ROOT_DIR / "data/remote/recall_p3_profile_relationships.jsonl"

VALIDATION_PATHS = []
for batch in range(1, 5):
    VALIDATION_PATHS.extend([
        ROOT_DIR / f"data/remote/recall_p3_batch{batch:02d}_validation_results.jsonl",
        ROOT_DIR / f"data/remote/recall_p3_batch{batch:02d}_validation_unresolved.jsonl",
    ])

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
        for line_no, raw in enumerate(fh, start=1):
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
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def canon(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("www."):
        raw = raw[4:]
    return HOST_ALIASES.get(raw, raw)


def validation_index() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for path in VALIDATION_PATHS:
        for row in load_jsonl(path):
            raw_key = str(row.get("validation_key", "") or row.get("canonical_host", "")).lower()
            key = canon(raw_key)
            if key:
                out.setdefault(key, []).append(row)
            # Also retain exact raw identity because consolidated aliases may
            # point back to the original batch key.
            if raw_key and raw_key != key:
                out.setdefault(raw_key, []).append(row)
    return out


def strongest_validation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    def strength(v: dict[str, Any]) -> tuple[int, int, int]:
        return (
            1 if v.get("resolved") else 0,
            int(v.get("candidate_link_count", 0) or 0),
            len(v.get("remote_term_hits", []) or []),
        )
    return max(rows, key=strength)


def main() -> int:
    queue = load_jsonl(PROFILE_QUEUE_PATH)
    validation = validation_index()

    output: list[dict[str, Any]] = []
    missing_validation: list[str] = []

    for row in queue:
        source = canon(str(row.get("p3_consolidated_identity") or ""))
        aliases = [canon(a) for a in (row.get("p3_aliases", []) or []) if a]

        candidates: list[dict[str, Any]] = []
        for key in [source, *aliases]:
            candidates.extend(validation.get(key, []))
        # Include validation embedded by the gate/consolidator if present.
        embedded = row.get("p3_validation")
        if isinstance(embedded, dict) and embedded:
            candidates.append(embedded)

        v = strongest_validation(candidates)
        if not v:
            missing_validation.append(source)

        resolved = bool(v.get("resolved"))
        final_url = str(v.get("final_url", "") or "")
        root_url = str(row.get("canonical_root_url", "") or "")
        candidate_links = int(v.get("candidate_link_count", 0) or 0)

        generic_score = 50
        generic_score += min(20, candidate_links // 3)
        generic_score += min(10, len(v.get("candidate_term_hits", []) or []) * 2)
        generic_score += min(10, len(v.get("remote_term_hits", []) or []) * 4)
        if resolved:
            generic_score += 5
        generic_score = max(0, min(100, generic_score))

        output.append({
            "Source": source,
            "URL": root_url or (f"https://{source}/" if source else ""),
            "Original Host": str(row.get("canonical_host", "") or ""),
            "Aliases": row.get("p3_aliases", []),
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
            "Recommended Use": "Recall Expansion P3 survivor: profile before Golden comparison",
            "Scoring Reasons": [
                "recall_expansion_p3",
                *list(row.get("p3_consolidated_reasons", []) or []),
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
        "validation_records_missing": sorted(set(missing_validation)),
        "output": str(OUTPUT_PATH),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
