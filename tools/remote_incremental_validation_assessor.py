#!/usr/bin/env python3
"""
remote_incremental_validation_assessor.py

Semantic assessment of run-scoped live validation results.

Why this exists
---------------
remote_source_validator.py intentionally treats a successful HTTP/browser
navigation as "resolved". For incremental Golden maintenance we need a stricter
semantic decision before profiling/scoring:

- live candidate source -> eligible for profiling/scoring
- redirect to a host already known historically -> already known after redirect
- bot/access wall -> check later (NOT exclusion)
- parked/for-sale/hijacked destination -> exclude invalid
- weak/inconclusive live page -> check later

Regional redirect policy
------------------------
If source.com redirects to source.ie/source.co.uk/etc., the final validated host
becomes the canonical identity for this run. This follows the project rule that
locale redirects should collapse to the final canonical destination, while
genuinely distinct regional domains remain separate records.

No scoring, Excel publication, or watermark advancement.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.remote_incremental_candidate_dedupe import build_known_universe


PARKING_TERMS = (
    "forsale.godaddy.com",
    "domain is for sale",
    "this domain is for sale",
    "buy this domain",
    "parked domain",
    "sedoparking",
    "afternic",
    "hugedomains",
)

BOT_OR_ACCESS_TERMS = (
    "bot-detection",
    "captcha",
    "access denied",
    "verify you are human",
    "checking your browser",
    "security challenge",
    "cloudflare challenge",
)

CANDIDATE_TERMS = (
    "job", "jobs", "career", "careers", "hiring", "vacanc",
    "recruit", "apply", "position", "opening", "opportunit",
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


def host_of(url: str) -> str:
    try:
        host = (urlparse(url or "").hostname or "").lower().strip(".")
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def text_blob(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("final_url", "")),
        str(row.get("page_title", "")),
        str(row.get("meta_description", "")),
        str(row.get("page_text_excerpt", "")),
        str(row.get("http_error", "")),
        str(row.get("browser_error", "")),
    ]
    return " ".join(parts).lower()


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def looks_like_parked_host(final_host: str, final_url: str, blob: str) -> bool:
    if contains_any(blob, PARKING_TERMS):
        return True

    # Common disposable parking host shape such as ww547.example.net.
    labels = final_host.split(".")
    if labels and re.fullmatch(r"ww\d+", labels[0] or ""):
        return True

    if "?tkn=" in (final_url or "").lower() and final_host:
        return True

    return False


def candidate_signal(row: dict[str, Any], blob: str) -> bool:
    if int(row.get("candidate_link_count", 0) or 0) > 0:
        return True
    if row.get("candidate_term_hits"):
        return True
    return contains_any(blob, CANDIDATE_TERMS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)
    validation_dir = run_dir / "validation"
    results_path = validation_dir / "source_validation_results.jsonl"
    unresolved_input_path = validation_dir / "source_validation_unresolved.jsonl"

    results = load_jsonl(results_path)
    unresolved_input = load_jsonl(unresolved_input_path)

    known = build_known_universe(ROOT_DIR / "data" / "remote")

    eligible: list[dict[str, Any]] = []
    already_known: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    check_later: list[dict[str, Any]] = []

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

        if contains_any(blob, BOT_OR_ACCESS_TERMS):
            check_later.append({
                **assessed,
                "assessment": "check_later",
                "assessment_reason": "bot_or_access_challenge",
            })
            continue

        if looks_like_parked_host(final_host, final_url, blob):
            excluded.append({
                **assessed,
                "assessment": "exclude_invalid",
                "assessment_reason": "parked_for_sale_or_disposable_destination",
            })
            continue

        # Post-redirect hard gate: the pre-validation dedupe may have seen a
        # .com host that locale-redirects to a historically known .ie/.co.uk host.
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
                # Downstream tools historically key on canonical_host/root URL.
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
        "eligible_output": str(eligible_path),
        "already_known_output": str(known_path),
        "excluded_output": str(excluded_path),
        "check_later_output": str(check_later_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE INCREMENTAL VALIDATION ASSESSMENT OK")
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
    for row in eligible:
        print(
            f"  {row.get('original_candidate_host')} -> "
            f"{row.get('assessed_canonical_host')} "
            f"({row.get('assessment_reason')})"
        )
    print()
    print("ALREADY KNOWN AFTER REDIRECT")
    for row in already_known:
        print(
            f"  {row.get('original_candidate_host')} -> "
            f"{row.get('assessed_canonical_host')}"
        )
    print()
    print("EXCLUDE INVALID")
    for row in excluded:
        print(
            f"  {row.get('original_candidate_host')} -> "
            f"{row.get('final_url')} "
            f"({row.get('assessment_reason')})"
        )
    print()
    print("CHECK LATER")
    for row in check_later:
        print(
            f"  {row.get('canonical_host') or row.get('original_candidate_host')} "
            f"({row.get('assessment_reason')})"
        )
    print()
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
