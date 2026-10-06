#!/usr/bin/env python3
"""High-recall expansion over semantic-review sources.

Reads:
- data/remote/source_semantic_review.jsonl
- data/remote/source_records.jsonl

Writes:
- data/remote/recall_expansion_queue.jsonl
- data/remote/recall_expansion_deferred.jsonl
- data/remote/recall_expansion_seed_missing.jsonl
- data/remote/recall_expansion_summary.json

This stage ranks candidates for secondary validation. It never writes Excel.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]

SEMANTIC_REVIEW_PATH = Path(os.getenv(
    "REMOTE_SOURCE_SEMANTIC_REVIEW",
    ROOT_DIR / "data/remote/source_semantic_review.jsonl",
))
BASELINE_PATH = Path(os.getenv(
    "REMOTE_SOURCE_RECORDS",
    ROOT_DIR / "data/remote/source_records.jsonl",
))
QUEUE_PATH = Path(os.getenv(
    "REMOTE_RECALL_EXPANSION_QUEUE",
    ROOT_DIR / "data/remote/recall_expansion_queue.jsonl",
))
DEFERRED_PATH = Path(os.getenv(
    "REMOTE_RECALL_EXPANSION_DEFERRED",
    ROOT_DIR / "data/remote/recall_expansion_deferred.jsonl",
))
SEED_MISSING_PATH = Path(os.getenv(
    "REMOTE_RECALL_EXPANSION_SEED_MISSING",
    ROOT_DIR / "data/remote/recall_expansion_seed_missing.jsonl",
))
SUMMARY_PATH = Path(os.getenv(
    "REMOTE_RECALL_EXPANSION_SUMMARY",
    ROOT_DIR / "data/remote/recall_expansion_summary.json",
))

MANUAL_SEEDS: dict[str, str] = {
    "linkedin.com": "https://www.linkedin.com/jobs/",
    "himalayas.app": "https://himalayas.app/",
    "workingnomads.com": "https://www.workingnomads.com/",
    "jobicy.com": "https://jobicy.com/",
    "ycombinator.com": "https://www.ycombinator.com/jobs",
    "dice.com": "https://www.dice.com/",
    "builtin.com": "https://builtin.com/jobs/remote",
    "braintrust.com": "https://www.usebraintrust.com/",
    "a.team": "https://www.a.team/",
    "malt.com": "https://www.malt.com/",
    "catalant.com": "https://catalant.com/",
    "contra.com": "https://contra.com/",
    "openjobseu.com": "https://www.openjobseu.com/",
}

COMMON_MULTI_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "ac.uk",
    "com.au", "net.au", "org.au",
    "co.nz", "org.nz", "co.za", "org.za",
    "com.br", "com.mx", "com.sg", "co.in", "org.in",
    "co.jp", "com.cn", "com.hk",
}

SOURCE_TERMS = (
    "job", "jobs", "career", "careers", "recruit", "recruitment",
    "staffing", "talent", "freelance", "contract", "consult",
    "hiring", "vacanc", "employment", "workforce",
)
REMOTE_TERMS = (
    "remote", "distributed", "work from home", "work-from-home",
    "anywhere", "worldwide", "global remote",
)
TARGET_TERMS = (
    " ai ", "artificial intelligence", "machine learning", "agentic",
    "data", "database", "sql", "mongodb", "power bi", "analytics",
    "cloud", "azure", "architecture", "architect", "platform",
    "engineering", "engineer", "developer", "fintech", "banking", "risk",
)
GEO_TERMS = (
    "ireland", "irish", "dublin", "europe", "european", "emea",
    "united kingdom",
)
INDIRECT_TERMS = (
    "news", "blog", "magazine", "journal", "podcast", "press", "article",
    "research", "university", "library", "media",
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


def clean_host(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        try:
            raw = urlparse(raw).hostname or ""
        except Exception:
            return ""
    raw = raw.strip(".")
    return raw[4:] if raw.startswith("www.") else raw


def registered_domain(host: str) -> str:
    host = clean_host(host)
    if not host:
        return ""
    try:
        import tldextract  # type: ignore
        ext = tldextract.extract(host)
        if ext.domain and ext.suffix:
            return f"{ext.domain}.{ext.suffix}".lower()
    except Exception:
        pass
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    suffix2 = ".".join(parts[-2:])
    if suffix2 in COMMON_MULTI_LABEL_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def identity(row: dict[str, Any]) -> str:
    host = clean_host(str(row.get("canonical_host", "")))
    if not host:
        host = clean_host(str(row.get("canonical_root_url", "")))
    return registered_domain(host)


def flatten(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value or "")


def surface_text(row: dict[str, Any]) -> str:
    text = " ".join([
        str(row.get("canonical_host", "")),
        str(row.get("canonical_root_url", "")),
        flatten(row, "source_hint_urls"),
        flatten(row, "representative_urls"),
        flatten(row, "source_subject_examples"),
        flatten(row, "source_sender_examples"),
        str(row.get("prevalidation_reason", "")),
    ]).lower()
    return re.sub(r"\s+", " ", text).strip()


def evidence_total(row: dict[str, Any]) -> int:
    return (
        int(row.get("evidence_url_count", 0) or 0)
        + int(row.get("discovery_resolution_evidence_count", 0) or 0)
        + int(row.get("email_evidence_count", 0) or 0)
    )


def hits(text: str, terms: tuple[str, ...]) -> list[str]:
    return sorted({term for term in terms if term in text})


def baseline_identities(rows: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        for raw in (str(row.get("Source", "")), str(row.get("URL", ""))):
            value = registered_domain(raw)
            if value:
                out.add(value)
    return out


def score(row: dict[str, Any], source_id: str) -> tuple[int, str, dict[str, Any]]:
    text = surface_text(row)
    source_hits = hits(text, SOURCE_TERMS)
    remote_hits = hits(text, REMOTE_TERMS)
    target_hits = hits(text, TARGET_TERMS)
    geo_hits = hits(text, GEO_TERMS)
    indirect_hits = hits(text, INDIRECT_TERMS)
    recurrence = evidence_total(row)

    value = 10
    value += min(32, len(source_hits) * 8)
    value += min(18, len(remote_hits) * 9)
    value += min(24, len(target_hits) * 4)
    value += min(12, len(geo_hits) * 4)
    value += min(10, recurrence // 5)
    value += min(6, int(row.get("prevalidation_score", 0) or 0) // 15)

    if source_id in MANUAL_SEEDS:
        value += 35
    if indirect_hits and not source_hits:
        value -= min(30, len(indirect_hits) * 8)

    value = max(0, min(100, value))

    if source_id in MANUAL_SEEDS or value >= 65:
        priority = "P1"
    elif value >= 45:
        priority = "P2"
    elif value >= 30:
        priority = "P3"
    else:
        priority = "deferred"

    return value, priority, {
        "source_terms": source_hits,
        "remote_terms": remote_hits,
        "target_terms": target_hits,
        "eu_ireland_terms": geo_hits,
        "indirect_terms": indirect_hits,
        "evidence_total": recurrence,
    }


def seed_record(source_id: str, url: str) -> dict[str, Any]:
    return {
        "canonical_host": source_id,
        "canonical_root_url": url,
        "source_hint_urls": [url],
        "representative_urls": [url],
        "source_subject_examples": [],
        "source_sender_examples": [],
        "evidence_url_count": 0,
        "discovery_resolution_evidence_count": 0,
        "email_evidence_count": 0,
        "prevalidation_bucket": "manual_seed",
        "prevalidation_score": 100,
        "prevalidation_reason": "manual_research_seed",
        "recall_seed_origin": "manual_researched_source_universe",
    }


def main() -> int:
    if not SEMANTIC_REVIEW_PATH.exists():
        print(f"Fatal error: input not found: {SEMANTIC_REVIEW_PATH}", file=sys.stderr)
        return 1
    if not BASELINE_PATH.exists():
        print(f"Fatal error: baseline not found: {BASELINE_PATH}", file=sys.stderr)
        return 1

    review_rows = load_jsonl(SEMANTIC_REVIEW_PATH)
    baseline_rows = load_jsonl(BASELINE_PATH)
    baseline = baseline_identities(baseline_rows)

    grouped: dict[str, list[dict[str, Any]]] = {}
    invalid_identity_rows = 0
    for row in review_rows:
        source_id = identity(row)
        if not source_id:
            invalid_identity_rows += 1
            continue
        grouped.setdefault(source_id, []).append(row)

    consolidated: dict[str, dict[str, Any]] = {}
    for source_id, rows in grouped.items():
        rows = sorted(
            rows,
            key=lambda r: (
                -evidence_total(r),
                -int(r.get("prevalidation_score", 0) or 0),
                str(r.get("canonical_host", "")),
            ),
        )
        rep = dict(rows[0])
        rep["recall_canonical_identity"] = source_id
        rep["recall_group_member_count"] = len(rows)
        rep["recall_group_hosts"] = sorted({
            clean_host(str(r.get("canonical_host", "")))
            for r in rows if clean_host(str(r.get("canonical_host", "")))
        })
        rep["recall_group_evidence_total"] = sum(evidence_total(r) for r in rows)
        consolidated[source_id] = rep

    injected: list[dict[str, Any]] = []
    for source_id, url in MANUAL_SEEDS.items():
        rid = registered_domain(source_id)
        if rid in baseline:
            continue
        if rid not in consolidated:
            row = seed_record(rid, url)
            row["recall_canonical_identity"] = rid
            row["recall_group_member_count"] = 1
            row["recall_group_hosts"] = [rid]
            row["recall_group_evidence_total"] = 0
            consolidated[rid] = row
            injected.append(dict(row))

    queue: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    baseline_overlaps = 0
    priority_counts = {"P1": 0, "P2": 0, "P3": 0, "deferred": 0}

    for source_id, row in consolidated.items():
        if source_id in baseline:
            baseline_overlaps += 1
            continue

        recall_score, priority, signals = score(row, source_id)
        out = dict(row)
        out["recall_score"] = recall_score
        out["recall_priority"] = priority
        out["recall_signals"] = signals

        priority_counts[priority] += 1
        if priority == "deferred":
            deferred.append(out)
        else:
            queue.append(out)

    queue.sort(key=lambda r: (
        {"P1": 0, "P2": 1, "P3": 2}.get(str(r.get("recall_priority")), 9),
        -int(r.get("recall_score", 0) or 0),
        -int(r.get("recall_group_evidence_total", 0) or 0),
        str(r.get("recall_canonical_identity", "")),
    ))
    deferred.sort(key=lambda r: (
        -int(r.get("recall_score", 0) or 0),
        -int(r.get("recall_group_evidence_total", 0) or 0),
        str(r.get("recall_canonical_identity", "")),
    ))

    write_jsonl(QUEUE_PATH, queue)
    write_jsonl(DEFERRED_PATH, deferred)
    write_jsonl(SEED_MISSING_PATH, injected)

    summary = {
        "status": "ok",
        "semantic_review_input_rows": len(review_rows),
        "golden_baseline_rows": len(baseline_rows),
        "golden_baseline_canonical_identities": len(baseline),
        "semantic_review_consolidated_identities": len(grouped),
        "invalid_identity_rows": invalid_identity_rows,
        "manual_seeds_configured": len(MANUAL_SEEDS),
        "manual_seeds_injected": len(injected),
        "baseline_overlaps_removed": baseline_overlaps,
        "recall_queue_sources": len(queue),
        "recall_deferred_sources": len(deferred),
        "priority_counts": priority_counts,
        "queue_output": str(QUEUE_PATH),
        "deferred_output": str(DEFERRED_PATH),
        "manual_seed_missing_output": str(SEED_MISSING_PATH),
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
