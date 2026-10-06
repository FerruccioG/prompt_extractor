#!/usr/bin/env python3
"""
remote_discovery_consolidator.py

Collapse duplicate discovery-node variants before browser resolution while
preserving every underlying evidence relationship.

The stage is intentionally high-recall:
- content-bearing / redirect nodes go to the resolver queue
- obvious social-platform utility/boilerplate pages are retained separately
  as platform evidence, never discarded
- every original evidence row is preserved

Inputs:
- data/remote/discovery_nodes.jsonl

Outputs:
- data/remote/discovery_nodes_consolidated.jsonl
- data/remote/discovery_nodes_platform_evidence.jsonl
- data/remote/discovery_node_evidence.jsonl
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
PLATFORM_EVIDENCE_PATH = Path(
    os.getenv(
        "REMOTE_DISCOVERY_PLATFORM_EVIDENCE",
        ROOT_DIR / "data/remote/discovery_nodes_platform_evidence.jsonl",
    )
)
EVIDENCE_PATH = Path(
    os.getenv(
        "REMOTE_DISCOVERY_NODE_EVIDENCE",
        ROOT_DIR / "data/remote/discovery_node_evidence.jsonl",
    )
)

DROP_QUERY_PREFIXES = ("utm_", "trk")
DROP_QUERY_NAMES = {
    "midtoken", "midsig", "eid", "lipi", "li_fat_id", "fbclid", "gclid",
    "igsh", "igshid", "mibextid", "mc_cid", "mc_eid", "si", "feature",
}

SOCIAL_BASE_DOMAINS = (
    "instagram.com", "tiktok.com", "facebook.com", "fb.watch",
    "linkedin.com", "youtube.com", "youtu.be", "x.com", "twitter.com",
    "pinterest.com",
)

# These are transport/account/navigation surfaces rather than content
# destinations. They are preserved as platform evidence but do not justify a
# browser visit during source discovery.
LINKEDIN_UTILITY_PREFIXES = (
    "/help/",
    "/login",
    "/uas/login",
    "/psettings/",
    "/mypreferences/",
    "/comm/feed",
    "/comm/messaging",
    "/comm/mynetwork",
    "/comm/notifications",
    "/comm/jobs/",
    "/jobs/",
    "/comm/psettings/",
    "/comm/dms/",
    "/e/v2",
)

GENERIC_UTILITY_TERMS = (
    "/login", "/signin", "/sign-in", "/logout", "/settings", "/preferences",
    "/privacy", "/terms", "/legal", "/help", "/support", "/account",
    "/unsubscribe",
)

CONTENT_PATH_TERMS = (
    "/pulse/", "/posts/", "/feed/update/", "/jobs/view/", "/job/",
    "/jobs/", "/reel/", "/reels/", "/p/", "/video/", "/watch",
    "/shorts/", "/status/", "/pin/",
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

    path = parsed.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if path != "/":
        path = path.rstrip("/")

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
            # Redirect wrappers may require opaque parameters to reach the
            # underlying destination, so preserve unknown parameters.
            kept.append((key, value))

    return urlunparse(("https", host, path, "", urlencode(kept, doseq=True), ""))


def unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def resolution_class(url: str, node_type: str) -> tuple[str, int, str]:
    """
    Return (class, content_score, reason).

    platform_evidence means "preserve, but don't spend a browser visit".
    resolve means "worth attempting to resolve/render".
    """
    parsed = urlparse(url)
    host = canonical_host(parsed.hostname or "")
    path = (parsed.path or "/").lower()

    if node_type == "redirect_wrapper":
        return "resolve", 90, "redirect_wrapper_may_reveal_source"

    if host == "linkedin.com" and any(path.startswith(p) for p in LINKEDIN_UTILITY_PREFIXES):
        return "platform_evidence", 5, "linkedin_platform_or_job_surface"

    if any(term in path for term in CONTENT_PATH_TERMS):
        return "resolve", 95, "content_bearing_social_path"

    if any(term in path for term in GENERIC_UTILITY_TERMS):
        return "platform_evidence", 10, "generic_utility_or_account_surface"

    if host in SOCIAL_BASE_DOMAINS:
        # Unknown social paths are still resolved rather than discarded.
        return "resolve", 60, "ambiguous_social_path_retained"

    return "resolve", 70, "non_social_discovery_node"


def main() -> int:
    if not INPUT_PATH.exists():
        print(f"Fatal error: input not found: {INPUT_PATH}", file=sys.stderr)
        return 1

    rows = load_jsonl(INPUT_PATH)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        raw = str(row.get("normalized_url", "")).strip()
        if raw:
            groups[canonical_discovery_url(raw)].append(row)

    resolve_rows: list[dict[str, Any]] = []
    platform_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []

    for canonical_url, members in groups.items():
        priorities = [int(r.get("triage_priority", 0) or 0) for r in members]
        message_ids = unique(r.get("source_message_id") for r in members)
        subjects = unique(r.get("source_subject") for r in members)
        senders = unique(r.get("source_from") for r in members)
        original_urls = unique(r.get("normalized_url") for r in members)
        dates = sorted(unique(r.get("email_datetime") for r in members))
        node_types = unique(r.get("discovery_node_type") for r in members)
        node_type = node_types[0] if node_types else ""

        bucket, content_score, class_reason = resolution_class(canonical_url, node_type)

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
        first["resolution_class"] = bucket
        first["content_score"] = content_score
        first["resolution_class_reason"] = class_reason

        if bucket == "platform_evidence":
            platform_rows.append(first)
        else:
            resolve_rows.append(first)

        for member in members:
            evidence_rows.append({
                "canonical_discovery_url": canonical_url,
                "resolution_class": bucket,
                "source_message_id": member.get("source_message_id"),
                "source_subject": member.get("source_subject"),
                "source_from": member.get("source_from"),
                "email_datetime": member.get("email_datetime"),
                "original_url": member.get("original_url"),
                "normalized_url": member.get("normalized_url"),
                "discovery_node_type": member.get("discovery_node_type"),
            })

    resolve_rows.sort(
        key=lambda r: (
            -int(r.get("content_score", 0) or 0),
            -int(r.get("email_evidence_count", 0) or 0),
            -int(r.get("evidence_variant_count", 0) or 0),
            -int(r.get("triage_priority", 0) or 0),
            str(r.get("normalized_url", "")),
        )
    )
    platform_rows.sort(
        key=lambda r: (
            -int(r.get("email_evidence_count", 0) or 0),
            -int(r.get("evidence_variant_count", 0) or 0),
            str(r.get("normalized_url", "")),
        )
    )

    write_jsonl(OUTPUT_PATH, resolve_rows)
    write_jsonl(PLATFORM_EVIDENCE_PATH, platform_rows)
    write_jsonl(EVIDENCE_PATH, evidence_rows)

    print(json.dumps({
        "status": "ok",
        "input_discovery_nodes": len(rows),
        "canonical_groups": len(groups),
        "resolver_queue_nodes": len(resolve_rows),
        "platform_evidence_nodes": len(platform_rows),
        "duplicate_variants_collapsed": len(rows) - len(groups),
        "evidence_rows_preserved": len(evidence_rows),
        "accounted_canonical_groups": len(resolve_rows) + len(platform_rows),
        "output": str(OUTPUT_PATH),
        "platform_evidence_output": str(PLATFORM_EVIDENCE_PATH),
        "evidence_output": str(EVIDENCE_PATH),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
