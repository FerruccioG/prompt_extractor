#!/usr/bin/env python3
"""
remote_source_merger.py

Merge direct source candidates with source roots discovered by resolving
social/redirect discovery nodes.

Inputs:
- data/remote/source_candidates.jsonl
- data/remote/discovery_resolution_results.jsonl

Output:
- data/remote/source_candidates_merged.jsonl

This is still pre-validation: it creates the durable source universe that the
later source validator/scorer will research before Excel publication.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]

DIRECT_PATH = Path(
    os.getenv("REMOTE_DIRECT_SOURCES", ROOT_DIR / "data/remote/source_candidates.jsonl")
)
RESOLVED_PATH = Path(
    os.getenv(
        "REMOTE_DISCOVERY_RESULTS",
        ROOT_DIR / "data/remote/discovery_resolution_results.jsonl",
    )
)
OUTPUT_PATH = Path(
    os.getenv(
        "REMOTE_MERGED_SOURCES",
        ROOT_DIR / "data/remote/source_candidates_merged.jsonl",
    )
)


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


def unique(values) -> list[str]:
    seen = set()
    out = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def main() -> int:
    direct = load_jsonl(DIRECT_PATH)
    resolved = load_jsonl(RESOLVED_PATH)

    groups: dict[str, dict[str, Any]] = {}

    for row in direct:
        host = str(row.get("canonical_host", "")).strip().lower()
        if host:
            groups[host] = dict(row)
            groups[host]["discovery_resolution_evidence_count"] = 0
            groups[host]["discovery_evidence_urls"] = []

    for row in resolved:
        source_node_url = str(row.get("normalized_url", "")).strip()
        for root in row.get("canonical_source_roots", []) or []:
            host = host_of(root)
            if not host:
                continue

            if host not in groups:
                groups[host] = {
                    "canonical_host": host,
                    "canonical_root_url": f"https://{host}/",
                    "source_hint_urls": [],
                    "evidence_url_count": 0,
                    "email_evidence_count": 0,
                    "first_seen": "",
                    "last_seen": "",
                    "max_triage_priority": int(row.get("triage_priority", 0) or 0),
                    "representative_urls": [],
                    "source_message_ids": [],
                    "source_subject_examples": [],
                    "source_sender_examples": [],
                    "status": "source_candidate_pending_validation",
                    "discovery_resolution_evidence_count": 0,
                    "discovery_evidence_urls": [],
                }

            target = groups[host]
            target["discovery_resolution_evidence_count"] = (
                int(target.get("discovery_resolution_evidence_count", 0)) + 1
            )
            target["discovery_evidence_urls"] = unique(
                list(target.get("discovery_evidence_urls", [])) + [source_node_url]
            )
            target["source_message_ids"] = unique(
                list(target.get("source_message_ids", []))
                + [row.get("source_message_id")]
            )
            target["email_evidence_count"] = len(target["source_message_ids"])
            target["source_subject_examples"] = unique(
                list(target.get("source_subject_examples", []))
                + [row.get("source_subject")]
            )[:10]
            target["source_sender_examples"] = unique(
                list(target.get("source_sender_examples", []))
                + [row.get("source_from")]
            )[:10]
            target["max_triage_priority"] = max(
                int(target.get("max_triage_priority", 0) or 0),
                int(row.get("triage_priority", 0) or 0),
            )

    merged = list(groups.values())
    merged.sort(
        key=lambda row: (
            -int(row.get("email_evidence_count", 0) or 0),
            -int(row.get("discovery_resolution_evidence_count", 0) or 0),
            -int(row.get("evidence_url_count", 0) or 0),
            str(row.get("canonical_host", "")),
        )
    )

    write_jsonl(OUTPUT_PATH, merged)

    print(json.dumps({
        "status": "ok",
        "direct_sources": len(direct),
        "resolved_discovery_rows": len(resolved),
        "merged_unique_sources": len(merged),
        "output": str(OUTPUT_PATH),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
