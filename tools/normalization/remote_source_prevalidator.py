#!/usr/bin/env python3
"""
remote_source_prevalidator.py

High-recall pre-validation for Candidate <-> Remote Source relationships.

This revision deliberately separates:
- source-surface evidence (host/URLs), which can justify validation;
- email subject/context evidence, which is weak because every source entered
  through a Gmail corpus already filtered by subject containing "Remote";
- infrastructure/newsletter/CDN hosts, which must not be promoted merely
  because they occur frequently.

Every merged source lands in exactly one bucket:
1. validation_queue
2. semantic_review
3. infrastructure_noise

Nothing is silently discarded.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = Path(os.getenv(
    "REMOTE_MERGED_SOURCES",
    ROOT_DIR / "data/remote/source_candidates_merged.jsonl",
))
QUEUE_PATH = Path(os.getenv(
    "REMOTE_SOURCE_VALIDATION_QUEUE",
    ROOT_DIR / "data/remote/source_validation_queue.jsonl",
))
REVIEW_PATH = Path(os.getenv(
    "REMOTE_SOURCE_SEMANTIC_REVIEW",
    ROOT_DIR / "data/remote/source_semantic_review.jsonl",
))
NOISE_PATH = Path(os.getenv(
    "REMOTE_SOURCE_INFRASTRUCTURE_NOISE",
    ROOT_DIR / "data/remote/source_infrastructure_noise.jsonl",
))
SUMMARY_PATH = Path(os.getenv(
    "REMOTE_SOURCE_PREVALIDATION_SUMMARY",
    ROOT_DIR / "data/remote/source_prevalidation_summary.json",
))

KNOWN_CANDIDATE_SOURCES = {
    "linkedin.com", "remote.com", "jobgether.com", "crossover.com",
    "indeed.com", "glassdoor.com", "wellfound.com", "weworkremotely.com",
    "remotive.com", "remote.co", "remoteok.com", "workingnomads.com",
    "flexjobs.com", "upwork.com", "contra.com", "toptal.com",
    "braintrust.com", "arc.dev", "malt.com", "catalant.com",
    "hays.ie", "hays.com",
}

INFRA_SUFFIXES = {
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "google-analytics.com", "googletagmanager.com", "hubapi.com",
    "awstrack.me", "mailgun.org", "sendgrid.net", "mandrillapp.com",
    "mediaplex.com", "pippio.com",
}

INFRA_EXACT = {
    "accounts.google.com",
    "media.licdn.com",
    "vercel.link",
    "lnkd.in",
}

GENERIC_OR_NON_CANDIDATE_PLATFORMS = {
    "bing.com",
    "github.com",
    "medium.com",
}

REMOTE_PRODUCT_NONEMPLOYMENT = {
    "teamviewer.com",
}

INFRA_FRAGMENTS = {
    "pubads.", "adservice.", "analytics.", "pixel.", "eventtracking.",
    "trackimp.", "imglinks.",
}

TRANSPORT_PREFIXES = (
    "click.", "tracking.", "track.", "email.", "view.email.", "pages.email.",
    "links.", "link.", "li.", "lm.", "ct.", "edt.", "enews.", "mailing.",
    "r.", "nl.", "eletters.", "newsletters.", "ifwnewsletters.",
)

TRANSPORT_EXACT_OR_SUFFIX = {
    "1105newsletters.com",
    "slgnt.eu",
    "smartbrief.com",
    "list-manage.com",
    "mkt3261.com",
    "p0.com",
}

SOCIAL_CHROME_OR_ECOSYSTEM = {
    "about.meta.com", "meta.ai", "muse.ai", "threads.com",
}

NEWS_OR_CONTENT_PUBLISHERS = {
    "bbc.com", "reuters.com", "arstechnica.com", "darkreading.com",
    "thehackernews.com", "bleepingcomputer.com", "theregister.com",
    "pcmag.com", "cnet.com", "eweek.com", "techtarget.com",
    "techrepublic.com", "computerworld.com", "infoworld.com",
}

# Strong candidate-facing terms in the actual source surface.
STRONG_SURFACE_TERMS = (
    "job", "jobs", "career", "careers", "recruit", "recruitment",
    "staffing", "talent", "freelance", "contract", "hiring", "vacanc",
    "apply", "employment", "opportunit",
)

# "remote" only counts when it occurs in the host/URL itself. It is NOT
# counted from email subject lines because the entire corpus was selected by
# subject:Remote, which otherwise creates circular scoring.
REMOTE_SURFACE_TERM = "remote"

NEGATIVE_REMOTE_CONTEXT = (
    "remote desktop", "remote code", "remote access", "remote server",
    "remote hacking", "remotely unlocked", "ransomware", "malware",
    "exploit", "security flaw", "ssh", "protocol", "vehicle", "car",
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


def host_matches_suffix(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def root_domain_hint(host: str) -> str:
    parts = [p for p in host.lower().split(".") if p]
    if len(parts) < 2:
        return host.lower()
    return ".".join(parts[-2:])


def join_values(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    values: list[str] = []
    for key in keys:
        value = row.get(key, [])
        if isinstance(value, list):
            values.extend(str(v) for v in value)
        elif value:
            values.append(str(value))
    return " ".join(values).lower()


def surface_text(row: dict[str, Any]) -> str:
    return " ".join([
        str(row.get("canonical_host", "")),
        str(row.get("canonical_root_url", "")),
        join_values(row, (
            "source_hint_urls",
            "representative_urls",
        )),
    ]).lower()


def contextual_text(row: dict[str, Any]) -> str:
    return join_values(row, (
        "source_subject_examples",
        "source_sender_examples",
    ))


def is_transport_host(host: str) -> bool:
    if host.startswith(TRANSPORT_PREFIXES):
        return True
    return any(host_matches_suffix(host, suffix) for suffix in TRANSPORT_EXACT_OR_SUFFIX)


def classify(row: dict[str, Any]) -> tuple[str, int, str]:
    host = str(row.get("canonical_host", "")).strip().lower()
    if not host:
        return "infrastructure_noise", 0, "missing_host"

    root_hint = root_domain_hint(host)
    surface = surface_text(row)
    context = contextual_text(row)

    if host in INFRA_EXACT:
        return "infrastructure_noise", 0, "known_infrastructure_host"
    if any(host_matches_suffix(host, suffix) for suffix in INFRA_SUFFIXES):
        return "infrastructure_noise", 0, "advertising_tracking_or_delivery_infrastructure"
    if any(fragment in host for fragment in INFRA_FRAGMENTS):
        return "infrastructure_noise", 0, "advertising_tracking_or_measurement_host"

    if host in KNOWN_CANDIDATE_SOURCES:
        return "validation_queue", 95, "known_candidate_facing_source"

    if is_transport_host(host):
        # Even when the parent organization is valuable (for example Hays or
        # Crossover), the tracking/click hostname is not the durable source
        # identity. Preserve it for parent resolution instead of validating it
        # as a separate source.
        if root_hint in KNOWN_CANDIDATE_SOURCES:
            return "semantic_review", 30, "known_source_transport_requires_parent_collapse"
        return "semantic_review", 15, "transport_or_newsletter_host_requires_parent_resolution"

    if root_hint in KNOWN_CANDIDATE_SOURCES:
        return "validation_queue", 90, "known_candidate_source_subdomain"

    if host in GENERIC_OR_NON_CANDIDATE_PLATFORMS:
        return "semantic_review", 20, "generic_platform_not_candidate_source"

    if host in REMOTE_PRODUCT_NONEMPLOYMENT:
        return "semantic_review", 5, "remote_product_not_remote_work_source"

    if host in SOCIAL_CHROME_OR_ECOSYSTEM:
        return "semantic_review", 25, "social_platform_chrome_or_ecosystem_root"

    strong_hits = sum(1 for term in STRONG_SURFACE_TERMS if term in surface)
    remote_on_surface = REMOTE_SURFACE_TERM in surface
    negative_hits = sum(1 for term in NEGATIVE_REMOTE_CONTEXT if term in (surface + " " + context))

    direct_count = int(row.get("evidence_url_count", 0) or 0)
    discovery_count = int(row.get("discovery_resolution_evidence_count", 0) or 0)
    email_count = int(row.get("email_evidence_count", 0) or 0)

    score = 20
    score += min(50, strong_hits * 15)
    score += 15 if remote_on_surface else 0
    score += min(10, discovery_count * 2)
    score += min(5, email_count // 5)
    score -= min(45, negative_hits * 15)
    score = max(0, min(100, score))

    if negative_hits >= 1 and strong_hits == 0:
        return "semantic_review", score, "remote_likely_non_employment_context"

    if host in NEWS_OR_CONTENT_PUBLISHERS or root_hint in NEWS_OR_CONTENT_PUBLISHERS:
        if strong_hits >= 1:
            return "validation_queue", max(score, 60), "publisher_with_explicit_candidate_surface"
        return "semantic_review", score, "news_or_content_publisher_not_candidate_source"

    # Strong source-surface evidence is sufficient for live validation.
    if strong_hits >= 1:
        return "validation_queue", max(score, 60), "explicit_candidate_source_surface"

    # A domain whose own identity is remote-oriented is worth validating even
    # without a jobs/careers token.
    if remote_on_surface:
        return "validation_queue", max(score, 55), "remote_oriented_source_surface"

    # High recurrence alone is evidence of importance, not of candidate-facing
    # usefulness. Keep it for review instead of promoting it automatically.
    if direct_count >= 20 or discovery_count >= 10 or email_count >= 10:
        return "semantic_review", score, "high_recurrence_but_no_candidate_surface_signal"

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

        def evidence_total(r: dict[str, Any]) -> int:
            return (
                int(r.get("evidence_url_count", 0) or 0)
                + int(r.get("discovery_resolution_evidence_count", 0) or 0)
                + int(r.get("email_evidence_count", 0) or 0)
            )

        queue.sort(key=lambda r: (
            -int(r.get("prevalidation_score", 0) or 0),
            -evidence_total(r),
            str(r.get("canonical_host", "")),
        ))
        review.sort(key=lambda r: (
            -evidence_total(r),
            -int(r.get("prevalidation_score", 0) or 0),
            str(r.get("canonical_host", "")),
        ))
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
