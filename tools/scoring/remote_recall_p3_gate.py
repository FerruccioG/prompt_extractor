#!/usr/bin/env python3
"""Reusable conservative gate for Recall Expansion P3 batches 02-04.

P3 is low-priority and noisy. This gate preserves plausible durable
Candidate <-> Source relationships without promoting anything automatically.

Environment variables:
- REMOTE_RECALL_P3_QUEUE
- REMOTE_RECALL_P3_VALIDATION_RESULTS
- REMOTE_RECALL_P3_VALIDATION_UNRESOLVED
- REMOTE_RECALL_P3_BASELINE
- REMOTE_RECALL_P3_TARGETED_OUTPUT
- REMOTE_RECALL_P3_INDIRECT_OUTPUT
- REMOTE_RECALL_P3_OVERLAP_OUTPUT
- REMOTE_RECALL_P3_REJECT_OUTPUT
- REMOTE_RECALL_P3_SUMMARY

Outputs four buckets:
- targeted_review
- indirect_keep
- baseline_overlap
- reject_or_defer
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]

QUEUE_PATH = Path(os.environ["REMOTE_RECALL_P3_QUEUE"])
RESULTS_PATH = Path(os.environ["REMOTE_RECALL_P3_VALIDATION_RESULTS"])
UNRESOLVED_PATH = Path(os.environ["REMOTE_RECALL_P3_VALIDATION_UNRESOLVED"])
BASELINE_PATH = Path(os.getenv(
    "REMOTE_RECALL_P3_BASELINE",
    ROOT_DIR / "data/remote/source_records_recall_p1_expanded.jsonl",
))

TARGET_PATH = Path(os.environ["REMOTE_RECALL_P3_TARGETED_OUTPUT"])
INDIRECT_PATH = Path(os.environ["REMOTE_RECALL_P3_INDIRECT_OUTPUT"])
OVERLAP_PATH = Path(os.environ["REMOTE_RECALL_P3_OVERLAP_OUTPUT"])
REJECT_PATH = Path(os.environ["REMOTE_RECALL_P3_REJECT_OUTPUT"])
SUMMARY_PATH = Path(os.environ["REMOTE_RECALL_P3_SUMMARY"])

HOST_ALIASES = {
    "www.wellfound.com": "wellfound.com",
    "angel.co": "wellfound.com",
    "remotive.io": "remotive.com",
    "thisisgcs.com": "gcsrecruitment.com",
    "gcstechtalent.com": "gcsrecruitment.com",
    "online.robertwalters.com": "robertwalters.com",
    "robertwalters.co.uk": "robertwalters.com",
    "email.cv-library.co.uk": "cv-library.co.uk",
    "api.hireez.com": "hireez.com",
    "securitylabs.datadoghq.com": "datadoghq.com",
}

TARGETED_HOSTS = {
    "powertofly.com": "candidate_platform",
    "themuse.com": "job_platform",
    "intuition-it.com": "technology_recruiter",
    "forbesprojectsolutions.com": "project_staffing_candidate",
    "robertwalters.com": "recruiter",
    "fiverr.com": "freelance_marketplace",
    "gun.io": "technology_talent_marketplace",
    "workwave.com": "technology_employer",
    "gibbshybrid.com": "recruitment_candidate",
    "atriumglobal.com": "recruitment_candidate",
    "outreach.ai": "technology_employer",
    "outreach.io": "technology_employer",
    "thrivedigital.com": "digital_technology_employer",
    "citrix.com": "technology_employer",
    "cv-library.co.uk": "job_platform",
}

INDIRECT_HOSTS = {
    "informationweek.com": "technology_media",
    "darkreading.com": "cybersecurity_media",
    "adtmag.com": "developer_media",
    "rcpmag.com": "microsoft_partner_media",
    "hackernoon.com": "technology_content",
    "arstechnica.com": "technology_media",
    "hackread.com": "cybersecurity_media",
    "securityaffairs.com": "cybersecurity_media",
    "duckdb.org": "database_technology_ecosystem",
    "biinsight.com": "business_intelligence_content",
    "pbiusergroup.com": "power_bi_community",
    "dynamicscommunities.com": "power_platform_community",
    "datadoghq.com": "technology_security_content",
    "techmentorevents.com": "technology_training_events",
    "live360events.com": "technology_events",
    "reddit.com": "community_platform",
}

INFRA_SUFFIXES = (
    "1105newsletters.com", "pentontech.com", "informamail07.com",
    "sendibm3.com", "en25.com", "netline.com", "lookbookhq.com",
    "halldata.com", "mimecast.com", "adspeed.net", "informaengage.com",
    "cbsi.com", "azureedge.net", "hubspotusercontent-eu1.net",
    "1105web.com", "eloqua.idg.co.uk", "cp20.com", "nylas.com",
)

INFRA_EXACT = {
    "tinyurl.com", "go.reg.cx", "referralhub.page", "calendly.com",
    "store.pcpro.co.uk", "calendar.zoho.com", "download.1105media.com",
    "cbsi.secure.force.com", "linkscan.io", "sl1nk.com", "zdbb.net",
    "r.smartbrief.com", "1105tech.com", "time.is", "getfeedback.com",
}

GENERIC_LOW_VALUE = {
    "groupon.ie", "grouponworks.ie", "independent.ie", "newsweek.com",
    "reuters.com", "bbc.com", "washingtonpost.com", "businessweek.com",
    "change.org", "quora.com", "newspicks.us",
}

KNOWN_EXPIRED_OR_INVALID = {
    "pangian.com",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def canon_host(value: str) -> str:
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = (parsed.hostname or "").lower().strip(".")
        if host.startswith("www."):
            host = host[4:]
        return HOST_ALIASES.get(host, host)
    except Exception:
        return ""


def validation_index() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in (RESULTS_PATH, UNRESOLVED_PATH):
        for row in load_jsonl(path):
            raw = str(row.get("validation_key", "") or row.get("canonical_host", ""))
            key = canon_host(raw)
            if key:
                out[key] = row
    return out


def baseline_hosts() -> set[str]:
    out: set[str] = set()
    for row in load_jsonl(BASELINE_PATH):
        source = canon_host(str(row.get("Source", "")))
        urlhost = canon_host(str(row.get("URL", "")))
        if source:
            out.add(source)
        if urlhost:
            out.add(urlhost)
    return out


def is_infra(host: str) -> bool:
    if host in INFRA_EXACT:
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in INFRA_SUFFIXES)


def classify(row: dict[str, Any], v: dict[str, Any] | None, baseline: set[str]) -> tuple[str, str]:
    original = canon_host(str(row.get("canonical_host", "")))
    v = v or {}
    final_host = canon_host(str(v.get("final_url", "") or ""))
    resolved = bool(v.get("resolved"))
    candidate_links = int(v.get("candidate_link_count", 0) or 0)
    remote_hits = len(v.get("remote_term_hits", []) or [])

    identities = {h for h in (original, final_host) if h}

    if identities & baseline:
        return "baseline_overlap", "already_represented_in_current_golden"

    if identities & KNOWN_EXPIRED_OR_INVALID:
        return "reject_or_defer", "expired_parked_or_invalid_source"

    for h in identities:
        if h in TARGETED_HOSTS:
            return "targeted_review", TARGETED_HOSTS[h]

    for h in identities:
        if h in INDIRECT_HOSTS:
            return "indirect_keep", INDIRECT_HOSTS[h]

    if any(is_infra(h) for h in identities):
        return "reject_or_defer", "tracking_transport_or_technical_infrastructure"

    if identities & GENERIC_LOW_VALUE:
        return "reject_or_defer", "generic_media_or_non_candidate_source"

    # Preserve high-density candidate surfaces not already classified.
    if resolved and candidate_links >= 8:
        return "targeted_review", "high_candidate_link_density_requires_manual_relationship_review"

    if resolved and candidate_links >= 3 and remote_hits >= 2:
        return "targeted_review", "moderate_candidate_links_plus_remote_signal"

    if not resolved:
        return "reject_or_defer", "unresolved_without_known_durable_candidate_identity"

    return "reject_or_defer", "insufficient_p3_evidence_for_deeper_review"


def main() -> int:
    queue = load_jsonl(QUEUE_PATH)
    validation = validation_index()
    baseline = baseline_hosts()

    buckets: dict[str, list[dict[str, Any]]] = {
        "targeted_review": [],
        "indirect_keep": [],
        "baseline_overlap": [],
        "reject_or_defer": [],
    }
    missing: list[str] = []

    for row in queue:
        key = canon_host(str(row.get("canonical_host", "")))
        v = validation.get(key)
        if v is None:
            # If validation redirected to an aliased identity, try raw key once.
            raw = str(row.get("canonical_host", "")).lower()
            for candidate in load_jsonl(RESULTS_PATH) + load_jsonl(UNRESOLVED_PATH):
                raw_candidate = str(candidate.get("validation_key", "") or candidate.get("canonical_host", "")).lower()
                if raw_candidate == raw:
                    v = candidate
                    break
        if v is None:
            missing.append(key or str(row.get("canonical_host", "")))

        bucket, reason = classify(row, v, baseline)
        out = dict(row)
        out["p3_gate_bucket"] = bucket
        out["p3_gate_reason"] = reason
        out["p3_validation"] = v or {}
        buckets[bucket].append(out)

    def sort_key(r: dict[str, Any]) -> tuple[int, str]:
        v = r.get("p3_validation", {}) or {}
        return (
            -int(v.get("candidate_link_count", 0) or 0),
            str(r.get("recall_canonical_identity") or r.get("canonical_host") or ""),
        )

    for rows in buckets.values():
        rows.sort(key=sort_key)

    write_jsonl(TARGET_PATH, buckets["targeted_review"])
    write_jsonl(INDIRECT_PATH, buckets["indirect_keep"])
    write_jsonl(OVERLAP_PATH, buckets["baseline_overlap"])
    write_jsonl(REJECT_PATH, buckets["reject_or_defer"])

    summary = {
        "status": "ok",
        "batch_input_sources": len(queue),
        "validation_records_found": len(queue) - len(missing),
        "validation_records_missing": missing,
        "targeted_review_sources": len(buckets["targeted_review"]),
        "indirect_keep_sources": len(buckets["indirect_keep"]),
        "baseline_overlap_sources": len(buckets["baseline_overlap"]),
        "rejected_or_deferred_sources": len(buckets["reject_or_defer"]),
        "accounted_sources": sum(len(v) for v in buckets.values()),
        "targeted_review_identities": [
            str(r.get("recall_canonical_identity") or r.get("canonical_host") or "")
            for r in buckets["targeted_review"]
        ],
        "indirect_keep_identities": [
            str(r.get("recall_canonical_identity") or r.get("canonical_host") or "")
            for r in buckets["indirect_keep"]
        ],
        "baseline_overlap_identities": [
            str(r.get("recall_canonical_identity") or r.get("canonical_host") or "")
            for r in buckets["baseline_overlap"]
        ],
    }

    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
