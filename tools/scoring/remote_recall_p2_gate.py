#!/usr/bin/env python3
"""Gate Recall Expansion P2 sources after live validation.

Inputs:
- data/remote/recall_p2_validation_queue.jsonl
- data/remote/recall_p2_validation_results.jsonl
- data/remote/recall_p2_validation_unresolved.jsonl

Outputs:
- data/remote/recall_p2_profile_queue.jsonl
- data/remote/recall_p2_direct_review.jsonl
- data/remote/recall_p2_rejected_or_deferred.jsonl
- data/remote/recall_p2_gate_summary.json

This gate is intentionally conservative. P2 contains many newsletter,
tracking, redirect, content and technical-platform hosts. Nothing is promoted
to Golden here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]

QUEUE_PATH = ROOT_DIR / "data/remote/recall_p2_validation_queue.jsonl"
RESULTS_PATH = ROOT_DIR / "data/remote/recall_p2_validation_results.jsonl"
UNRESOLVED_PATH = ROOT_DIR / "data/remote/recall_p2_validation_unresolved.jsonl"

PROFILE_PATH = ROOT_DIR / "data/remote/recall_p2_profile_queue.jsonl"
DIRECT_PATH = ROOT_DIR / "data/remote/recall_p2_direct_review.jsonl"
REJECT_PATH = ROOT_DIR / "data/remote/recall_p2_rejected_or_deferred.jsonl"
SUMMARY_PATH = ROOT_DIR / "data/remote/recall_p2_gate_summary.json"

# Durable direct relationships that may matter even if they are not broad job boards.
DIRECT_RELATIONSHIP_HOSTS = {
    "bunq.com",
    "red-gate.com",
}

# Content/community surfaces that may help discovery/networking but should not
# be treated as direct candidate-source relationships without stronger evidence.
CONTENT_OR_COMMUNITY_HOSTS = {
    "mssqltips.com",
    "sqlservercentral.com",
    "blog.sqlauthority.com",
    "mcpmag.com",
    "redmondmag.com",
    "tldr.tech",
    "medium.com",
    "github.com",
    "w3.org",
    "sitepoint.com",
}

# Known infrastructure/transport/service hosts.
INFRA_SUFFIXES = (
    "licdn.com",
    "googleapis.com",
    "googleusercontent.com",
    "hubspotemail.net",
    "hs-sites.com",
    "user-subscription.com",
    "slgnt.eu",
    "emltrk.com",
    "emv3.com",
    "updatemyprofile.com",
)

INFRA_EXACT = {
    "bit.ly", "ow.ly", "hubs.li", "go.pardot.com",
    "subscribe.wordpress.com", "itunes.apple.com",
    "go.techtarget.com", "1105info.com", "info.1105edata.com",
    "go.1105.net", "info.101com.com",
    "keysurvey.co.uk",
}

TRANSPORT_PREFIXES = (
    "click.", "ct.", "edt.", "links.", "url", "go.", "info.", "media-exp",
)

CANDIDATE_TERMS = (
    "job", "jobs", "career", "careers", "hiring", "recruit",
    "recruitment", "talent", "vacanc", "apply", "contract",
    "freelance", "opportunit",
)

REMOTE_TERMS = (
    "remote", "work from home", "distributed", "anywhere",
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


def validation_index() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in (RESULTS_PATH, UNRESOLVED_PATH):
        for row in load_jsonl(path):
            key = str(row.get("validation_key", "") or row.get("canonical_host", "")).lower()
            if key:
                out[key] = row
    return out


def all_text(v: dict[str, Any]) -> str:
    parts = [
        str(v.get("page_title", "")),
        str(v.get("meta_description", "")),
        str(v.get("page_text_excerpt", "")),
        str(v.get("final_url", "")),
    ]
    for link in v.get("candidate_links", []) or []:
        if isinstance(link, dict):
            parts.extend([str(link.get("text", "")), str(link.get("url", ""))])
    return " ".join(parts).lower()


def is_infra(host: str) -> bool:
    if host in INFRA_EXACT:
        return True
    if any(host == suffix or host.endswith("." + suffix) for suffix in INFRA_SUFFIXES):
        return True
    if host.startswith(TRANSPORT_PREFIXES):
        return True
    return False


def classify(q: dict[str, Any], v: dict[str, Any] | None) -> tuple[str, str]:
    host = str(q.get("canonical_host", "")).lower()
    v = v or {}
    final_host = host_of(str(v.get("final_url", "") or ""))
    resolved = bool(v.get("resolved"))
    candidate_links = int(v.get("candidate_link_count", 0) or 0)
    remote_hits = len(v.get("remote_term_hits", []) or [])
    text = all_text(v)

    candidate_text_hits = sum(1 for t in CANDIDATE_TERMS if t in text)
    remote_text_hits = sum(1 for t in REMOTE_TERMS if t in text)

    # Preserve durable direct relationships for explicit review.
    if host in DIRECT_RELATIONSHIP_HOSTS or final_host in DIRECT_RELATIONSHIP_HOSTS:
        return "direct_review", "durable_direct_employer_or_technology_relationship"

    # Content/community sources can still be useful, but should not go through
    # broad-source profiling unless they show unusually strong candidate evidence.
    if host in CONTENT_OR_COMMUNITY_HOSTS or final_host in CONTENT_OR_COMMUNITY_HOSTS:
        if candidate_links >= 10 and candidate_text_hits >= 3:
            return "profile", "content_or_community_surface_with_strong_candidate_evidence"
        return "direct_review", "content_or_community_relationship_requires_manual_value_review"

    # Infrastructure/tracking hosts are not durable relationship identities.
    if is_infra(host) or (final_host and is_infra(final_host)):
        if candidate_links >= 10:
            return "direct_review", "infrastructure_host_with_candidate_links_requires_parent_resolution"
        return "reject_or_defer", "tracking_transport_or_infrastructure_host"

    # Unresolved P2 sources with no evidence stay preserved but do not advance.
    if not resolved:
        return "reject_or_defer", "unresolved_p2_without_durable_candidate_evidence"

    # Strong candidate-facing live evidence can still earn profiling.
    if candidate_links >= 10 and candidate_text_hits >= 2:
        return "profile", "strong_live_candidate_surface"

    if candidate_links >= 5 and (candidate_text_hits >= 3 or remote_hits + remote_text_hits >= 2):
        return "profile", "moderate_candidate_surface_with_remote_or_candidate_signal"

    # Generic platforms/redirectors/search/content with weak candidate signal.
    if candidate_links <= 2:
        return "reject_or_defer", "weak_or_generic_candidate_signal"

    return "direct_review", "ambiguous_p2_relationship_requires_manual_review"


def main() -> int:
    queue = load_jsonl(QUEUE_PATH)
    validation = validation_index()

    profile: list[dict[str, Any]] = []
    direct: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    missing: list[str] = []

    for row in queue:
        key = str(row.get("canonical_host", "")).lower()
        v = validation.get(key)
        if v is None:
            missing.append(key)

        bucket, reason = classify(row, v)
        out = dict(row)
        out["p2_gate_bucket"] = bucket
        out["p2_gate_reason"] = reason
        out["p2_validation"] = v or {}

        if bucket == "profile":
            profile.append(out)
        elif bucket == "direct_review":
            direct.append(out)
        else:
            rejected.append(out)

    def sort_key(r: dict[str, Any]) -> tuple[int, str]:
        v = r.get("p2_validation", {}) or {}
        return (
            -int(v.get("candidate_link_count", 0) or 0),
            str(r.get("recall_canonical_identity") or r.get("canonical_host") or ""),
        )

    profile.sort(key=sort_key)
    direct.sort(key=sort_key)
    rejected.sort(key=sort_key)

    write_jsonl(PROFILE_PATH, profile)
    write_jsonl(DIRECT_PATH, direct)
    write_jsonl(REJECT_PATH, rejected)

    summary = {
        "status": "ok",
        "p2_input_sources": len(queue),
        "validation_records_found": len(queue) - len(missing),
        "validation_records_missing": missing,
        "profile_queue_sources": len(profile),
        "direct_review_sources": len(direct),
        "rejected_or_deferred_sources": len(rejected),
        "accounted_sources": len(profile) + len(direct) + len(rejected),
        "profile_queue_output": str(PROFILE_PATH),
        "direct_review_output": str(DIRECT_PATH),
        "rejected_output": str(REJECT_PATH),
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
