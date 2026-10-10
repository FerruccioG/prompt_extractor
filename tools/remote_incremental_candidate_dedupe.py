#!/usr/bin/env python3
"""
remote_incremental_candidate_dedupe.py

Run-scoped hard-gate dedupe for newly harvested Remote source candidates.

Inputs
------
- <run>/harvest/instagram/candidate_source_evidence[_v2].jsonl (when present)
- <run>/harvest/generic/candidate_source_evidence.jsonl (when present)
- historical/root-level data/remote/*.jsonl classification/evidence artifacts

Outputs
-------
- <run>/dedupe/new_source_validation_queue.jsonl
- <run>/dedupe/already_known_source_evidence.jsonl
- <run>/dedupe/needs_resolution.jsonl
- <run>/dedupe/dedupe_manifest.json

Design
------
* Exact canonical host is the primary identity key at this stage.
* www. is collapsed, but regional domains are NOT collapsed:
    example.com != example.ie != example.co.uk
* Redirect/locale equivalence is deferred to live validation, where final URL
  evidence can prove that two hosts are actually the same destination.
* Any prior root-level Remote artifact can establish "already known" evidence.
  This intentionally implements the hard gate across Golden / Review / Exclude /
  unresolved / historical evidence, not merely the current Golden set.
* Run-scoped refresh artifacts are excluded from the historical universe so a
  candidate cannot dedupe against itself.

This stage does not browse, validate, score, write Excel, or advance watermark.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.remote_source_identity import canonical_host_from_value, row_hosts


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


def host_from_value(value: Any) -> str:
    # Backward-compatible public helper used by platform-specific dedupe tools.
    return canonical_host_from_value(value)

def classify_artifact(path: Path) -> str:
    name = path.name.lower()

    if "excluded" in name or "exclude" in name or "rejected" in name:
        return "exclude"
    if "review" in name or "hold" in name:
        return "hold_review"
    if "unresolved" in name or "check_later" in name:
        return "check_later"
    if "source_records_recall_p3_expanded" in name:
        return "golden"
    if name == "source_records.jsonl":
        return "historical_scored"
    if "source_records_recall" in name:
        return "historical_golden"
    if "validation" in name:
        return "validation_history"
    return "historical_evidence"


def build_known_universe(remote_root: Path) -> dict[str, list[dict[str, str]]]:
    known: dict[str, list[dict[str, str]]] = defaultdict(list)

    # Deliberately root-level only. refresh_runs/ contains the current run and
    # must never be allowed to dedupe against itself.
    for path in sorted(remote_root.glob("*.jsonl")):
        category = classify_artifact(path)

        try:
            rows = load_jsonl(path)
        except Exception:
            # A malformed unrelated historical artifact must not silently become
            # source evidence; leave it for operator inspection if encountered.
            raise

        for row in rows:
            for host in row_hosts(row):
                known[host].append({
                    "category": category,
                    "artifact": path.name,
                })

    return known


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    remote_root = root / "data" / "remote"

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    evidence_v2 = run_dir / "harvest" / "instagram" / "candidate_source_evidence_v2.jsonl"
    instagram_evidence = (
        evidence_v2
        if evidence_v2.exists()
        else run_dir / "harvest" / "instagram" / "candidate_source_evidence.jsonl"
    )
    generic_evidence = run_dir / "harvest" / "generic" / "candidate_source_evidence.jsonl"
    generic_unresolved = run_dir / "harvest" / "generic" / "needs_resolution.jsonl"

    evidence_inputs = [p for p in (instagram_evidence, generic_evidence) if p.exists()]
    rows: list[dict[str, Any]] = []
    for evidence_path in evidence_inputs:
        source_platform = "generic_web" if evidence_path == generic_evidence else "instagram"
        rows.extend({**row, "_evidence_platform": row.get("source_platform") or source_platform}
                    for row in load_jsonl(evidence_path))

    known = build_known_universe(remote_root)

    # Consolidate duplicate candidate hosts within the current run first.
    current: dict[str, dict[str, Any]] = {}
    resolution_rows: list[dict[str, Any]] = [
        {
            **row,
            "dedupe_status": "needs_resolution",
            "dedupe_reason": row.get("dedupe_reason")
            or row.get("resolution_reason")
            or "generic_candidate_needs_resolution",
        }
        for row in load_jsonl(generic_unresolved)
    ]

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
            "platform": row.get("_evidence_platform") or row.get("source_platform"),
            "post_id": row.get("post_id"),
            "instagram_url": row.get("instagram_url"),
            "source_url": row.get("source_url"),
            "source_message_uid": row.get("source_message_uid"),
            "evidence_type": row.get("evidence_type"),
            "confidence": row.get("confidence"),
            "ocr_observed": row.get("ocr_observed"),
            "observations": row.get("observations"),
        })

    new_rows: list[dict[str, Any]] = []
    known_rows: list[dict[str, Any]] = []

    for host, item in sorted(current.items()):
        prior = known.get(host, [])
        if prior:
            categories = sorted({p["category"] for p in prior})
            artifacts = sorted({p["artifact"] for p in prior})
            known_rows.append({
                **item,
                "dedupe_status": "already_known",
                "known_categories": categories,
                "known_artifacts": artifacts,
            })
        else:
            new_rows.append({
                **item,
                "dedupe_status": "new_candidate",
                # remote_source_validator.py consumes these historical field names.
                "prevalidation_score": None,
                "prevalidation_reason": "incremental_remote_discovery",
                "email_evidence_count": item["evidence_count"],
                "evidence_url_count": item["evidence_count"],
                "discovery_resolution_evidence_count": item["evidence_count"],
            })

    out_dir = run_dir / "dedupe"
    new_path = out_dir / "new_source_validation_queue.jsonl"
    known_path = out_dir / "already_known_source_evidence.jsonl"
    resolution_path = out_dir / "needs_resolution.jsonl"
    manifest_path = out_dir / "dedupe_manifest.json"

    write_jsonl(new_path, new_rows)
    write_jsonl(known_path, known_rows)
    write_jsonl(resolution_path, resolution_rows)

    manifest = {
        "status": "ok",
        "evidence_inputs": [str(p) for p in evidence_inputs],
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
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE INCREMENTAL CANDIDATE DEDUPE OK")
    print("=" * 58)
    print(f"Run directory:                  {run_dir}")
    print("Evidence inputs:")
    if evidence_inputs:
        for evidence_path in evidence_inputs:
            print(f"  {evidence_path}")
    else:
        print("  [NONE]")
    print(f"Input evidence rows:            {len(rows)}")
    print(f"Unique candidate hosts:         {len(current)}")
    print(f"Already known hosts:            {len(known_rows)}")
    print(f"New candidate hosts:            {len(new_rows)}")
    print(f"Needs name/domain resolution:   {len(resolution_rows)}")
    print(f"Historical known-host universe: {len(known)}")
    print()
    print(f"New validation queue:           {new_path}")
    print(f"Known-source evidence:          {known_path}")
    print(f"Needs resolution:               {resolution_path}")
    print()
    print("No network validation was performed.")
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
