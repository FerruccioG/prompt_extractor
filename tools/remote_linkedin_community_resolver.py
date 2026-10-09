#!/usr/bin/env python3
"""
remote_linkedin_community_resolver.py

Resolve unresolved LinkedIn community/group evidence to canonical external
community domains when the original Remote email contains an explicit
organization-owned email address.

Inputs:
  <run>/harvest/linkedin/resolved_candidate_source_evidence.jsonl
  <run>/harvest/linkedin/dedupe/still_needs_resolution.jsonl

Outputs:
  <run>/harvest/linkedin/resolved_candidate_source_evidence_v2.jsonl
  <run>/harvest/linkedin/still_needs_resolution_v2.jsonl
  <run>/harvest/linkedin/community_resolution_manifest.json

Policy:
- use only explicit email-address domains present in the preserved email evidence;
- ignore generic/public mailbox domains and LinkedIn infrastructure;
- do not guess domains from names;
- preserve provenance linking every resolved row to the LinkedIn target and email.

No live validation, scoring, Excel publication, or watermark advancement.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b",
    re.IGNORECASE,
)

EXCLUDED_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
    "live.com", "icloud.com", "yahoo.com", "proton.me", "protonmail.com",
    "linkedin.com", "licdn.com",
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


def external_email_domains(text: str) -> list[str]:
    domains = []
    seen = set()
    for match in EMAIL_RE.finditer(text or ""):
        domain = match.group(1).lower().strip(".")
        if domain in EXCLUDED_EMAIL_DOMAINS:
            continue
        if domain.endswith(".linkedin.com") or domain.endswith(".licdn.com"):
            continue
        if domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    linkedin_dir = run_dir / "harvest" / "linkedin"

    resolved_input = linkedin_dir / "resolved_candidate_source_evidence.jsonl"
    unresolved_input = linkedin_dir / "dedupe" / "still_needs_resolution.jsonl"

    if not resolved_input.exists():
        raise RuntimeError(f"Resolved LinkedIn evidence not found: {resolved_input}")
    if not unresolved_input.exists():
        raise RuntimeError(f"Unresolved LinkedIn evidence not found: {unresolved_input}")

    resolved_rows = read_jsonl(resolved_input)
    unresolved_rows = read_jsonl(unresolved_input)

    out_resolved = list(resolved_rows)
    still_unresolved: list[dict[str, Any]] = []
    added_rows = 0
    resolved_targets = 0
    domains_found: set[str] = set()

    for row in unresolved_rows:
        email_text = str(row.get("email_text_excerpt") or "")
        domains = external_email_domains(email_text)

        if not domains:
            still_unresolved.append(row)
            continue

        # Conservative rule: only auto-resolve when the evidence exposes exactly
        # one organization-owned external email domain.
        if len(domains) != 1:
            still_unresolved.append({
                **row,
                "community_resolution_status": "ambiguous_external_email_domains",
                "external_email_domains": domains,
            })
            continue

        domain = domains[0]
        domains_found.add(domain)
        resolved_targets += 1
        added_rows += 1

        out_resolved.append({
            "linkedin_url": row.get("linkedin_url"),
            "final_linkedin_url": row.get("final_linkedin_url"),
            "content_kind": row.get("content_kind"),
            "source_message_uid": row.get("source_message_uid"),
            "source_subject": row.get("source_subject"),
            "evidence_type": "organization_email_domain_in_linkedin_notification",
            "evidence_provenance": "original_remote_email_text",
            "resolution_provenance": "explicit_organization_owned_email_domain",
            "ocr_observed": None,
            "candidate_name": "Remote Workers Worldwide"
                if domain == "remoteworkersworldwide.co" else None,
            "candidate_domain": domain,
            "candidate_url": f"https://{domain}/",
            "confidence": "high",
            "status": "needs_validation",
        })

    resolved_output = linkedin_dir / "resolved_candidate_source_evidence_v2.jsonl"
    unresolved_output = linkedin_dir / "still_needs_resolution_v2.jsonl"
    manifest_path = linkedin_dir / "community_resolution_manifest.json"

    write_jsonl(resolved_output, out_resolved)
    write_jsonl(unresolved_output, still_unresolved)

    manifest = {
        "status": "ok",
        "input_resolved_evidence_rows": len(resolved_rows),
        "input_unresolved_targets": len(unresolved_rows),
        "resolved_targets_from_email_domain": resolved_targets,
        "added_evidence_rows": added_rows,
        "external_domains_found": sorted(domains_found),
        "remaining_unresolved_targets": len(still_unresolved),
        "resolved_output": str(resolved_output),
        "unresolved_output": str(unresolved_output),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE LINKEDIN COMMUNITY RESOLUTION OK")
    print("=" * 58)
    print(f"Run directory:                       {run_dir}")
    print(f"Input unresolved targets:            {len(unresolved_rows)}")
    print(f"Resolved from explicit email domain: {resolved_targets}")
    print(f"Remaining unresolved targets:        {len(still_unresolved)}")
    print()
    print("RESOLVED EXTERNAL DOMAINS")
    if domains_found:
        for domain in sorted(domains_found):
            print(f"  {domain}")
    else:
        print("  [NONE]")
    print()
    print("No live validation was performed.")
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
