#!/usr/bin/env python3
"""
remote_source_consolidator.py

Convert URL-level Remote discovery evidence into durable source candidates.

Primary entity:
    Candidate <-> Remote Source relationship

This stage DOES NOT try to identify individual jobs. It groups direct web
evidence by canonical host/root URL and preserves the underlying URLs as
provenance. Social posts and redirect/tracking wrappers remain individual
"discovery nodes" because resolving them may reveal a different underlying
source.

Inputs:
- data/remote/url_queue_ready.jsonl
- data/remote/url_queue_quarantine.jsonl

Outputs:
- data/remote/source_candidates.jsonl
- data/remote/discovery_nodes.jsonl
- data/remote/source_consolidation_summary.json
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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

TRACKING_HOST_TERMS = (
    "click.",
    "track.",
    "tracking.",
    "pixel.",
    "analytics.",
    "email.",
)

SOURCE_HINT_PATH_TERMS = (
    "career",
    "careers",
    "jobs",
    "job",
    "work-with-us",
    "join-us",
    "join-our-team",
    "opportunities",
    "vacancies",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
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


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def is_social_host(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in SOCIAL_BASE_DOMAINS)


def is_tracking_host(host: str) -> bool:
    return any(term in host for term in TRACKING_HOST_TERMS)


def canonical_host(host: str) -> str:
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and any(
        host[2:] == d or host[2:].endswith("." + d)
        for d in SOCIAL_BASE_DOMAINS
    ):
        host = host[2:]
    return host


def root_url_for(url: str) -> str:
    parsed = urlparse(url)
    host = canonical_host(parsed.hostname or "")
    if not host:
        return ""
    scheme = "https"
    return f"{scheme}://{host}/"


def source_hint_url(url: str) -> str | None:
    """
    Preserve a stable careers/jobs parent when one is clearly present.
    The canonical root remains the primary source URL.
    """
    parsed = urlparse(url)
    host = canonical_host(parsed.hostname or "")
    if not host:
        return None

    segments = [seg for seg in parsed.path.split("/") if seg]
    if not segments:
        return None

    first = segments[0].lower()
    if any(term == first for term in SOURCE_HINT_PATH_TERMS):
        return f"https://{host}/{segments[0]}/"

    if len(segments) >= 2:
        combined = "/".join(seg.lower() for seg in segments[:2])
        if any(term in combined for term in SOURCE_HINT_PATH_TERMS):
            return f"https://{host}/" + "/".join(segments[:2]) + "/"

    return None


def unique_nonempty(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def build_source_candidates(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    discovery_nodes: list[dict[str, Any]] = []

    for row in rows:
        url = str(row.get("normalized_url", "")).strip()
        if not url:
            continue

        host = host_of(url)
        if not host:
            continue

        reason = str(row.get("triage_reason", ""))

        # Social content and probable redirect wrappers are evidence nodes.
        # Resolving them may reveal a different underlying site/company.
        if is_social_host(host) or is_tracking_host(host) or reason == "possible_redirect_wrapper":
            node = dict(row)
            node["discovery_node_type"] = (
                "social" if is_social_host(host) else "redirect_wrapper"
            )
            node["pre_resolution_host"] = canonical_host(host)
            discovery_nodes.append(node)
            continue

        key = canonical_host(host)
        if key:
            groups[key].append(row)

    candidates: list[dict[str, Any]] = []

    for host, evidence_rows in groups.items():
        urls = unique_nonempty(
            row.get("normalized_url") for row in evidence_rows
        )
        message_ids = unique_nonempty(
            row.get("source_message_id") for row in evidence_rows
        )
        subjects = unique_nonempty(
            row.get("source_subject") for row in evidence_rows
        )
        senders = unique_nonempty(
            row.get("source_from") for row in evidence_rows
        )
        dates = sorted(unique_nonempty(
            row.get("email_datetime") for row in evidence_rows
        ))

        hints = unique_nonempty(source_hint_url(url) for url in urls)
        priorities = [
            int(row.get("triage_priority", 0) or 0)
            for row in evidence_rows
        ]

        representative_urls = sorted(
            urls,
            key=lambda value: (
                0 if source_hint_url(value) else 1,
                len(value),
                value,
            ),
        )[:10]

        candidates.append(
            {
                "canonical_host": host,
                "canonical_root_url": f"https://{host}/",
                "source_hint_urls": hints[:10],
                "evidence_url_count": len(urls),
                "email_evidence_count": len(message_ids),
                "first_seen": dates[0] if dates else "",
                "last_seen": dates[-1] if dates else "",
                "max_triage_priority": max(priorities) if priorities else 0,
                "representative_urls": representative_urls,
                "source_message_ids": message_ids,
                "source_subject_examples": subjects[:10],
                "source_sender_examples": senders[:10],
                "status": "source_candidate_pending_validation",
            }
        )

    candidates.sort(
        key=lambda row: (
            -int(row.get("email_evidence_count", 0)),
            -int(row.get("evidence_url_count", 0)),
            -int(row.get("max_triage_priority", 0)),
            str(row.get("canonical_host", "")),
        )
    )

    discovery_nodes.sort(
        key=lambda row: int(row.get("triage_priority", 0) or 0),
        reverse=True,
    )

    return candidates, discovery_nodes


def main() -> int:
    ready_path = Path(
        os.getenv("READY_JSONL", "data/remote/url_queue_ready.jsonl")
    ).resolve()
    quarantine_path = Path(
        os.getenv("QUARANTINE_JSONL", "data/remote/url_queue_quarantine.jsonl")
    ).resolve()
    candidates_path = Path(
        os.getenv("SOURCE_CANDIDATES_JSONL", "data/remote/source_candidates.jsonl")
    ).resolve()
    nodes_path = Path(
        os.getenv("DISCOVERY_NODES_JSONL", "data/remote/discovery_nodes.jsonl")
    ).resolve()
    summary_path = Path(
        os.getenv(
            "SOURCE_CONSOLIDATION_SUMMARY",
            "data/remote/source_consolidation_summary.json",
        )
    ).resolve()

    if not ready_path.exists():
        print(f"Fatal error: ready input not found: {ready_path}", file=sys.stderr)
        return 1

    try:
        ready_rows = load_jsonl(ready_path)
        quarantine_rows = load_jsonl(quarantine_path)

        # Quarantine is retained as evidence, but does not create a source
        # candidate by itself at this stage.
        candidates, discovery_nodes = build_source_candidates(ready_rows)

        write_jsonl(candidates_path, candidates)
        write_jsonl(nodes_path, discovery_nodes)

        summary = {
            "status": "ok",
            "ready_input_rows": len(ready_rows),
            "quarantine_retained_rows": len(quarantine_rows),
            "direct_source_candidates": len(candidates),
            "discovery_nodes_to_resolve": len(discovery_nodes),
            "direct_candidate_evidence_urls": sum(
                int(row.get("evidence_url_count", 0)) for row in candidates
            ),
            "accounted_ready_rows": (
                sum(int(row.get("evidence_url_count", 0)) for row in candidates)
                + len(discovery_nodes)
            ),
            "source_candidates_output": str(candidates_path),
            "discovery_nodes_output": str(nodes_path),
            "quarantine_output": str(quarantine_path),
        }

        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        print(json.dumps(summary, ensure_ascii=False))
        return 0

    except Exception as exc:
        print(f"Fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
