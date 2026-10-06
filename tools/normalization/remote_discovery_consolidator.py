#!/usr/bin/env python3
"""
remote_discovery_consolidator.py

Collapse duplicate discovery-node variants before browser resolution while
preserving every underlying evidence relationship.

Examples:
- Multiple LinkedIn newsletter CTA variants pointing to the same article
- The same social post discovered in several emails
- Tracking-parameter variants of the same social content URL

Input:
- data/remote/discovery_nodes.jsonl

Outputs:
- data/remote/discovery_nodes_consolidated.jsonl
- data/remote/discovery_node_evidence.jsonl

The consolidated file is what the browser resolver should process.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = Path(
    os.getenv("REMOTE_DISCOVERY_NODES", ROOT_DIR / "data/remote/discovery_nodes.jsonl")
)
OUTPUT_PATH = Path(
    os.getenv(
        "REMOTE_DISCOVERY_NODES_CONSOLIDATED",
        ROOT_DIR / "data/remote/discovery_nodes_consolidated.jsonl",
    )
)
EVIDENCE_PATH = Path(
    os.getenv(
        "REMOTE_DISCOVERY_NODE_EVIDENCE",
        ROOT_DIR / "data/remote/discovery_node_evidence.jsonl",
    )
)

DROP_QUERY_PREFIXES = (
    "utm_",
    "trk",
)
DROP_QUERY_NAMES = {
    "midtoken", "midsig", "eid", "lipi", "li_fat_id", "fbclid", "gclid",
    "igsh", "igshid", "mibextid", "mc_cid", "mc_eid", "si", "feature",
}

SOCIAL_BASE_DOMAINS = (
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "fb.watch",
    "linkedin.com",
    "youtube.com",
    "youtu.be",
    "x.com",
    "twitter.com",
    "pinterest.com",
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


def canonical_host(host: str) -> str:
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        possible = host[2:]
        if possible in SOCIAL_BASE_DOMAINS:
            host = possible
    return host


def canonical_discovery_url(url: str) -> str:
    parsed = urlparse(url)
    host = canonical_host(parsed.hostname or "")
    if not host:
        return url.strip()

    scheme = "https"
    path = parsed.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if path != "/":
        path = path.rstrip("/")

    # For most social content, query parameters are transport/tracking noise.
    # YouTube watch URLs are the exception: v identifies the content.
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower = key.lower()
        if lower in DROP_QUERY_NAMES or any(lower.startswith(p) for p in DROP_QUERY_PREFIXES):
            continue

        if host in {"youtube.com", "youtu.be"}:
            if lower in {"v", "list"}:
                kept.append((key, value))
        elif host in SOCIAL_BASE_DOMAINS:
            continue
        else:
            # For non-social redirect wrappers keep unknown params because
            # they may contain the destination identifier.
            kept.append((key, value))

    query = urlencode(kept, doseq=True)
    return urlunparse((scheme, host, path, "", query, ""))


def unique(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def main() -> int:
    if not INPUT_PATH.exists():
        print(f"Fatal error: input not found: {INPUT_PATH}", file=sys.stderr)
        return 1

    rows = load_jsonl(INPUT_PATH)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        raw = str(row.get("normalized_url", "")).strip()
        if not raw:
            continue
        groups[canonical_discovery_url(raw)].append(row)

    consolidated: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []

    for canonical_url, members in groups.items():
        priorities = [int(r.get("triage_priority", 0) or 0) for r in members]
        message_ids = unique(r.get("source_message_id") for r in members)
        subjects = unique(r.get("source_subject") for r in members)
        senders = unique(r.get("source_from") for r in members)
        original_urls = unique(r.get("normalized_url") for r in members)
        dates = sorted(unique(r.get("email_datetime") for r in members))

        first = dict(members[0])
        first["normalized_url"] = canonical_url
        first["canonical_discovery_url"] = canonical_url
        first["evidence_variant_count"] = len(original_urls)
        first["email_evidence_count"] = len(message_ids)
        first["source_message_ids"] = message_ids
        first["source_subject_examples"] = subjects[:10]
        first["source_sender_examples"] = senders[:10]
        first["first_seen"] = dates[0] if dates else ""
        first["last_seen"] = dates[-1] if dates else ""
        first["triage_priority"] = max(priorities) if priorities else 0
        consolidated.append(first)

        for member in members:
            evidence_rows.append({
                "canonical_discovery_url": canonical_url,
                "source_message_id": member.get("source_message_id"),
                "source_subject": member.get("source_subject"),
                "source_from": member.get("source_from"),
                "email_datetime": member.get("email_datetime"),
                "original_url": member.get("original_url"),
                "normalized_url": member.get("normalized_url"),
                "discovery_node_type": member.get("discovery_node_type"),
            })

    consolidated.sort(
        key=lambda r: (
            -int(r.get("email_evidence_count", 0) or 0),
            -int(r.get("evidence_variant_count", 0) or 0),
            -int(r.get("triage_priority", 0) or 0),
            str(r.get("normalized_url", "")),
        )
    )

    write_jsonl(OUTPUT_PATH, consolidated)
    write_jsonl(EVIDENCE_PATH, evidence_rows)

    print(json.dumps({
        "status": "ok",
        "input_discovery_nodes": len(rows),
        "consolidated_discovery_nodes": len(consolidated),
        "duplicate_variants_collapsed": len(rows) - len(consolidated),
        "evidence_rows_preserved": len(evidence_rows),
        "output": str(OUTPUT_PATH),
        "evidence_output": str(EVIDENCE_PATH),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
