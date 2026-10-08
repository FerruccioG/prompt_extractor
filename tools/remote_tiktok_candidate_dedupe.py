#!/usr/bin/env python3
"""
remote_tiktok_candidate_dedupe.py

Run-scoped hard-gate dedupe for candidate sources extracted from TikTok.

Input:
  <run>/harvest/tiktok/candidate_source_evidence.jsonl

Historical universe:
  root-level data/remote/*.jsonl only

Outputs:
  <run>/harvest/tiktok/dedupe/new_source_validation_queue.jsonl
  <run>/harvest/tiktok/dedupe/already_known_source_evidence.jsonl
  <run>/harvest/tiktok/dedupe/needs_resolution.jsonl
  <run>/harvest/tiktok/dedupe/dedupe_manifest.json

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
    tiktok_dir = run_dir / "harvest" / "tiktok"
    evidence_path = tiktok_dir / "candidate_source_evidence.jsonl"

    if not evidence_path.exists():
        raise RuntimeError(f"TikTok candidate evidence not found: {evidence_path}")

    rows = load_jsonl(evidence_path)
    known = build_known_universe(remote_root)

    current: dict[str, dict[str, Any]] = {}
    resolution_rows: list[dict[str, Any]] = []

    for row in rows:
        host = host_from_value(row.get("candidate_domain") or row.get("candidate_url"))

        if not host:
            resolution_rows.append({
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
            "tiktok_url": row.get("tiktok_url"),
            "requested_url": row.get("requested_url"),
            "evidence_type": row.get("evidence_type"),
            "evidence_provenance": row.get("evidence_provenance"),
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
                "prevalidation_reason": "incremental_tiktok_discovery",
                "email_evidence_count": item["evidence_count"],
                "evidence_url_count": item["evidence_count"],
                "discovery_resolution_evidence_count": item["evidence_count"],
            })

    out_dir = tiktok_dir / "dedupe"
    new_path = out_dir / "new_source_validation_queue.jsonl"
    known_path = out_dir / "already_known_source_evidence.jsonl"
    resolution_path = out_dir / "needs_resolution.jsonl"
    manifest_path = out_dir / "dedupe_manifest.json"

    write_jsonl(new_path, new_rows)
    write_jsonl(known_path, known_rows)
    write_jsonl(resolution_path, resolution_rows)

    manifest = {
        "status": "ok",
        "input_evidence_rows": len(rows),
        "unique_resolved_candidate_hosts": len(current),
        "already_known_hosts": len(known_rows),
        "new_candidate_hosts": len(new_rows),
        "needs_resolution_rows": len(resolution_rows),
        "historical_known_host_count": len(known),
        "new_validation_queue": str(new_path),
        "already_known_evidence": str(known_path),
        "needs_resolution": str(resolution_path),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE TIKTOK CANDIDATE DEDUPE OK")
    print("=" * 58)
    print(f"Run directory:                  {run_dir}")
    print(f"Input evidence rows:            {len(rows)}")
    print(f"Unique candidate hosts:         {len(current)}")
    print(f"Already known hosts:            {len(known_rows)}")
    print(f"New candidate hosts:            {len(new_rows)}")
    print(f"Needs name/domain resolution:   {len(resolution_rows)}")
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
    print("No network validation was performed.")
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
