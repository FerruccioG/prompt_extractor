#!/usr/bin/env python3
"""Gate Recall Expansion P3 Batch 01 after live validation.

P3 is lower-priority and substantially noisier than P1/P2, so this gate is
more conservative. It separates:
- targeted_review: plausible durable Candidate <-> Source relationships worth
  deeper source-specific review;
- indirect_keep: useful technical/content/community ecosystems, but not direct
  remote-opportunity sources;
- baseline_overlap: already represented in Golden;
- reject_or_defer: tracking, transport, generic media, infrastructure, or weak
  evidence.

Inputs:
- data/remote/recall_p3_validation_batch_01.jsonl
- data/remote/recall_p3_batch01_validation_results.jsonl
- data/remote/recall_p3_batch01_validation_unresolved.jsonl
- data/remote/source_records_recall_p1_expanded.jsonl

Outputs:
- data/remote/recall_p3_batch01_targeted_review.jsonl
- data/remote/recall_p3_batch01_indirect_keep.jsonl
- data/remote/recall_p3_batch01_baseline_overlap.jsonl
- data/remote/recall_p3_batch01_rejected_or_deferred.jsonl
- data/remote/recall_p3_batch01_gate_summary.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]

QUEUE_PATH = ROOT_DIR / "data/remote/recall_p3_validation_batch_01.jsonl"
RESULTS_PATH = ROOT_DIR / "data/remote/recall_p3_batch01_validation_results.jsonl"
UNRESOLVED_PATH = ROOT_DIR / "data/remote/recall_p3_batch01_validation_unresolved.jsonl"
BASELINE_PATH = ROOT_DIR / "data/remote/source_records_recall_p1_expanded.jsonl"

TARGET_PATH = ROOT_DIR / "data/remote/recall_p3_batch01_targeted_review.jsonl"
INDIRECT_PATH = ROOT_DIR / "data/remote/recall_p3_batch01_indirect_keep.jsonl"
OVERLAP_PATH = ROOT_DIR / "data/remote/recall_p3_batch01_baseline_overlap.jsonl"
REJECT_PATH = ROOT_DIR / "data/remote/recall_p3_batch01_rejected_or_deferred.jsonl"
SUMMARY_PATH = ROOT_DIR / "data/remote/recall_p3_batch01_gate_summary.json"

# Plausible durable candidate/company/recruitment relationships in this batch.
# Inclusion here means "review more deeply", never automatic promotion.
TARGETED_HOSTS = {
    "trilogyinternational.com": "recruitment_or_staffing_candidate",
    "eurodyn.com": "technology_employer_candidate",
    "infinityquest.co.uk": "recruitment_or_consulting_candidate",
    "i-q.co": "recruitment_or_consulting_candidate",
    "accesa.eu": "technology_employer_candidate",
    "business-umbrella.com": "contractor_umbrella_candidate",
    "cloudpeeps.com": "freelance_marketplace_candidate",
    "hireez.com": "talent_platform_candidate",
}

INDIRECT_HOSTS = {
    "bleepingcomputer.com": "technology_media",
    "visualstudiomagazine.com": "microsoft_technology_media",
    "virtualizationreview.com": "technology_media",
    "gallup.com": "workplace_research",
    "talentgarden.com": "technology_learning_community",
}

INFRA_SUFFIXES = (
    "penton.com",
    "forwardtomyfriend.com",
    "gravatar.com",
    "cneteu.net",
    "tldrnewsletter.com",
    "emedia.co.uk",
    "ml-links.com",
)

INFRA_EXACT = {
    "schema.org", "wp.me", "t.me", "signaturehound.com",
}

GENERIC_LOW_VALUE = {
    "amazon.com", "newsweek.com", "livingsocial.com",
    "gestiondemantenimiento.com",
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


def host_of(value: str) -> str:
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = (parsed.hostname or "").lower().strip(".")
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


def baseline_hosts() -> set[str]:
    out: set[str] = set()
    for row in load_jsonl(BASELINE_PATH):
        source = str(row.get("Source", "")).lower().strip()
        if source:
            out.add(source)
        url_host = host_of(str(row.get("URL", "") or ""))
        if url_host:
            out.add(url_host)
    return out


def infra(host: str) -> bool:
    if host in INFRA_EXACT:
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in INFRA_SUFFIXES)


def classify(row: dict[str, Any], v: dict[str, Any] | None, baseline: set[str]) -> tuple[str, str]:
    original = str(row.get("canonical_host", "")).lower()
    v = v or {}
    final_host = host_of(str(v.get("final_url", "") or ""))
    resolved = bool(v.get("resolved"))
    candidate_links = int(v.get("candidate_link_count", 0) or 0)
    remote_hits = len(v.get("remote_term_hits", []) or [])

    identities = {h for h in (original, final_host) if h}

    if identities & baseline:
        return "baseline_overlap", "already_represented_in_current_golden"

    for h in identities:
        if h in TARGETED_HOSTS:
            return "targeted_review", TARGETED_HOSTS[h]

    for h in identities:
        if h in INDIRECT_HOSTS:
            return "indirect_keep", INDIRECT_HOSTS[h]

    if any(infra(h) for h in identities):
        return "reject_or_defer", "tracking_transport_or_technical_infrastructure"

    if identities & GENERIC_LOW_VALUE:
        return "reject_or_defer", "generic_or_non_candidate_source"

    # Preserve unusually candidate-facing surfaces even when not preclassified.
    if resolved and candidate_links >= 8:
        return "targeted_review", "high_candidate_link_density_requires_manual_relationship_review"

    # A weak page with a remote token is not enough in P3.
    if resolved and candidate_links >= 3 and remote_hits >= 2:
        return "targeted_review", "moderate_candidate_links_plus_remote_signal"

    if not resolved:
        return "reject_or_defer", "unresolved_without_known_durable_candidate_identity"

    if candidate_links <= 2:
        return "reject_or_defer", "weak_candidate_signal"

    return "reject_or_defer", "insufficient_p3_evidence_for_deeper_review"


def main() -> int:
    queue = load_jsonl(QUEUE_PATH)
    validation = validation_index()
    baseline = baseline_hosts()

    target: list[dict[str, Any]] = []
    indirect: list[dict[str, Any]] = []
    overlap: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    missing: list[str] = []

    for row in queue:
        key = str(row.get("canonical_host", "")).lower()
        v = validation.get(key)
        if v is None:
            missing.append(key)

        bucket, reason = classify(row, v)
        out = dict(row)
        out["p3_batch01_gate_bucket"] = bucket
        out["p3_batch01_gate_reason"] = reason
        out["p3_validation"] = v or {}

        if bucket == "targeted_review":
            target.append(out)
        elif bucket == "indirect_keep":
            indirect.append(out)
        elif bucket == "baseline_overlap":
            overlap.append(out)
        else:
            rejected.append(out)

    def sort_key(r: dict[str, Any]) -> tuple[int, str]:
        v = r.get("p3_validation", {}) or {}
        return (
            -int(v.get("candidate_link_count", 0) or 0),
            str(r.get("recall_canonical_identity") or r.get("canonical_host") or ""),
        )

    for rows in (target, indirect, overlap, rejected):
        rows.sort(key=sort_key)

    write_jsonl(TARGET_PATH, target)
    write_jsonl(INDIRECT_PATH, indirect)
    write_jsonl(OVERLAP_PATH, overlap)
    write_jsonl(REJECT_PATH, rejected)

    summary = {
        "status": "ok",
        "batch_input_sources": len(queue),
        "validation_records_found": len(queue) - len(missing),
        "validation_records_missing": missing,
        "targeted_review_sources": len(target),
        "indirect_keep_sources": len(indirect),
        "baseline_overlap_sources": len(overlap),
        "rejected_or_deferred_sources": len(rejected),
        "accounted_sources": len(target) + len(indirect) + len(overlap) + len(rejected),
        "targeted_review_identities": [
            str(r.get("recall_canonical_identity") or r.get("canonical_host") or "")
            for r in target
        ],
        "indirect_keep_identities": [
            str(r.get("recall_canonical_identity") or r.get("canonical_host") or "")
            for r in indirect
        ],
        "baseline_overlap_identities": [
            str(r.get("recall_canonical_identity") or r.get("canonical_host") or "")
            for r in overlap
        ],
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
