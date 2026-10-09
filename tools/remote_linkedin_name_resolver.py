#!/usr/bin/env python3
"""
remote_linkedin_name_resolver.py

Resolve LinkedIn employer/source names to canonical candidate domains using a
small, explicit, auditable mapping plus already-extracted domain evidence.

Inputs:
  <run>/harvest/linkedin/candidate_source_evidence.jsonl
  <run>/harvest/linkedin/needs_resolution.jsonl

Outputs:
  <run>/harvest/linkedin/resolved_candidate_source_evidence.jsonl
  <run>/harvest/linkedin/still_needs_resolution.jsonl
  <run>/harvest/linkedin/resolution_manifest.json

Resolution policy:
- if the name clearly corresponds to a domain already extracted from the same
  LinkedIn harvest, reuse that domain;
- otherwise resolve only through explicit verified mappings;
- never guess a domain from a company/source name;
- unresolved targets remain unresolved/check-later.

No live validation, scoring, Excel publication, or watermark advancement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


# Explicit verified mappings only. These are intentionally narrow.
VERIFIED_NAME_TO_DOMAIN = {
    "accenture uk & ireland": "accenture.com",
    "accenture": "accenture.com",
    "kraken": "kraken.com",
    "platform engineering": "platformengineering.org",
    "servicenow": "servicenow.com",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def latest_run_dir(root: Path) -> Path:
    runs_dir = root / "data" / "remote" / "refresh_runs"
    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"No refresh run directories found under {runs_dir}")
    return candidates[0]


def norm_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    linkedin_dir = run_dir / "harvest" / "linkedin"

    evidence_path = linkedin_dir / "candidate_source_evidence.jsonl"
    unresolved_path = linkedin_dir / "needs_resolution.jsonl"

    if not evidence_path.exists():
        raise RuntimeError(f"LinkedIn evidence not found: {evidence_path}")
    if not unresolved_path.exists():
        raise RuntimeError(f"LinkedIn unresolved input not found: {unresolved_path}")

    evidence = read_jsonl(evidence_path)
    unresolved = read_jsonl(unresolved_path)

    explicit_domains = {
        str(r.get("candidate_domain") or "").lower()
        for r in evidence
        if r.get("candidate_domain")
    }

    # Preserve explicit domain evidence and resolve name-only evidence.
    resolved_evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for row in evidence:
        linkedin_url = str(row.get("linkedin_url") or "")
        domain = str(row.get("candidate_domain") or "").lower().strip()

        if domain:
            key = (linkedin_url, domain)
            if key not in seen:
                seen.add(key)
                resolved_evidence.append(row)
            continue

        name = str(row.get("candidate_name") or "").strip()
        domain = VERIFIED_NAME_TO_DOMAIN.get(norm_name(name), "")
        if not domain:
            continue

        key = (linkedin_url, domain)
        if key in seen:
            continue
        seen.add(key)

        provenance = (
            "resolved_to_domain_already_present_in_linkedin_evidence"
            if domain in explicit_domains
            else "explicit_verified_name_to_domain_mapping"
        )

        resolved_evidence.append({
            **row,
            "evidence_type": "linkedin_name_resolved_to_candidate_domain",
            "resolution_provenance": provenance,
            "candidate_domain": domain,
            "candidate_url": f"https://{domain}/",
            "confidence": "medium",
            "status": "needs_validation",
        })

    # Determine which LinkedIn targets still lack any resolved candidate domain.
    resolved_urls = {
        str(r.get("linkedin_url") or "")
        for r in resolved_evidence
        if r.get("candidate_domain")
    }

    still_unresolved = [
        row for row in unresolved
        if str(row.get("linkedin_url") or "") not in resolved_urls
    ]

    out_evidence = linkedin_dir / "resolved_candidate_source_evidence.jsonl"
    out_unresolved = linkedin_dir / "still_needs_resolution.jsonl"
    manifest_path = linkedin_dir / "resolution_manifest.json"

    write_jsonl(out_evidence, resolved_evidence)
    write_jsonl(out_unresolved, still_unresolved)

    domains = sorted({
        str(r.get("candidate_domain"))
        for r in resolved_evidence
        if r.get("candidate_domain")
    })

    manifest = {
        "status": "ok",
        "input_evidence_rows": len(evidence),
        "resolved_evidence_rows": len(resolved_evidence),
        "distinct_candidate_domains": len(domains),
        "candidate_domains": domains,
        "still_unresolved_targets": len(still_unresolved),
        "resolved_output": str(out_evidence),
        "unresolved_output": str(out_unresolved),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE LINKEDIN NAME RESOLUTION OK")
    print("=" * 58)
    print(f"Run directory:                  {run_dir}")
    print(f"Input evidence rows:            {len(evidence)}")
    print(f"Resolved evidence rows:         {len(resolved_evidence)}")
    print(f"Distinct candidate domains:     {len(domains)}")
    print(f"Still unresolved targets:       {len(still_unresolved)}")
    print()
    print("RESOLVED CANDIDATE DOMAINS")
    for domain in domains:
        print(f"  {domain}")
    if not domains:
        print("  [NONE]")
    print()
    print("No live source validation was performed.")
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
