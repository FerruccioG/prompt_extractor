#!/usr/bin/env python3
"""Gate Recall Expansion P1 sources into reproducible next actions.

Inputs:
- data/remote/recall_expansion_queue.jsonl
- data/remote/recall_p1_validation_results.jsonl
- data/remote/recall_p1_validation_unresolved.jsonl

Outputs:
- data/remote/recall_p1_profile_queue.jsonl
- data/remote/recall_p1_direct_review.jsonl
- data/remote/recall_p1_rejected_or_deferred.jsonl
- data/remote/recall_p1_gate_summary.json

The gate does not modify the Golden workbook. It only decides which P1
relationships should proceed to candidate-specific profiling, which should be
reviewed as direct-employer/ecosystem relationships, and which should be
rejected/deferred as weak or non-candidate sources.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]

QUEUE_PATH = Path(os.getenv(
    "REMOTE_RECALL_EXPANSION_QUEUE",
    ROOT_DIR / "data/remote/recall_expansion_queue.jsonl",
))
RESULTS_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_VALIDATION_RESULTS",
    ROOT_DIR / "data/remote/recall_p1_validation_results.jsonl",
))
UNRESOLVED_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_VALIDATION_UNRESOLVED",
    ROOT_DIR / "data/remote/recall_p1_validation_unresolved.jsonl",
))

PROFILE_QUEUE_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_PROFILE_QUEUE",
    ROOT_DIR / "data/remote/recall_p1_profile_queue.jsonl",
))
DIRECT_REVIEW_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_DIRECT_REVIEW",
    ROOT_DIR / "data/remote/recall_p1_direct_review.jsonl",
))
REJECTED_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_REJECTED",
    ROOT_DIR / "data/remote/recall_p1_rejected_or_deferred.jsonl",
))
SUMMARY_PATH = Path(os.getenv(
    "REMOTE_RECALL_P1_GATE_SUMMARY",
    ROOT_DIR / "data/remote/recall_p1_gate_summary.json",
))

# Durable candidate-facing source types that should proceed to profiling even
# when a crawler is blocked or page text is sparse.
PROFILE_FIRST = {
    "linkedin.com",
    "builtin.com",
    "ycombinator.com",
    "jobicy.com",
    "himalayas.app",
    "a.team",
    "braintrust.com",
    "catalant.com",
    "contra.com",
    "dice.com",
    "malt.com",
    "workingnomads.com",
    "openjobseu.com",
}

# Direct employers / ecosystems can be valuable Candidate <-> Source
# relationships but should not be scored as if they were broad job boards.
DIRECT_REVIEW = {
    "aegon.ie",
    "athora.com",
    "bordgaisenergy.ie",
    "canadalife.ie",
    "tcd.ie",
    "cloudflare.com",
    "bfgl.com",
}

# Known non-candidate, content-only, infrastructure, expired or generic
# surfaces encountered in this P1 batch.
REJECT_OR_DEFER = {
    "learningsolutionsmag.com",
    "serviceframe.com",
    "hugedomains.com",
    "bing.com",
    "about.meta.com",
    "meta.ai",
    "muse.ai",
    "threads.com",
    "policies.google.com",
}

CANDIDATE_MARKERS = (
    "job", "jobs", "career", "careers", "vacanc", "hiring",
    "recruit", "talent", "freelance", "contract", "apply",
)

CONTENT_OR_GENERIC_MARKERS = (
    "news", "magazine", "blog", "policy", "policies", "login",
    "search engine", "domain for sale", "hugedomains",
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


def host_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def validation_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in (RESULTS_PATH, UNRESOLVED_PATH):
        for row in load_jsonl(path):
            key = str(row.get("validation_key", "") or row.get("canonical_host", "")).lower()
            if key:
                index[key] = row
    return index


def text_blob(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("page_title", "")),
        str(row.get("meta_description", "")),
        str(row.get("page_text_excerpt", "")),
        str(row.get("final_url", "")),
    ]
    for link in row.get("candidate_links", []) or []:
        if isinstance(link, dict):
            parts.extend([str(link.get("text", "")), str(link.get("url", ""))])
    return " ".join(parts).lower()


def classify(queue_row: dict[str, Any], validation: dict[str, Any] | None) -> tuple[str, str]:
    source = str(queue_row.get("recall_canonical_identity") or queue_row.get("canonical_host") or "").lower()
    validation = validation or {}
    final_host = host_of(str(validation.get("final_url", "") or ""))

    # Exact durable-source rules take precedence.
    if source in PROFILE_FIRST:
        return "profile", "known_candidate_facing_or_manual_seed_source"

    if source in DIRECT_REVIEW or final_host in DIRECT_REVIEW:
        return "direct_review", "direct_employer_or_ecosystem_relationship"

    if source in REJECT_OR_DEFER or final_host in REJECT_OR_DEFER:
        return "reject_or_defer", "known_non_candidate_content_generic_or_expired_surface"

    # Generic evidence fallback keeps this gate reusable beyond the current 28.
    candidate_links = int(validation.get("candidate_link_count", 0) or 0)
    resolved = bool(validation.get("resolved"))
    text = text_blob(validation)
    candidate_hits = sum(1 for marker in CANDIDATE_MARKERS if marker in text)
    generic_hits = sum(1 for marker in CONTENT_OR_GENERIC_MARKERS if marker in text)

    if candidate_links >= 10 or candidate_hits >= 3:
        return "profile", "strong_live_candidate_surface"

    if not resolved:
        return "direct_review", "unresolved_p1_requires_manual_source_type_review"

    if generic_hits >= 2 and candidate_links <= 2:
        return "reject_or_defer", "generic_or_content_surface_with_weak_candidate_signal"

    if candidate_links <= 2:
        return "direct_review", "low_density_p1_requires_relationship_review"

    return "profile", "candidate_surface_requires_profile"


def main() -> int:
    if not QUEUE_PATH.exists():
        raise SystemExit(f"Input not found: {QUEUE_PATH}")

    queue = [
        row for row in load_jsonl(QUEUE_PATH)
        if str(row.get("recall_priority", "")) == "P1"
    ]
    validation = validation_index()

    profile: list[dict[str, Any]] = []
    direct: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    validation_missing: list[str] = []

    for row in queue:
        key = str(row.get("canonical_host", "")).lower()
        v = validation.get(key)
        if v is None:
            validation_missing.append(key)

        bucket, reason = classify(row, v)
        out = dict(row)
        out["p1_gate_bucket"] = bucket
        out["p1_gate_reason"] = reason
        out["p1_validation"] = v or {}

        if bucket == "profile":
            profile.append(out)
        elif bucket == "direct_review":
            direct.append(out)
        else:
            rejected.append(out)

    def sort_key(row: dict[str, Any]) -> tuple[int, str]:
        v = row.get("p1_validation", {}) or {}
        return (
            -int(v.get("candidate_link_count", 0) or 0),
            str(row.get("recall_canonical_identity") or row.get("canonical_host") or ""),
        )

    profile.sort(key=sort_key)
    direct.sort(key=sort_key)
    rejected.sort(key=sort_key)

    write_jsonl(PROFILE_QUEUE_PATH, profile)
    write_jsonl(DIRECT_REVIEW_PATH, direct)
    write_jsonl(REJECTED_PATH, rejected)

    summary = {
        "status": "ok",
        "p1_input_sources": len(queue),
        "validation_records_found": len(queue) - len(validation_missing),
        "validation_records_missing": validation_missing,
        "profile_queue_sources": len(profile),
        "direct_review_sources": len(direct),
        "rejected_or_deferred_sources": len(rejected),
        "accounted_sources": len(profile) + len(direct) + len(rejected),
        "profile_queue_output": str(PROFILE_QUEUE_PATH),
        "direct_review_output": str(DIRECT_REVIEW_PATH),
        "rejected_output": str(REJECTED_PATH),
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
