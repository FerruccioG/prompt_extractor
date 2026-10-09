#!/usr/bin/env python3
"""
remote_linkedin_candidate_dedupe.py

Run-scoped hard-gate dedupe for candidate sources extracted/resolved from
LinkedIn evidence.

Inputs:
  <run>/harvest/linkedin/resolved_candidate_source_evidence.jsonl
  <run>/harvest/linkedin/still_needs_resolution.jsonl

Historical universe:
  root-level data/remote/*.jsonl only

Outputs:
  <run>/harvest/linkedin/dedupe/new_source_validation_queue.jsonl
  <run>/harvest/linkedin/dedupe/already_known_source_evidence.jsonl
  <run>/harvest/linkedin/dedupe/still_needs_resolution.jsonl
  <run>/harvest/linkedin/dedupe/dedupe_manifest.json

No browsing, validation, scoring, Excel publication, or watermark advancement.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.remote_incremental_candidate_dedupe import (
    build_known_universe,
    host_from_value,
    load_jsonl,
    write_jsonl,
)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)
    remote_root = ROOT_DIR / "data" / "remote"
    linkedin_dir = run_dir / "harvest" / "linkedin"

    evidence_v2 = linkedin_dir / "resolved_candidate_source_evidence_v2.jsonl"
    unresolved_v2 = linkedin_dir / "still_needs_resolution_v2.jsonl"

    evidence_path = (
        evidence_v2
        if evidence_v2.exists()
        else linkedin_dir / "resolved_candidate_source_evidence.jsonl"
    )
    unresolved_input_path = (
        unresolved_v2
        if unresolved_v2.exists()
        else linkedin_dir / "still_needs_resolution.jsonl"
    )

    if not evidence_path.exists():
        raise RuntimeError(f"LinkedIn resolved evidence not found: {evidence_path}")
    if not unresolved_input_path.exists():
        raise RuntimeError(f"LinkedIn unresolved evidence not found: {unresolved_input_path}")

    rows = load_jsonl(evidence_path)
    unresolved_rows = load_jsonl(unresolved_input_path)
    known = build_known_universe(remote_root)

    current: dict[str, dict[str, Any]] = {}

    for row in rows:
        host = host_from_value(row.get("candidate_domain") or row.get("candidate_url"))
        if not host:
            # Defensive: preserve any malformed resolved evidence as unresolved.
            unresolved_rows.append({
                **row,
                "dedupe_status": "needs_resolution",
                "dedupe_reason": "candidate_has_no_resolved_canonical_host",
            })
            continue

        item = current.setdefault(host, {
            "canonical_host": host,
            "canonical_root_url": f"https://{host}/",
            "evidence_count": 0,
            "evidence": [],
        })
        item["evidence_count"] += 1
        item["evidence"].append({
            "linkedin_url": row.get("linkedin_url"),
            "final_linkedin_url": row.get("final_linkedin_url"),
            "content_kind": row.get("content_kind"),
            "source_message_uid": row.get("source_message_uid"),
            "source_subject": row.get("source_subject"),
            "candidate_name": row.get("candidate_name"),
            "evidence_type": row.get("evidence_type"),
            "evidence_provenance": row.get("evidence_provenance"),
            "resolution_provenance": row.get("resolution_provenance"),
            "confidence": row.get("confidence"),
            "ocr_observed": row.get("ocr_observed"),
        })

    new_rows: list[dict[str, Any]] = []
    known_rows: list[dict[str, Any]] = []

    for host, item in sorted(current.items()):
        prior = known.get(host, [])
        if prior:
            known_rows.append({
                **item,
                "dedupe_status": "already_known",
                "known_categories": sorted({p["category"] for p in prior}),
                "known_artifacts": sorted({p["artifact"] for p in prior}),
            })
        else:
            new_rows.append({
                **item,
                "dedupe_status": "new_candidate",
                "prevalidation_score": None,
                "prevalidation_reason": "incremental_linkedin_discovery",
                "email_evidence_count": item["evidence_count"],
                "evidence_url_count": item["evidence_count"],
                "discovery_resolution_evidence_count": item["evidence_count"],
            })

    out_dir = linkedin_dir / "dedupe"
    new_path = out_dir / "new_source_validation_queue.jsonl"
    known_path = out_dir / "already_known_source_evidence.jsonl"
    unresolved_path = out_dir / "still_needs_resolution.jsonl"
    manifest_path = out_dir / "dedupe_manifest.json"

    write_jsonl(new_path, new_rows)
    write_jsonl(known_path, known_rows)
    write_jsonl(unresolved_path, unresolved_rows)

    manifest = {
        "status": "ok",
        "input_resolved_evidence_rows": len(rows),
        "unique_resolved_candidate_hosts": len(current),
        "already_known_hosts": len(known_rows),
        "new_candidate_hosts": len(new_rows),
        "still_unresolved_targets": len(unresolved_rows),
        "historical_known_host_count": len(known),
        "new_validation_queue": str(new_path),
        "already_known_evidence": str(known_path),
        "still_unresolved": str(unresolved_path),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE LINKEDIN CANDIDATE DEDUPE OK")
    print("=" * 58)
    print(f"Run directory:                  {run_dir}")
    print(f"Resolved evidence input:        {evidence_path}")
    print(f"Unresolved input:               {unresolved_input_path}")
    print(f"Input resolved evidence rows:   {len(rows)}")
    print(f"Unique candidate hosts:         {len(current)}")
    print(f"Already known hosts:            {len(known_rows)}")
    print(f"New candidate hosts:            {len(new_rows)}")
    print(f"Still unresolved targets:       {len(unresolved_rows)}")
    print(f"Historical known-host universe: {len(known)}")
    print()
    print("ALREADY KNOWN")
    if known_rows:
        for row in known_rows:
            print(
                f"  {row['canonical_host']} | "
                f"{','.join(row.get('known_categories', []))}"
            )
    else:
        print("  [NONE]")
    print()
    print("NEW")
    if new_rows:
        for row in new_rows:
            print(f"  {row['canonical_host']}")
    else:
        print("  [NONE]")
    print()
    print("Unresolved targets were preserved for later resolution/check-later.")
    print("No network validation was performed.")
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
