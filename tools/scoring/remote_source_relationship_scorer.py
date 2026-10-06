#!/usr/bin/env python3
"""
remote_source_relationship_scorer.py

Score live-validated Candidate <-> Remote Source relationships.

Inputs:
- data/remote/source_validation_queue.jsonl
- data/remote/source_validation_results.jsonl
- data/remote/source_validation_unresolved.jsonl

Outputs:
- data/remote/source_relationship_scored.jsonl
- data/remote/source_relationship_shortlist.jsonl
- data/remote/source_relationship_review.jsonl
- data/remote/source_relationship_excluded.jsonl
- data/remote/source_relationship_scoring_summary.json

Design:
- Preserve all 128 validation-queue candidates.
- Treat crawler failure separately from source quality.
- Collapse known technical/subdomain aliases into durable parent relationships.
- Reward candidate-facing job/career evidence and remote orientation.
- Penalize news/content/editorial sites, product/support sites, expired domains,
  redirect shorteners, and unrelated redirect destinations.
- Keep unresolved but known high-value sources (e.g. FlexJobs/Remote.co) for
  manual/targeted verification instead of auto-rejecting them.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]

QUEUE_PATH = Path(os.getenv(
    "REMOTE_SOURCE_VALIDATION_QUEUE",
    ROOT_DIR / "data/remote/source_validation_queue.jsonl",
))
RESULTS_PATH = Path(os.getenv(
    "REMOTE_SOURCE_VALIDATION_RESULTS",
    ROOT_DIR / "data/remote/source_validation_results.jsonl",
))
UNRESOLVED_PATH = Path(os.getenv(
    "REMOTE_SOURCE_VALIDATION_UNRESOLVED",
    ROOT_DIR / "data/remote/source_validation_unresolved.jsonl",
))

SCORED_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_SCORED",
    ROOT_DIR / "data/remote/source_relationship_scored.jsonl",
))
SHORTLIST_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_SHORTLIST",
    ROOT_DIR / "data/remote/source_relationship_shortlist.jsonl",
))
REVIEW_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_REVIEW",
    ROOT_DIR / "data/remote/source_relationship_review.jsonl",
))
EXCLUDED_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_EXCLUDED",
    ROOT_DIR / "data/remote/source_relationship_excluded.jsonl",
))
SUMMARY_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_SUMMARY",
    ROOT_DIR / "data/remote/source_relationship_scoring_summary.json",
))

KNOWN_STRONG = {
    "jobgether.com", "crossover.com", "remote.com", "flexjobs.com",
    "remote.co", "remoteok.com", "weworkremotely.com", "remotive.com",
    "wellfound.com", "upwork.com", "arc.dev", "toptal.com",
    "dynamitejobs.com", "jobspresso.co", "landing.jobs", "justremote.co",
    "remotewoman.com", "freelancer.com", "authenticjobs.com",
    "computerfutures.com", "gcsrecruitment.com", "realstaffing.com",
    "trustinsoda.com", "reperiohumancapital.com", "collinsmcnicholas.ie",
    "hays.ie", "talentspot.ie", "recruiter.com", "job-hunt.org",
    "jobright.ai", "remotely.jobs", "theaijobboard.com",
}

KNOWN_CONTENT = {
    "wsj.com", "businessinsider.com", "fortune.com", "forbes.com",
    "theregister.com", "itpro.co.uk", "itprotoday.com", "makeuseof.com",
    "dev.to", "dublinlive.ie", "prnewswire.com", "techradar.com",
    "afr.com", "scmp.com", "nbcnews.com", "thestreet.com",
    "hbr.org", "inc.com", "rfi.fr", "time.com", "thehill.com",
    "yourtango.com", "personneltoday.com", "hrdive.com",
}

KNOWN_NON_CANDIDATE = {
    "teamviewer.com", "try.teamviewer.com", "community.teamviewer.com",
    "rebrand.ly", "geni.us", "open.substack.com", "file-eu.clickdimensions.com",
    "support.microsoft.com", "nonprofits.tsi.microsoft.com",
    "newsletter.aiautomationsociety.ai", "res.infoq.com",
}

# Subdomain / legacy aliases that should become one durable relationship.
CANONICAL_ALIASES = {
    "m.hays.ie": "hays.ie",
    "onlineapi-internet.hays.com": "hays.ie",
    "candidate-support.crossover.com": "crossover.com",
    "apply2.computerfutures.com": "computerfutures.com",
    "ie.gcsrecruitment.com": "gcsrecruitment.com",
    "in.indeed.com": "indeed.com",
    "uk.indeed.com": "indeed.com",
    "in.talent.com": "talent.com",
    "ie.neuvoo.com": "talent.com",
    "jobs.zendesk.com": "zendesk.com",
    "careers.microsoft.com": "microsoft.com",
    "members.microsoft.com": "microsoft.com",
    "careers.bunq.com": "bunq.com",
    "press.bunq.com": "bunq.com",
    "careers.accelerationpartners.com": "accelerationpartners.com",
    "intuition-it.vincere.io": "intuition-it.com",
    "remotelyx-3.careers-page.com": "remotelyx.com",
}

EXPIRED_OR_DEAD_MARKERS = (
    "expireddomains.com", "domain for sale", "this domain is for sale",
    "expired domain", "parked domain",
)

CONTENT_MARKERS = (
    "news", "magazine", "journal", "latest stories", "breaking news",
    "opinion", "analysis", "podcast", "newsletter",
)

CANDIDATE_MARKERS = (
    "jobs", "job search", "find jobs", "search jobs", "careers",
    "open positions", "vacancies", "hiring", "recruitment", "recruiter",
    "freelance", "contract jobs", "remote jobs", "apply",
)

REMOTE_MARKERS = (
    "remote jobs", "remote work", "work from home", "work anywhere",
    "distributed", "fully remote",
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


def canonical_relationship_host(host: str) -> str:
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return CANONICAL_ALIASES.get(host, host)


def all_text(row: dict[str, Any]) -> str:
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


def hits(text: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker in text)


def score_one(queue_row: dict[str, Any], validation: dict[str, Any] | None) -> dict[str, Any]:
    original_host = str(queue_row.get("canonical_host", "")).lower()
    canonical_host = canonical_relationship_host(original_host)

    validation = validation or {}
    resolved = bool(validation.get("resolved"))
    final_url = str(validation.get("final_url", "") or "")
    final_host = host_of(final_url)
    text = all_text(validation)

    candidate_link_count = int(validation.get("candidate_link_count", 0) or 0)
    remote_hit_count = len(validation.get("remote_term_hits", []) or [])
    candidate_hit_count = len(validation.get("candidate_term_hits", []) or [])

    candidate_text_hits = hits(text, CANDIDATE_MARKERS)
    remote_text_hits = hits(text, REMOTE_MARKERS)

    score = 20
    reasons: list[str] = []

    if canonical_host in KNOWN_STRONG:
        score += 45
        reasons.append("known_candidate_source")

    if resolved:
        score += 5
        reasons.append("live_resolved")
    else:
        reasons.append("live_unresolved")

    if candidate_link_count:
        score += min(20, 4 + candidate_link_count // 4)
        reasons.append(f"candidate_links:{candidate_link_count}")

    if candidate_hit_count or candidate_text_hits:
        score += min(15, 3 * max(candidate_hit_count, candidate_text_hits))
        reasons.append("candidate_language")

    if remote_hit_count or remote_text_hits:
        score += min(15, 5 * max(remote_hit_count, remote_text_hits))
        reasons.append("remote_language")

    if canonical_host in KNOWN_CONTENT:
        score -= 35
        reasons.append("content_publisher_penalty")

    if canonical_host in KNOWN_NON_CANDIDATE or original_host in KNOWN_NON_CANDIDATE:
        score -= 45
        reasons.append("non_candidate_platform_penalty")

    if any(marker in text for marker in EXPIRED_OR_DEAD_MARKERS):
        score -= 60
        reasons.append("expired_or_parked_domain")

    if canonical_host not in KNOWN_STRONG and hits(text, CONTENT_MARKERS) >= 2 and candidate_link_count <= 5:
        score -= 20
        reasons.append("editorial_content_bias")

    # Redirecting to an unrelated host is a strong warning unless this is a
    # known migration/brand canonicalization.
    if resolved and final_host:
        final_canon = canonical_relationship_host(final_host)
        if final_canon != canonical_host:
            allowed = {
                ("latpro.com", "diversityjobs.com"),
                ("gcsrecruitment.com", "gcstechtalent.com"),
                ("itprotoday.com", "techtarget.com"),
                ("windowsitpro.com", "techtarget.com"),
                ("winsupersite.com", "techtarget.com"),
                ("networkcomputing.com", "techtarget.com"),
                ("productcollective.com", "mindtheproduct.com"),
                ("remotecircle.com", "alexistogel.biz"),
                ("jsremotely.com", "javascript.jobs"),
            }
            if (canonical_host, final_canon) not in allowed:
                score -= 10
                reasons.append(f"redirected_to:{final_canon}")

    # Unresolved is not a quality failure for known strong sources.
    if not resolved and canonical_host not in KNOWN_STRONG:
        score -= 10

    score = max(0, min(100, score))

    if score >= 75:
        relationship_class = "strong"
        recommended_use = "Primary source: monitor and use actively"
    elif score >= 55:
        relationship_class = "promising"
        recommended_use = "Secondary source: validate fit and monitor"
    elif score >= 35:
        relationship_class = "review"
        recommended_use = "Manual review before inclusion"
    else:
        relationship_class = "exclude"
        recommended_use = "Do not prioritize as remote-opportunity source"

    # Preserve known strong unresolved sources as review/strong candidates.
    verification_status = "live_verified" if resolved else "live_unresolved"
    if not resolved and canonical_host in KNOWN_STRONG:
        score = max(score, 70)
        relationship_class = "promising"
        recommended_use = "Known high-value source; targeted manual verification required"
        verification_status = "known_source_live_blocked"

    return {
        "Source": canonical_host,
        "URL": f"https://{canonical_host}/",
        "Original Host": original_host,
        "Final URL": final_url,
        "Final Host": final_host,
        "Relationship Score": score,
        "Relationship Class": relationship_class,
        "Verification Status": verification_status,
        "Candidate Link Count": candidate_link_count,
        "Candidate Term Hits": candidate_hit_count,
        "Remote Term Hits": remote_hit_count,
        "Email Evidence Count": int(queue_row.get("email_evidence_count", 0) or 0),
        "Discovery Evidence Count": int(queue_row.get("discovery_resolution_evidence_count", 0) or 0),
        "Direct Evidence URL Count": int(queue_row.get("evidence_url_count", 0) or 0),
        "Prevalidation Score": int(queue_row.get("prevalidation_score", 0) or 0),
        "Prevalidation Reason": queue_row.get("prevalidation_reason"),
        "Recommended Use": recommended_use,
        "Scoring Reasons": reasons,
        "Page Title": validation.get("page_title", ""),
        "Meta Description": validation.get("meta_description", ""),
        "Candidate Links": validation.get("candidate_links", []),
    }


def merge_relationship_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("Source", ""))].append(row)

    merged: list[dict[str, Any]] = []
    for source, members in grouped.items():
        members.sort(
            key=lambda r: (
                -int(r.get("Relationship Score", 0) or 0),
                -int(r.get("Candidate Link Count", 0) or 0),
                str(r.get("Original Host", "")),
            )
        )
        best = dict(members[0])

        best["Relationship Score"] = max(int(m.get("Relationship Score", 0) or 0) for m in members)
        classes = [str(m.get("Relationship Class", "")) for m in members]
        rank = {"exclude": 0, "review": 1, "promising": 2, "strong": 3}
        best["Relationship Class"] = max(classes, key=lambda c: rank.get(c, -1))
        best["Original Hosts"] = sorted({str(m.get("Original Host", "")) for m in members if m.get("Original Host")})
        best["Alias Count"] = len(best["Original Hosts"])
        best["Email Evidence Count"] = sum(int(m.get("Email Evidence Count", 0) or 0) for m in members)
        best["Discovery Evidence Count"] = sum(int(m.get("Discovery Evidence Count", 0) or 0) for m in members)
        best["Direct Evidence URL Count"] = sum(int(m.get("Direct Evidence URL Count", 0) or 0) for m in members)
        best["Candidate Link Count"] = max(int(m.get("Candidate Link Count", 0) or 0) for m in members)
        best["Scoring Reasons"] = sorted({
            reason
            for m in members
            for reason in (m.get("Scoring Reasons", []) or [])
        })
        merged.append(best)

    merged.sort(key=lambda r: (-int(r.get("Relationship Score", 0) or 0), str(r.get("Source", ""))))
    return merged


def main() -> int:
    queue = load_jsonl(QUEUE_PATH)
    results = {str(r.get("validation_key", "")).lower(): r for r in load_jsonl(RESULTS_PATH)}
    unresolved = {str(r.get("validation_key", "")).lower(): r for r in load_jsonl(UNRESOLVED_PATH)}
    validation = dict(results)
    validation.update(unresolved)

    scored_raw = [
        score_one(row, validation.get(str(row.get("canonical_host", "")).lower()))
        for row in queue
    ]
    scored = merge_relationship_rows(scored_raw)

    shortlist = [r for r in scored if r.get("Relationship Class") in {"strong", "promising"}]
    review = [r for r in scored if r.get("Relationship Class") == "review"]
    excluded = [r for r in scored if r.get("Relationship Class") == "exclude"]

    write_jsonl(SCORED_PATH, scored)
    write_jsonl(SHORTLIST_PATH, shortlist)
    write_jsonl(REVIEW_PATH, review)
    write_jsonl(EXCLUDED_PATH, excluded)

    summary = {
        "status": "ok",
        "validation_queue_rows": len(queue),
        "live_results_rows": len(results),
        "live_unresolved_rows": len(unresolved),
        "accounted_validation_rows": len(scored_raw),
        "canonical_relationships": len(scored),
        "aliases_collapsed": len(scored_raw) - len(scored),
        "strong_relationships": sum(1 for r in scored if r.get("Relationship Class") == "strong"),
        "promising_relationships": sum(1 for r in scored if r.get("Relationship Class") == "promising"),
        "review_relationships": len(review),
        "excluded_relationships": len(excluded),
        "scored_output": str(SCORED_PATH),
        "shortlist_output": str(SHORTLIST_PATH),
        "review_output": str(REVIEW_PATH),
        "excluded_output": str(EXCLUDED_PATH),
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
