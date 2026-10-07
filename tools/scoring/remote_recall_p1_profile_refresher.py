#!/usr/bin/env python3
"""Refresh Recall P1 candidate profiles with targeted verification evidence.

Inputs:
- data/remote/recall_p1_candidate_profiles.jsonl
- data/remote/recall_p1_targeted_verification_results.jsonl
- data/remote/recall_p1_targeted_verification_unresolved.jsonl

Output:
- data/remote/recall_p1_candidate_profiles_refreshed.jsonl

The refresher preserves the original profile and only augments evidence found
during targeted verification. It does not modify Golden baseline files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

PROFILE_PATH = Path(os.getenv(
    "REMOTE_RECALL_PROFILE_INPUT",
    ROOT_DIR / "data/remote/recall_p1_candidate_profiles.jsonl",
))
TARGETED_RESULTS_PATH = Path(os.getenv(
    "REMOTE_RECALL_TARGETED_RESULTS",
    ROOT_DIR / "data/remote/recall_p1_targeted_verification_results.jsonl",
))
TARGETED_UNRESOLVED_PATH = Path(os.getenv(
    "REMOTE_RECALL_TARGETED_UNRESOLVED",
    ROOT_DIR / "data/remote/recall_p1_targeted_verification_unresolved.jsonl",
))
OUTPUT_PATH = Path(os.getenv(
    "REMOTE_RECALL_PROFILE_REFRESHED_OUTPUT",
    ROOT_DIR / "data/remote/recall_p1_candidate_profiles_refreshed.jsonl",
))

GROUP_TERMS = {
    "ai_agentic": (
        "artificial intelligence", "machine learning", "generative ai", "agentic",
    ),
    "forward_deployment_solutions": (
        "solutions architect", "solution architect", "technical consultant",
    ),
    "data_engineering_architecture": (
        "data engineer", "data engineering", "data architect", "data platform",
    ),
    "bi_powerbi": (
        "power bi", "business intelligence",
    ),
    "database_sql": (
        "sql", "mongodb", "database",
    ),
    "cloud_azure": (
        "azure", "cloud architect", "cloud engineer",
    ),
    "financial_services": (
        "financial services", "banking", "fintech", "risk",
    ),
    "seniority": (
        "senior", "lead", "principal", "architect",
    ),
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


def uniq(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        v = str(value)
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def targeted_index() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in (TARGETED_RESULTS_PATH, TARGETED_UNRESOLVED_PATH):
        for row in load_jsonl(path):
            source = str(row.get("source", "")).lower()
            if source:
                out[source] = row
    return out


def map_profile_hits(flat_hits: list[str]) -> dict[str, list[str]]:
    result = {group: [] for group in GROUP_TERMS}
    for group, terms in GROUP_TERMS.items():
        for hit in flat_hits:
            low = str(hit).lower()
            if any(term in low or low in term for term in terms):
                result[group].append(str(hit))
    return {k: uniq(v) for k, v in result.items()}


def main() -> int:
    profiles = load_jsonl(PROFILE_PATH)
    targeted = targeted_index()

    refreshed: list[dict[str, Any]] = []
    augmented = 0
    unresolved_targeted = 0

    for profile in profiles:
        source = str(profile.get("source", "")).lower()
        t = targeted.get(source)
        out = dict(profile)

        if t:
            if t.get("status") == "unresolved":
                unresolved_targeted += 1

            out["remote_hits"] = uniq(
                list(out.get("remote_hits", []) or [])
                + list(t.get("remote_hits", []) or [])
            )
            out["eu_ireland_hits"] = uniq(
                list(out.get("eu_ireland_hits", []) or [])
                + list(t.get("eu_ireland_hits", []) or [])
            )
            out["contract_hits"] = uniq(
                list(out.get("contract_hits", []) or [])
                + list(t.get("contract_hits", []) or [])
            )

            existing_groups = dict(out.get("profile_signal_hits", {}) or {})
            targeted_groups = map_profile_hits(list(t.get("profile_hits", []) or []))
            for group in GROUP_TERMS:
                existing_groups[group] = uniq(
                    list(existing_groups.get(group, []) or [])
                    + list(targeted_groups.get(group, []) or [])
                )
            out["profile_signal_hits"] = existing_groups
            out["profile_signal_group_count"] = sum(
                1 for vals in existing_groups.values() if vals
            )
            out["profile_signal_total_hits"] = sum(
                len(vals) for vals in existing_groups.values()
            )

            target_urls = list(t.get("target_urls", []) or [])
            out["page_urls"] = uniq(
                list(out.get("page_urls", []) or []) + target_urls
            )
            out["pages_attempted"] = int(out.get("pages_attempted", 0) or 0) + len(target_urls)
            out["pages_resolved"] = int(out.get("pages_resolved", 0) or 0) + int(
                t.get("resolved_pages", 0) or 0
            )
            out["targeted_verification_status"] = t.get("status")
            out["targeted_verification_remote_hits"] = t.get("remote_hits", [])
            out["targeted_verification_eu_ireland_hits"] = t.get("eu_ireland_hits", [])
            out["targeted_verification_profile_hits"] = t.get("profile_hits", [])
            out["targeted_verification_contract_hits"] = t.get("contract_hits", [])
            augmented += 1

        refreshed.append(out)

    refreshed.sort(key=lambda r: str(r.get("source", "")))
    write_jsonl(OUTPUT_PATH, refreshed)

    print(json.dumps({
        "status": "ok",
        "profiles_input": len(profiles),
        "profiles_augmented": augmented,
        "targeted_unresolved_sources": unresolved_targeted,
        "profiles_written": len(refreshed),
        "output": str(OUTPUT_PATH),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
