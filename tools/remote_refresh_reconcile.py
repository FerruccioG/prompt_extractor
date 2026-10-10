#!/usr/bin/env python3
"""
remote_refresh_reconcile.py

Whole-run disposition reconciliation for one Remote Golden refresh run.

This stage does NOT publish to Excel and does NOT advance the watermark.
It consolidates platform-specific final scoring/validation artifacts and applies
the strict Golden publication gate to scored A/B/C records.

Strict incremental publication gate:
- Candidate <-> Source Relationship Score >= 50
- Candidate Fit >= 40
- Remote Strength >= 50
- Ireland/EU Accessibility >= 30
- Opportunity Density >= 60
- Signal Quality >= 40

Outputs:
  <run>/reconciliation/promote.jsonl
  <run>/reconciliation/hold_review.jsonl
  <run>/reconciliation/exclude.jsonl
  <run>/reconciliation/check_later.jsonl
  <run>/reconciliation/unresolved.jsonl
  <run>/reconciliation/reconciliation_manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.remote_source_identity import source_key_from_row


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


def source_name(row: dict[str, Any]) -> str:
    return source_key_from_row(row, allow_evidence_fallback=False)


def strict_gate(row: dict[str, Any]) -> tuple[bool, list[str]]:
    checks = (
        ("score", "Candidate ↔ Source Relationship Score", 50),
        ("candidate_fit", "Candidate Fit", 40),
        ("remote_strength", "Remote Strength", 50),
        ("ireland_eu_accessibility", "Ireland/EU Accessibility", 30),
        ("opportunity_density", "Opportunity Density", 60),
        ("signal_quality", "Signal Quality", 40),
    )
    failures: list[str] = []
    for label, key, minimum in checks:
        value = int(row.get(key, 0) or 0)
        if value < minimum:
            failures.append(f"{label}:{value}<{minimum}")
    return not failures, failures


def tagged(rows: list[dict[str, Any]], platform: str, stage: str) -> list[dict[str, Any]]:
    return [{**row, "_platform": platform, "_source_stage": stage} for row in rows]


def unique_by_disposition(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Preserve rows without a canonical source identity (important unresolved
    # evidence) while deduping source-level dispositions by platform + source.
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for i, row in enumerate(rows):
        src = source_name(row)
        key = (
            str(row.get("_platform") or ""),
            src or f"__row_{i}_{row.get('linkedin_url') or row.get('instagram_url') or row.get('tiktok_url') or ''}",
            str(row.get("_disposition") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)

    platform_dirs = {
        "instagram": run_dir,
        "tiktok": run_dir / "harvest" / "tiktok",
        "linkedin": run_dir / "harvest" / "linkedin",
    }

    promote: list[dict[str, Any]] = []
    hold: list[dict[str, Any]] = []
    exclude: list[dict[str, Any]] = []
    check_later: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    platform_summary: dict[str, dict[str, int]] = {}

    for platform, base in platform_dirs.items():
        final_dir = base / "final"
        validation_dir = base / "validation"
        dedupe_dir = base / "dedupe"

        included_rows = load_jsonl(final_dir / "source_records.jsonl")
        review_rows = load_jsonl(final_dir / "source_records_review.jsonl")
        final_excluded_rows = load_jsonl(final_dir / "source_records_excluded.jsonl")
        invalid_rows = load_jsonl(validation_dir / "excluded_invalid_sources.jsonl")
        check_rows = load_jsonl(validation_dir / "check_later_sources.jsonl")

        for row in included_rows:
            passed, failures = strict_gate(row)
            if passed:
                promote.append({
                    **row,
                    "_platform": platform,
                    "_source_stage": "final_scoring",
                    "_disposition": "promote",
                    "_strict_gate_failures": [],
                })
            else:
                hold.append({
                    **row,
                    "_platform": platform,
                    "_source_stage": "final_scoring",
                    "_disposition": "hold_review",
                    "_strict_gate_failures": failures,
                    "_hold_reason": "tier_record_failed_strict_golden_gate",
                })

        hold.extend({
            **row,
            "_platform": platform,
            "_source_stage": "final_scoring",
            "_disposition": "hold_review",
            "_strict_gate_failures": strict_gate(row)[1],
            "_hold_reason": "final_scorer_review",
        } for row in review_rows)

        exclude.extend({
            **row,
            "_platform": platform,
            "_source_stage": "final_scoring",
            "_disposition": "exclude",
            "_exclude_reason": "final_scorer_exclude",
        } for row in final_excluded_rows)

        exclude.extend({
            **row,
            "_platform": platform,
            "_source_stage": "semantic_validation",
            "_disposition": "exclude",
            "_exclude_reason": row.get("assessment_reason") or "semantic_invalid",
        } for row in invalid_rows)

        check_later.extend({
            **row,
            "_platform": platform,
            "_source_stage": "semantic_validation",
            "_disposition": "check_later",
            "_check_later_reason": row.get("assessment_reason") or "semantic_inconclusive",
        } for row in check_rows)

        # Platform-specific unresolved artifacts.
        unresolved_candidates: list[Path] = []
        if platform == "instagram":
            unresolved_candidates = [
                dedupe_dir / "needs_resolution.jsonl",
                base / "harvest" / "instagram" / "needs_resolution.jsonl",
                base / "needs_resolution.jsonl",
            ]
        elif platform == "tiktok":
            unresolved_candidates = [
                dedupe_dir / "needs_resolution.jsonl",
                base / "needs_resolution.jsonl",
            ]
        elif platform == "linkedin":
            unresolved_candidates = [
                dedupe_dir / "still_needs_resolution.jsonl",
                base / "still_needs_resolution_v2.jsonl",
            ]

        seen_paths: set[Path] = set()
        for path in unresolved_candidates:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            for row in load_jsonl(path):
                unresolved.append({
                    **row,
                    "_platform": platform,
                    "_source_stage": "resolution",
                    "_disposition": "unresolved",
                    "_unresolved_artifact": str(path),
                })

        platform_summary[platform] = {
            "tier_records": len(included_rows),
            "review_records": len(review_rows),
            "final_excluded_records": len(final_excluded_rows),
            "semantic_invalid_records": len(invalid_rows),
            "semantic_check_later_records": len(check_rows),
        }

    promote = unique_by_disposition(promote)
    hold = unique_by_disposition(hold)
    exclude = unique_by_disposition(exclude)
    check_later = unique_by_disposition(check_later)
    unresolved = unique_by_disposition(unresolved)

    out_dir = run_dir / "reconciliation"
    promote_path = out_dir / "promote.jsonl"
    hold_path = out_dir / "hold_review.jsonl"
    exclude_path = out_dir / "exclude.jsonl"
    check_path = out_dir / "check_later.jsonl"
    unresolved_path = out_dir / "unresolved.jsonl"
    manifest_path = out_dir / "reconciliation_manifest.json"

    write_jsonl(promote_path, promote)
    write_jsonl(hold_path, hold)
    write_jsonl(exclude_path, exclude)
    write_jsonl(check_path, check_later)
    write_jsonl(unresolved_path, unresolved)

    manifest = {
        "status": "ok",
        "run_dir": str(run_dir),
        "strict_golden_gate": {
            "score_min": 50,
            "candidate_fit_min": 40,
            "remote_strength_min": 50,
            "ireland_eu_accessibility_min": 30,
            "opportunity_density_min": 60,
            "signal_quality_min": 40,
        },
        "platform_summary": platform_summary,
        "promote": len(promote),
        "hold_review": len(hold),
        "exclude": len(exclude),
        "check_later": len(check_later),
        "unresolved": len(unresolved),
        "excel_touched": False,
        "watermark_advanced": False,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE REFRESH WHOLE-RUN RECONCILIATION")
    print("=" * 64)
    print(f"Run directory:       {run_dir}")
    print(f"Promote:             {len(promote)}")
    print(f"Hold / Review:       {len(hold)}")
    print(f"Exclude:             {len(exclude)}")
    print(f"Check Later:         {len(check_later)}")
    print(f"Unresolved:          {len(unresolved)}")
    print()

    def show(label: str, rows: list[dict[str, Any]]) -> None:
        print(label)
        if not rows:
            print("  [NONE]")
        else:
            for row in rows:
                src = source_name(row) or "(unresolved target)"
                reason = (
                    row.get("_hold_reason")
                    or row.get("_exclude_reason")
                    or row.get("_check_later_reason")
                    or row.get("reason")
                    or row.get("assessment_reason")
                    or ""
                )
                platform = row.get("_platform")
                failures = row.get("_strict_gate_failures") or []
                suffix = f" | {reason}" if reason else ""
                if failures:
                    suffix += f" | gate_fail={','.join(failures)}"
                print(f"  {platform}: {src}{suffix}")
        print()

    show("PROMOTE", promote)
    show("HOLD / REVIEW", hold)
    show("EXCLUDE", exclude)
    show("CHECK LATER", check_later)
    show("UNRESOLVED", unresolved)

    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
