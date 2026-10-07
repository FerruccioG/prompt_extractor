#!/usr/bin/env python3
"""
remote_candidate_source_final_scorer.py

Final evidence-weighted Candidate <-> Remote Source scorer.

This stage combines:
- generic source relationship quality,
- live source validation,
- user-specific deep profiling,
- evidence confidence,
while keeping crawler/access failure separate from source quality.

Inputs
------
- data/remote/source_relationship_scored.jsonl
- data/remote/candidate_source_profile_results.jsonl
- data/remote/candidate_source_profile_unresolved.jsonl

Outputs
-------
- data/remote/source_records.jsonl
- data/remote/source_records_review.jsonl
- data/remote/source_records_excluded.jsonl
- data/remote/source_final_scoring_summary.json

The output schema is intentionally aligned with the Golden Excel relationship
model. Scores are evidence-based proxies, not claims about guaranteed job
availability.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

RELATIONSHIPS_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RELATIONSHIP_SCORED",
    ROOT_DIR / "data/remote/source_relationship_scored.jsonl",
))
PROFILE_PATH = Path(os.getenv(
    "REMOTE_CANDIDATE_SOURCE_PROFILE_RESULTS",
    ROOT_DIR / "data/remote/candidate_source_profile_results.jsonl",
))
PROFILE_UNRESOLVED_PATH = Path(os.getenv(
    "REMOTE_CANDIDATE_SOURCE_PROFILE_UNRESOLVED",
    ROOT_DIR / "data/remote/candidate_source_profile_unresolved.jsonl",
))

OUTPUT_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RECORDS",
    ROOT_DIR / "data/remote/source_records.jsonl",
))
REVIEW_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RECORDS_REVIEW",
    ROOT_DIR / "data/remote/source_records_review.jsonl",
))
EXCLUDED_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RECORDS_EXCLUDED",
    ROOT_DIR / "data/remote/source_records_excluded.jsonl",
))
SUMMARY_PATH = Path(os.getenv(
    "REMOTE_SOURCE_FINAL_SCORING_SUMMARY",
    ROOT_DIR / "data/remote/source_final_scoring_summary.json",
))

# Durable source types. These are explicit because source-type semantics matter
# to Candidate <-> Source usefulness and cannot be inferred reliably from
# keyword counts alone.
REMOTE_JOB_BOARDS = {
    "jobgether.com", "weworkremotely.com", "remotive.com", "remoteok.com",
    "jobspresso.co", "justremote.co", "remotely.jobs", "remotewoman.com",
    "flexjobs.com", "remote.co", "jsremotely.com", "remotelyx.com",
    "dynamitejobs.com", "authenticjobs.com", "remote.com",
    "jobicy.com", "himalayas.app", "workingnomads.com", "openjobseu.com",
}
GENERAL_JOB_PLATFORMS = {
    "indeed.com", "wellfound.com", "jobright.ai", "talent.com",
    "landing.jobs", "theaijobboard.com", "latpro.com",
    "linkedin.com", "builtin.com", "ycombinator.com", "dice.com",
}
MARKETPLACES = {
    "upwork.com", "freelancer.com", "arc.dev", "toptal.com",
    "a.team", "braintrust.com", "catalant.com", "contra.com", "malt.com",
}
RECRUITERS = {
    "hays.ie", "computerfutures.com", "gcsrecruitment.com",
    "realstaffing.com", "trustinsoda.com", "reperiohumancapital.com",
    "collinsmcnicholas.ie", "talentspot.ie", "glocomms.com",
    "recruiter.com", "eursap.eu", "contractoruk.com",
}
EMPLOYER_CAREERS = {
    "microsoft.com", "zendesk.com", "bunq.com", "accelerationpartners.com",
}
COMMUNITIES_OR_ADVICE = {
    "growremote.ie", "job-hunt.org", "linkedintalentconnect.com",
    "productcollective.com", "hbswk.hbs.edu",
}
CONTENT_OR_LOW_DIRECTNESS = {
    "timesofindia.indiatimes.com", "blog.yelp.com", "gartner.com",
    "snacknation.com", "detect.fyi", "udemy.com",
}
KNOWN_BAD_OR_HIJACKED = {
    "remotecircle.com",
}
SPECIALIZATION_MISMATCH = {
    "careerstructure.com",
    "eursap.eu",
}

# Strong known sources whose profile extraction may be blocked/JS-heavy.
KNOWN_HIGH_VALUE_WITH_WEAK_PROFILE = {
    "crossover.com", "flexjobs.com", "remote.co", "computerfutures.com",
    "gcsrecruitment.com", "toptal.com", "upwork.com", "weworkremotely.com",
    "theaijobboard.com", "indeed.com",
    "openjobseu.com", "malt.com",
}

GROUP_WEIGHTS = {
    "ai_agentic": 22,
    "forward_deployment_solutions": 22,
    "data_engineering_architecture": 18,
    "bi_powerbi": 14,
    "database_sql": 14,
    "cloud_azure": 12,
    "financial_services": 10,
    "seniority": 10,
}


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


def latest_by_source(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = str(row.get("source", "")).lower()
        if source:
            latest[source] = row
    return latest


def source_type(source: str) -> str:
    if source in REMOTE_JOB_BOARDS:
        return "Remote Job Board"
    if source in GENERAL_JOB_PLATFORMS:
        return "Job Platform"
    if source in MARKETPLACES:
        return "Talent Marketplace"
    if source in RECRUITERS:
        return "Recruiter / Staffing"
    if source in EMPLOYER_CAREERS:
        return "Employer Careers"
    if source in COMMUNITIES_OR_ADVICE:
        return "Community / Career Resource"
    if source in CONTENT_OR_LOW_DIRECTNESS:
        return "Content / Indirect"
    if source in SPECIALIZATION_MISMATCH:
        return "Job Platform - Low Specialization Fit"
    if source in KNOWN_BAD_OR_HIJACKED:
        return "Hijacked / Invalid"
    if source == "crossover.com":
        return "Remote Talent Platform"
    return "Other Candidate Source"


def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(round(max(low, min(high, value))))


def count_hits(profile: dict[str, Any], key: str) -> int:
    value = profile.get(key, []) or []
    return len(value) if isinstance(value, list) else 0


def specialization_fit(profile: dict[str, Any], source: str) -> int:
    groups = profile.get("profile_signal_hits", {}) or {}
    raw = 0
    for group, weight in GROUP_WEIGHTS.items():
        if groups.get(group):
            raw += weight

    # Cap evidence accumulation; a source does not need to mention every stack.
    score = min(100, raw)

    # Protect known strong platforms from JS/anti-bot extraction false negatives.
    if source in KNOWN_HIGH_VALUE_WITH_WEAK_PROFILE:
        score = max(score, 58)

    if source == "careerstructure.com":
        score = min(score, 20)
    if source == "eursap.eu":
        score = min(score, 35)
    if source == "job-hunt.org":
        score = min(score, 25)
    if source in CONTENT_OR_LOW_DIRECTNESS or source in KNOWN_BAD_OR_HIJACKED:
        score = min(score, 15)

    return clamp(score)


def remote_strength(profile: dict[str, Any], source: str) -> int:
    n = count_hits(profile, "remote_hits")
    base = min(100, n * 22)

    if source in REMOTE_JOB_BOARDS or source in {"crossover.com", "remote.com"}:
        base = max(base, 90)
    elif source in MARKETPLACES:
        base = max(base, 55)
    elif source in RECRUITERS:
        base = max(base, 35)
    elif source in EMPLOYER_CAREERS:
        base = min(base, 55)
    elif source in CONTENT_OR_LOW_DIRECTNESS:
        base = min(base, 20)

    return clamp(base)


def geographic_access(profile: dict[str, Any], source: str) -> int:
    eu = count_hits(profile, "eu_ireland_hits")
    glob = count_hits(profile, "global_hits")

    score = min(100, eu * 22 + glob * 12)

    if source.endswith(".ie") or source in {
        "landing.jobs", "eursap.eu", "growremote.ie", "hays.ie",
        "collinsmcnicholas.ie", "talentspot.ie", "reperiohumancapital.com",
        "trustinsoda.com",
    }:
        score = max(score, 75)

    if source in REMOTE_JOB_BOARDS and glob:
        score = max(score, 70)

    if source in {"remotely.jobs", "jobright.ai"} and not eu:
        score = min(score, 45)

    return clamp(score)


def opportunity_density(rel: dict[str, Any], stype: str) -> int:
    links = int(rel.get("Candidate Link Count", 0) or 0)
    score = min(100, links * 2)

    if stype in {"Remote Job Board", "Job Platform", "Talent Marketplace", "Remote Talent Platform"}:
        score = max(score, 70)
    elif stype == "Recruiter / Staffing":
        score = max(score, 55)
    elif stype == "Employer Careers":
        score = min(score, 45)
    elif stype in {"Community / Career Resource", "Content / Indirect"}:
        score = min(score, 30)

    return clamp(score)


def directness(stype: str, source: str) -> int:
    mapping = {
        "Remote Job Board": 95,
        "Job Platform": 90,
        "Talent Marketplace": 90,
        "Remote Talent Platform": 95,
        "Recruiter / Staffing": 85,
        "Employer Careers": 75,
        "Community / Career Resource": 40,
        "Content / Indirect": 15,
        "Job Platform - Low Specialization Fit": 45,
        "Hijacked / Invalid": 0,
        "Other Candidate Source": 50,
    }
    score = mapping.get(stype, 50)
    if source == "job-hunt.org":
        score = 25
    return score


def candidate_cost(profile: dict[str, Any], source: str) -> int:
    # 100 means favorable/low candidate cost.
    friction = count_hits(profile, "cost_friction_hits")
    free = count_hits(profile, "free_access_hits")
    score = 80 - min(45, friction * 12) + min(20, free * 10)

    if source == "flexjobs.com":
        score = min(score, 45)
    if source in RECRUITERS or source in EMPLOYER_CAREERS:
        score = max(score, 90)

    return clamp(score)


def contract_fit(profile: dict[str, Any]) -> int:
    contract = count_hits(profile, "contract_hits")
    permanent = count_hits(profile, "permanent_hits")
    return clamp(min(70, contract * 14) + min(30, permanent * 8))


def hidden_potential(profile: dict[str, Any], source: str, stype: str) -> int:
    groups = int(profile.get("profile_signal_group_count", 0) or 0)
    score = min(80, groups * 10)

    if source in {"crossover.com", "jobgether.com", "wellfound.com", "arc.dev", "remotive.com"}:
        score = max(score, 80)
    if stype == "Recruiter / Staffing":
        score = max(score, 55)
    if stype in {"Community / Career Resource", "Content / Indirect"}:
        score = min(score, 35)

    return clamp(score)


def signal_quality(rel: dict[str, Any], profile: dict[str, Any]) -> int:
    pages_attempted = int(profile.get("pages_attempted", 0) or 0)
    pages_resolved = int(profile.get("pages_resolved", 0) or 0)
    blocked = int(profile.get("blocked_or_shell_pages", 0) or 0)

    if pages_attempted:
        ratio = pages_resolved / pages_attempted
    else:
        ratio = 0.0

    score = 30 + 50 * ratio
    score -= min(35, blocked * 6)

    if rel.get("Verification Status") == "known_source_live_blocked":
        score = max(score, 45)
    if int(rel.get("Email Evidence Count", 0) or 0) >= 3:
        score += 8
    if int(rel.get("Direct Evidence URL Count", 0) or 0) >= 10:
        score += 8

    return clamp(score)


def freshness_activity(rel: dict[str, Any]) -> int:
    email = int(rel.get("Email Evidence Count", 0) or 0)
    direct = int(rel.get("Direct Evidence URL Count", 0) or 0)
    discovery = int(rel.get("Discovery Evidence Count", 0) or 0)

    # Frequency is evidence of activity, not source goodness.
    score = 30
    score += min(30, email * 3)
    score += min(25, direct // 4)
    score += min(15, discovery * 2)
    return clamp(score)


def trust_risk(rel: dict[str, Any], source: str, stype: str) -> int:
    # 100 means trustworthy/low risk.
    score = 80
    if rel.get("Verification Status") == "live_verified":
        score += 10
    if rel.get("Verification Status") == "known_source_live_blocked":
        score -= 5
    if source in KNOWN_BAD_OR_HIJACKED:
        return 0
    if "expired" in " ".join(rel.get("Scoring Reasons", []) or []).lower():
        return 0
    if stype == "Content / Indirect":
        score -= 10
    return clamp(score)


def score_row(rel: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    source = str(rel.get("Source", "")).lower()
    stype = source_type(source)

    dims = {
        "Candidate Fit": specialization_fit(profile, source),
        "Remote Strength": remote_strength(profile, source),
        "Ireland/EU Accessibility": geographic_access(profile, source),
        "Opportunity Density": opportunity_density(rel, stype),
        "Specialization Fit": specialization_fit(profile, source),
        "Hidden Potential": hidden_potential(profile, source, stype),
        "Directness": directness(stype, source),
        "Trust/Risk": trust_risk(rel, source, stype),
        "Candidate Cost": candidate_cost(profile, source),
        "Earning Potential": clamp(
            0.45 * specialization_fit(profile, source)
            + 0.30 * hidden_potential(profile, source, stype)
            + 0.25 * contract_fit(profile)
        ),
        "Signal Quality": signal_quality(rel, profile),
        "Freshness/Activity": freshness_activity(rel),
    }

    weights = {
        "Candidate Fit": 0.15,
        "Remote Strength": 0.13,
        "Ireland/EU Accessibility": 0.12,
        "Opportunity Density": 0.10,
        "Specialization Fit": 0.13,
        "Hidden Potential": 0.08,
        "Directness": 0.08,
        "Trust/Risk": 0.06,
        "Candidate Cost": 0.04,
        "Earning Potential": 0.05,
        "Signal Quality": 0.03,
        "Freshness/Activity": 0.03,
    }

    final_score = sum(dims[k] * weights[k] for k in weights)

    # Keep generic source quality as a bounded stabilizer, not the main score.
    relationship_score = int(rel.get("Relationship Score", 0) or 0)
    final_score = 0.88 * final_score + 0.12 * relationship_score

    if source in KNOWN_BAD_OR_HIJACKED:
        final_score = 0
    if source == "careerstructure.com":
        final_score = min(final_score, 35)
    if source == "eursap.eu":
        final_score = min(final_score, 59)
    if source == "job-hunt.org":
        final_score = min(final_score, 45)
    if source in CONTENT_OR_LOW_DIRECTNESS:
        final_score = min(final_score, 30)

    # Sanity gates: the project is specifically about useful REMOTE-source
    # relationships for this candidate. High generic source quality must not
    # overpower weak remote or geographic evidence.
    if dims["Remote Strength"] < 50:
        final_score = min(final_score, 74)

    # Candidate-facing but indirect/community resources should not rank as
    # primary monitoring channels.
    if stype == "Community / Career Resource":
        final_score = min(final_score, 59)

    # Recruiters/job platforms with no Ireland/EU evidence remain useful, but
    # should stay below Tier B until geography is verified.
    if dims["Ireland/EU Accessibility"] < 30 and stype in {
        "Recruiter / Staffing", "Job Platform", "Other Candidate Source"
    }:
        final_score = min(final_score, 59)

    # A broad remote board with almost no demonstrated specialization fit is
    # still useful for discovery, but should not outrank better-targeted
    # sources.
    if dims["Candidate Fit"] < 30 and stype == "Remote Job Board":
        final_score = min(final_score, 59)

    score = clamp(final_score)

    if score >= 75:
        tier = "A"
        use = "Primary: actively monitor and cultivate"
    elif score >= 60:
        tier = "B"
        use = "Secondary: monitor regularly"
    elif score >= 45:
        tier = "C"
        use = "Selective: use when relevant"
    elif score >= 30:
        tier = "Review"
        use = "Manual review before inclusion"
    else:
        tier = "Exclude"
        use = "Do not prioritize"

    notes = []
    if rel.get("Verification Status") == "known_source_live_blocked":
        notes.append("Live crawler blocked; source retained using known-source and historical evidence.")
    if int(profile.get("blocked_or_shell_pages", 0) or 0):
        notes.append("Profile evidence partially limited by blocked/shell pages.")
    if source in KNOWN_HIGH_VALUE_WITH_WEAK_PROFILE and int(profile.get("profile_signal_group_count", 0) or 0) <= 1:
        notes.append("Low extracted fit signal may reflect JS/anti-bot limitations, not true source weakness.")
    if stype in {"Community / Career Resource", "Content / Indirect"}:
        notes.append("Indirect relationship: useful for discovery/networking more than direct applications.")
    if source == "careerstructure.com":
        notes.append("Legitimate job source but specialization is construction, materially misaligned with target profile.")
    if source == "eursap.eu":
        notes.append("Legitimate recruiter, but its SAP specialization is not a core match for the target profile.")
    if dims["Remote Strength"] < 50:
        notes.append("Remote evidence is limited; score capped below Tier A.")
    if dims["Ireland/EU Accessibility"] < 30 and stype in {
        "Recruiter / Staffing", "Job Platform", "Other Candidate Source"
    }:
        notes.append("Ireland/EU accessibility is not sufficiently evidenced; score capped below Tier B.")
    if dims["Candidate Fit"] < 30 and stype == "Remote Job Board":
        notes.append("Remote source is valid, but demonstrated specialization fit is weak; score capped below Tier B.")
    if source in KNOWN_BAD_OR_HIJACKED:
        notes.append("Current destination appears hijacked/unrelated; exclude.")

    evidence_urls = profile.get("page_urls", []) or []
    verification_url = str(rel.get("Final URL", "") or rel.get("URL", ""))

    return {
        "Source": source,
        "URL": rel.get("URL"),
        "Category": stype,
        "Geographic Focus": "Ireland/EU/Global evidence-based",
        "Specialization": ", ".join(
            group for group, vals in (profile.get("profile_signal_hits", {}) or {}).items() if vals
        ),
        "Candidate Cost": dims["Candidate Cost"],
        "Quality Score": score,
        "Ireland/EU Fit": dims["Ireland/EU Accessibility"],
        "Remote Eligibility Clarity": dims["Remote Strength"],
        "Risk Level": "Low" if dims["Trust/Risk"] >= 75 else ("Medium" if dims["Trust/Risk"] >= 45 else "High"),
        "Recommended Use": use,
        "Last Verified": profile.get("profiled_at_utc") or "",
        "Evidence/Verification URL": verification_url,
        "Notes": " ".join(notes),
        "Candidate ↔ Source Relationship Score": score,
        **dims,
        "Relationship Tier": tier,
        "Generic Relationship Score": relationship_score,
        "Verification Status": rel.get("Verification Status"),
        "Discovery Evidence Count": int(rel.get("Discovery Evidence Count", 0) or 0),
        "Direct Evidence URL Count": int(rel.get("Direct Evidence URL Count", 0) or 0),
        "Email Evidence Count": int(rel.get("Email Evidence Count", 0) or 0),
        "Profile Signal Groups": int(profile.get("profile_signal_group_count", 0) or 0),
        "Representative Evidence URLs": evidence_urls[:8],
        "Scoring Notes": notes,
    }


def main() -> int:
    relationships = load_jsonl(RELATIONSHIPS_PATH)
    profiles = latest_by_source(load_jsonl(PROFILE_PATH))
    unresolved_profiles = latest_by_source(load_jsonl(PROFILE_UNRESOLVED_PATH))

    eligible = [
        r for r in relationships
        if r.get("Relationship Class") in {"strong", "promising", "review"}
    ]

    rows: list[dict[str, Any]] = []
    missing_profiles: list[str] = []

    for rel in eligible:
        source = str(rel.get("Source", "")).lower()
        profile = profiles.get(source)
        if profile is None:
            profile = unresolved_profiles.get(source, {})
        if not profile:
            missing_profiles.append(source)
            profile = {
                "source": source,
                "profile_signal_hits": {},
                "profile_signal_group_count": 0,
                "pages_attempted": 0,
                "pages_resolved": 0,
            }
        rows.append(score_row(rel, profile))

    rows.sort(key=lambda r: (-int(r.get("Quality Score", 0) or 0), str(r.get("Source", ""))))

    included = [r for r in rows if r.get("Relationship Tier") in {"A", "B", "C"}]
    review = [r for r in rows if r.get("Relationship Tier") == "Review"]
    excluded = [r for r in rows if r.get("Relationship Tier") == "Exclude"]

    write_jsonl(OUTPUT_PATH, included)
    write_jsonl(REVIEW_PATH, review)
    write_jsonl(EXCLUDED_PATH, excluded)

    summary = {
        "status": "ok",
        "eligible_relationships": len(eligible),
        "profiled_distinct_sources": len(profiles),
        "profile_unresolved_distinct_sources": len(unresolved_profiles),
        "missing_profiles": missing_profiles,
        "tier_A": sum(1 for r in included if r.get("Relationship Tier") == "A"),
        "tier_B": sum(1 for r in included if r.get("Relationship Tier") == "B"),
        "tier_C": sum(1 for r in included if r.get("Relationship Tier") == "C"),
        "review": len(review),
        "excluded": len(excluded),
        "included_source_records": len(included),
        "source_records_output": str(OUTPUT_PATH),
        "review_output": str(REVIEW_PATH),
        "excluded_output": str(EXCLUDED_PATH),
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
