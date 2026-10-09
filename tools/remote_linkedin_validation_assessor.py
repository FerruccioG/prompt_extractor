#!/usr/bin/env python3
"""
remote_linkedin_validation_assessor.py

Semantic assessment for LinkedIn-derived candidate-source validation results.

Inputs:
  <run>/harvest/linkedin/validation/source_validation_results.jsonl
  <run>/harvest/linkedin/validation/source_validation_unresolved.jsonl

Outputs:
  <run>/harvest/linkedin/validation/eligible_new_sources.jsonl
  <run>/harvest/linkedin/validation/already_known_after_redirect.jsonl
  <run>/harvest/linkedin/validation/excluded_invalid_sources.jsonl
  <run>/harvest/linkedin/validation/check_later_sources.jsonl
  <run>/harvest/linkedin/validation/assessment_manifest.json

Reuses the same semantic rules as the incremental assessor while keeping
LinkedIn-derived artifacts isolated from other platform branches.

No scoring, Excel publication, or watermark advancement.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.remote_incremental_candidate_dedupe import build_known_universe
from tools.remote_incremental_validation_assessor import (
    BOT_OR_ACCESS_TERMS,
    candidate_signal,
    contains_any,
    host_of,
    load_jsonl,
    looks_like_parked_host,
    text_blob,
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
    validation_dir = run_dir / "harvest" / "linkedin" / "validation"
    results_path = validation_dir / "source_validation_results.jsonl"
    unresolved_input_path = validation_dir / "source_validation_unresolved.jsonl"

    results = load_jsonl(results_path)
    unresolved_input = load_jsonl(unresolved_input_path)
    known = build_known_universe(ROOT_DIR / "data" / "remote")

    eligible = []
    already_known = []
    excluded = []
    check_later = []

    for row in results:
        original_host = str(row.get("canonical_host", "") or "").lower()
        final_url = str(row.get("final_url", "") or "")
        final_host = host_of(final_url) or str(row.get("final_host", "") or "").lower()
        blob = text_blob(row)

        assessed = {
            **row,
            "original_candidate_host": original_host,
            "assessed_canonical_host": final_host or original_host,
            "assessed_canonical_root_url": (
                f"https://{final_host}/" if final_host else row.get("canonical_root_url")
            ),
            "redirected_to_different_host": bool(final_host and final_host != original_host),
        }

        if looks_like_parked_host(final_host, final_url, blob):
            excluded.append({
                **assessed,
                "assessment": "exclude_invalid",
                "assessment_reason": "parked_for_sale_or_disposable_destination",
            })
            continue

        if contains_any(blob, BOT_OR_ACCESS_TERMS):
            check_later.append({
                **assessed,
                "assessment": "check_later",
                "assessment_reason": "bot_or_access_challenge",
            })
            continue

        prior = known.get(final_host, []) if final_host else []
        if prior:
            already_known.append({
                **assessed,
                "assessment": "already_known_after_redirect",
                "assessment_reason": "validated_final_host_already_known",
                "known_categories": sorted({x["category"] for x in prior}),
                "known_artifacts": sorted({x["artifact"] for x in prior}),
            })
            continue

        if candidate_signal(row, blob):
            eligible.append({
                **assessed,
                "assessment": "eligible_new_source",
                "assessment_reason": "live_candidate_source_evidence",
                "canonical_host": final_host or original_host,
                "canonical_root_url": (
                    f"https://{final_host}/" if final_host else row.get("canonical_root_url")
                ),
            })
            continue

        check_later.append({
            **assessed,
            "assessment": "check_later",
            "assessment_reason": "resolved_but_candidate_source_evidence_inconclusive",
        })

    for row in unresolved_input:
        check_later.append({
            **row,
            "assessment": "check_later",
            "assessment_reason": "live_validation_unresolved",
        })

    eligible_path = validation_dir / "eligible_new_sources.jsonl"
    known_path = validation_dir / "already_known_after_redirect.jsonl"
    excluded_path = validation_dir / "excluded_invalid_sources.jsonl"
    check_later_path = validation_dir / "check_later_sources.jsonl"
    manifest_path = validation_dir / "assessment_manifest.json"

    write_jsonl(eligible_path, eligible)
    write_jsonl(known_path, already_known)
    write_jsonl(excluded_path, excluded)
    write_jsonl(check_later_path, check_later)

    manifest = {
        "status": "ok",
        "validator_resolved_rows": len(results),
        "validator_unresolved_rows": len(unresolved_input),
        "eligible_new_sources": len(eligible),
        "already_known_after_redirect": len(already_known),
        "excluded_invalid_sources": len(excluded),
        "check_later_sources": len(check_later),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE LINKEDIN VALIDATION ASSESSMENT OK")
    print("=" * 58)
    print(f"Run directory:                  {run_dir}")
    print(f"Validator resolved rows:        {len(results)}")
    print(f"Validator unresolved rows:      {len(unresolved_input)}")
    print(f"Eligible new sources:           {len(eligible)}")
    print(f"Already known after redirect:   {len(already_known)}")
    print(f"Exclude invalid/parked:         {len(excluded)}")
    print(f"Check later / inconclusive:     {len(check_later)}")
    print()
    print("ELIGIBLE")
    if eligible:
        for row in eligible:
            print(
                f"  {row.get('original_candidate_host')} -> "
                f"{row.get('assessed_canonical_host')} "
                f"({row.get('assessment_reason')})"
            )
    else:
        print("  [NONE]")
    print()
    print("ALREADY KNOWN AFTER REDIRECT")
    if already_known:
        for row in already_known:
            print(
                f"  {row.get('original_candidate_host')} -> "
                f"{row.get('assessed_canonical_host')}"
            )
    else:
        print("  [NONE]")
    print()
    print("EXCLUDE INVALID")
    if excluded:
        for row in excluded:
            print(
                f"  {row.get('original_candidate_host')} -> "
                f"{row.get('final_url')} "
                f"({row.get('assessment_reason')})"
            )
    else:
        print("  [NONE]")
    print()
    print("CHECK LATER")
    if check_later:
        for row in check_later:
            print(
                f"  {row.get('canonical_host') or row.get('original_candidate_host')} "
                f"({row.get('assessment_reason')})"
            )
    else:
        print("  [NONE]")
    print()
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
