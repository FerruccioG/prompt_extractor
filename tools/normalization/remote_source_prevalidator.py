#!/usr/bin/env python3
"""
remote_source_prevalidator.py

High-recall pre-validation for Candidate <-> Remote Source relationships.

Purpose:
- Remove obvious infrastructure/tracking/ad/consent hosts from expensive source research.
- Separate semantically weak/ambiguous sources for later review.
- Preserve every merged source in exactly one bucket.

Input:
- data/remote/source_candidates_merged.jsonl

Outputs:
- data/remote/source_validation_queue.jsonl
- data/remote/source_semantic_review.jsonl
- data/remote/source_infrastructure_noise.jsonl
- data/remote/source_prevalidation_summary.json

This stage is intentionally conservative. It does not decide final Golden Excel
membership. It only prevents known technical noise from being mistaken for a
remote-work source while retaining ambiguous sources for later analysis.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = Path(
    os.getenv(
        "REMOTE_MERGED_SOURCES",
        ROOT_DIR / "data/remote/source_candidates_merged.jsonl",
    )
)
QUEUE_PATH = Path(
    os.getenv(
        "REMOTE_SOURCE_VALIDATION_QUEUE",
        ROOT_DIR / "data/remote/source_validation_queue.jsonl",
    )
)
REVIEW_PATH = Path(
    os.getenv(
        "REMOTE_SOURCE_SEMANTIC_REVIEW",
        ROOT_DIR / "data/remote/source_semantic_review.jsonl",
    )
)
NOISE_PATH = Path(
    os.getenv(
        "REMOTE_SOURCE_INFRASTRUCTURE_NOISE",
        ROOT_DIR / "data/remote/source_infrastructure_noise.jsonl",
    )
)
SUMMARY_PATH = Path(
    os.getenv(
        "REMOTE_SOURCE_PREVALIDATION_SUMMARY",
        ROOT_DIR / "data/remote/source_prevalidation_summary.json",
    )
)

# Strong infrastructure patterns. These are safe to exclude from source
# research because they are delivery/measurement/consent/storage systems, not
# candidate-facing opportunity sources.
INFRA_EXACT_OR_SUFFIX = {
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "google-analytics.com",
    "googletagmanager.com",
    "hubapi.com",
    "awstrack.me",
    "mailgun.org",
    "sendgrid.net",
    "mandrillapp.com",
}

INFRA_HOST_FRAGMENTS = {
    "pubads.",
    "adservice.",
    "analytics.",
    "pixel.",
    "eventtracking.",
    "trackimp.",
}

CONSENT_HOSTS = {
    "consent.youtube.com",
    "accounts.google.com",
}

# Hosts whose names strongly indicate email transport/tracking. We keep a
# special exception for known candidate-facing root domains such as remote.com.
TRANSPORT_PREFIXES = (
    "click.",
    "tracking.",
    "track.",
    "email.",
    "view.email.",
    "pages.email.",
    "links.",
)

# Strong positive source signals. These are not final scores; they merely help
# prioritize the validation queue.
POSITIVE_TERMS = (
    "job", "jobs", "career", "careers", "remote", "talent", "recruit",
    "staffing", "contract", "freelance", "consult", "work", "hiring",
    "opportunit", "vacanc", "apply", "employment",
)

# Terms that often mean "remote" in a non-employment sense.
NEGATIVE_REMOTE_CONTEXT = (
    "remote desktop", "remote code", "remote access", "remote server",
    "remote hacking", "remotely unlocked", "ransomware", "malware",
    "exploit", "security flaw", "ssh", "protocol", "vehicle", "car",
)

KNOWN_SOURCE_ROOTS = {
    "linkedin.com",
    "remote.com",
    "jobgether.com",
    "crossover.com",
    "hays.ie",
    "hays.com",
    "indeed.com",
    "glassdoor.com",
    "wellfound.com",
    "weworkremotely.com",
    "remotive.com",
    "remote.co",
    "remoteok.com",
    "workingnomads.com",
    "flexjobs.com",
    "upwork.com",
    "contra.com",
    "toptal.com",
    "braintrust.com",
    "arc.dev",
    "malt.com",
    "catalant.com",
}


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


def host_matches_suffix(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def evidence_text(row: dict[str, Any]) -> str:
    fields: list[str] = [
        str(row.get("canonical_host", "")),
        str(row.get("canonical_root_url", "")),
    ]
    for key in (
        "source_hint_urls",
        "representative_urls",
        "discovery_evidence_urls",
        "source_subject_examples",
        "source_sender_examples",
    ):
        value = row.get(key, [])
        if isinstance(value, list):
            fields.extend(str(v) for v in value)
        elif value:
            fields.append(str(value))
    return " ".join(fields).lower()


def root_domain_hint(host: str) -> str:
    parts = [p for p in host.lower().split(".") if p]
    if len(parts) < 2:
        return host.lower()
    return ".".join(parts[-2:])


def classify(row: dict[str, Any]) -> tuple[str, int, str]:
    host = str(row.get("canonical_host", "")).strip().lower()
    text = evidence_text(row)

    if not host:
        return "infrastructure_noise", 0, "missing_host"

    if host in CONSENT_HOSTS:
        return "infrastructure_noise", 0, "consent_or_auth_infrastructure"

    if any(host_matches_suffix(host, suffix) for suffix in INFRA_EXACT_OR_SUFFIX):
        return "infrastructure_noise", 0, "advertising_tracking_or_delivery_infrastructure"

    if any(fragment in host for fragment in INFRA_HOST_FRAGMENTS):
        return "infrastructure_noise", 0, "advertising_tracking_or_measurement_host"

    root_hint = root_domain_hint(host)

    # Email/click subdomains can still belong to a meaningful organization.
    # Keep the organization when recognizable, but do not mistake the
    # transport hostname itself for the final canonical source.
    if host.startswith(TRANSPORT_PREFIXES):
        if root_hint in {"remote.com", "hays.com"}:
            return "validation_queue", 80, "known_source_via_email_transport"
        return "semantic_review", 20, "email_or_click_transport_requires_parent_resolution"

    if host in KNOWN_SOURCE_ROOTS or root_hint in KNOWN_SOURCE_ROOTS:
        return "validation_queue", 95, "known_candidate_facing_source"

    positive_hits = sum(1 for term in POSITIVE_TERMS if term in text)
    negative_hits = sum(1 for term in NEGATIVE_REMOTE_CONTEXT if term in text)

    direct_count = int(row.get("evidence_url_count", 0) or 0)
    discovery_count = int(row.get("discovery_resolution_evidence_count", 0) or 0)
    email_count = int(row.get("email_evidence_count", 0) or 0)

    score = 35
    score += min(25, positive_hits * 5)
    score += min(15, email_count * 2)
    score += min(10, direct_count // 3)
    score += min(10, discovery_count * 2)
    score -= min(35, negative_hits * 12)
    score = max(0, min(100, score))

    if negative_hits >= 1 and positive_hits <= 1:
        return "semantic_review", score, "remote_likely_non_employment_context"

    if positive_hits >= 1 or email_count >= 2 or direct_count >= 3 or discovery_count >= 2:
        return "validation_queue", score, "sufficient_candidate_source_signal"

    return "semantic_review", score, "weak_or_ambiguous_source_signal"


def main() -> int:
    if not INPUT_PATH.exists():
        print(f"Fatal error: input not found: {INPUT_PATH}", file=sys.stderr)
        return 1

    try:
        rows = load_jsonl(INPUT_PATH)
        queue: list[dict[str, Any]] = []
        review: list[dict[str, Any]] = []
        noise: list[dict[str, Any]] = []

        for row in rows:
            bucket, score, reason = classify(row)
            output = dict(row)
            output["prevalidation_bucket"] = bucket
            output["prevalidation_score"] = score
            output["prevalidation_reason"] = reason

            if bucket == "validation_queue":
                queue.append(output)
            elif bucket == "semantic_review":
                review.append(output)
            else:
                noise.append(output)

        queue.sort(
            key=lambda r: (
                -int(r.get("prevalidation_score", 0) or 0),
                -int(r.get("email_evidence_count", 0) or 0),
                -int(r.get("evidence_url_count", 0) or 0),
                str(r.get("canonical_host", "")),
            )
        )
        review.sort(
            key=lambda r: (
                -int(r.get("prevalidation_score", 0) or 0),
                -int(r.get("email_evidence_count", 0) or 0),
                str(r.get("canonical_host", "")),
            )
        )
        noise.sort(key=lambda r: str(r.get("canonical_host", "")))

        write_jsonl(QUEUE_PATH, queue)
        write_jsonl(REVIEW_PATH, review)
        write_jsonl(NOISE_PATH, noise)

        summary = {
            "status": "ok",
            "merged_input_sources": len(rows),
            "validation_queue_sources": len(queue),
            "semantic_review_sources": len(review),
            "infrastructure_noise_sources": len(noise),
            "accounted_sources": len(queue) + len(review) + len(noise),
            "validation_queue_output": str(QUEUE_PATH),
            "semantic_review_output": str(REVIEW_PATH),
            "infrastructure_noise_output": str(NOISE_PATH),
        }

        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(
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
