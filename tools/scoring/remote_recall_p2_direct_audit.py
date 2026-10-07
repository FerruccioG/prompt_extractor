#!/usr/bin/env python3
"""Audit Recall Expansion P2 direct-review relationships.

Inputs:
- data/remote/recall_p2_direct_review.jsonl

Outputs:
- data/remote/recall_p2_direct_audit.jsonl
- data/remote/recall_p2_direct_targeted_profile.jsonl
- data/remote/recall_p2_direct_indirect_keep.jsonl
- data/remote/recall_p2_direct_reject.jsonl
- data/remote/recall_p2_direct_audit_summary.json

This stage does not touch Golden Excel.  It separates durable direct/company
relationships from useful-but-indirect content/community sources and from
transport/noise that survived the conservative gate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = ROOT_DIR / "data/remote/recall_p2_direct_review.jsonl"
AUDIT_PATH = ROOT_DIR / "data/remote/recall_p2_direct_audit.jsonl"
TARGET_PATH = ROOT_DIR / "data/remote/recall_p2_direct_targeted_profile.jsonl"
INDIRECT_PATH = ROOT_DIR / "data/remote/recall_p2_direct_indirect_keep.jsonl"
REJECT_PATH = ROOT_DIR / "data/remote/recall_p2_direct_reject.jsonl"
SUMMARY_PATH = ROOT_DIR / "data/remote/recall_p2_direct_audit_summary.json"

DIRECT_TARGETS = {
    "bunq.com": "direct_employer",
    "red-gate.com": "technology_employer",
}

INDIRECT_KEEP = {
    "mssqltips.com": "sql_technical_community",
    "sqlservercentral.com": "sql_technical_community",
    "blog.sqlauthority.com": "sql_technical_content",
    "mcpmag.com": "microsoft_technical_content",
    "redmondmag.com": "microsoft_technical_content",
    "tldr.tech": "technology_newsletter",
    "medium.com": "generic_content_platform",
    "github.com": "developer_ecosystem",
    "w3.org": "standards_ecosystem",
    "sitepoint.com": "developer_content_community",
}


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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def host_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def classify(row: dict[str, Any]) -> tuple[str, str]:
    host = str(row.get("canonical_host", "")).lower()
    v = row.get("p2_validation", {}) or {}
    final_host = host_of(str(v.get("final_url", "") or ""))

    if host in DIRECT_TARGETS:
        return "targeted_profile", DIRECT_TARGETS[host]
    if final_host in DIRECT_TARGETS:
        return "targeted_profile", DIRECT_TARGETS[final_host]

    if host in INDIRECT_KEEP:
        return "indirect_keep", INDIRECT_KEEP[host]
    if final_host in INDIRECT_KEEP:
        return "indirect_keep", INDIRECT_KEEP[final_host]

    candidate_links = int(v.get("candidate_link_count", 0) or 0)
    resolved = bool(v.get("resolved"))

    if resolved and candidate_links >= 5:
        return "targeted_profile", "ambiguous_but_candidate_facing_enough_for_targeted_profile"

    if resolved:
        return "indirect_keep", "resolved_indirect_relationship_but_not_golden_candidate_source"

    return "reject", "unresolved_or_non_durable_direct_review_residue"


def main() -> int:
    rows = load_jsonl(INPUT_PATH)

    audited: list[dict[str, Any]] = []
    targeted: list[dict[str, Any]] = []
    indirect: list[dict[str, Any]] = []
    reject: list[dict[str, Any]] = []

    for row in rows:
        decision, reason = classify(row)
        out = dict(row)
        out["p2_direct_audit_decision"] = decision
        out["p2_direct_audit_reason"] = reason
        audited.append(out)

        if decision == "targeted_profile":
            targeted.append(out)
        elif decision == "indirect_keep":
            indirect.append(out)
        else:
            reject.append(out)

    def key(r: dict[str, Any]) -> str:
        return str(r.get("recall_canonical_identity") or r.get("canonical_host") or "")

    audited.sort(key=key)
    targeted.sort(key=key)
    indirect.sort(key=key)
    reject.sort(key=key)

    write_jsonl(AUDIT_PATH, audited)
    write_jsonl(TARGET_PATH, targeted)
    write_jsonl(INDIRECT_PATH, indirect)
    write_jsonl(REJECT_PATH, reject)

    summary = {
        "status": "ok",
        "direct_review_input": len(rows),
        "targeted_profile": len(targeted),
        "indirect_keep": len(indirect),
        "reject": len(reject),
        "accounted": len(targeted) + len(indirect) + len(reject),
        "targeted_profile_sources": [key(r) for r in targeted],
        "indirect_keep_sources": [key(r) for r in indirect],
        "reject_sources": [key(r) for r in reject],
        "targeted_profile_output": str(TARGET_PATH),
        "indirect_keep_output": str(INDIRECT_PATH),
        "reject_output": str(REJECT_PATH),
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
