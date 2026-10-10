#!/usr/bin/env python3
"""
remote_refresh_finalize.py

Finalize one completed Remote Golden refresh run when there are NO Golden
promotions.

Safety contract
---------------
- requires reconciliation status=ok
- requires unresolved=0
- requires promote=0
- performs read-only Golden Excel integrity validation
- persists Hold/Review, Exclude, Check Later, and known-source activity evidence
  into root-level cumulative JSONL artifacts
- advances the watermark/state only after every preceding step succeeds
- never writes the Excel workbook in this zero-promotion path

If a later run contains promotions, this script intentionally refuses to
finalize; promotion publication must use the Excel write/backup transaction.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
REMOTE_ROOT = ROOT_DIR / "data" / "remote"
STATE_PATH = REMOTE_ROOT / "remote_golden_refresh_state.json"

DEFAULT_WORKBOOK = Path("/mnt/remote/Remote_Opportunities_Master_List_Expanded_Sources.xlsx")
GOLDEN_CANDIDATES = (
    REMOTE_ROOT / "source_records_recall_p3_expanded.jsonl",
    REMOTE_ROOT / "source_records_recall_p3.jsonl",
    REMOTE_ROOT / "source_records_recall_expanded.jsonl",
)

PERSIST_PATHS = {
    "hold_review": REMOTE_ROOT / "incremental_source_records_hold_review.jsonl",
    "exclude": REMOTE_ROOT / "incremental_source_records_excluded.jsonl",
    "check_later": REMOTE_ROOT / "incremental_source_records_check_later.jsonl",
    "known_evidence": REMOTE_ROOT / "incremental_known_source_evidence.jsonl",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def latest_run_dir() -> Path:
    runs_dir = REMOTE_ROOT / "refresh_runs"
    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"No refresh runs found under {runs_dir}")
    return candidates[0]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


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
                raise RuntimeError(f"Invalid JSONL {path}:{line_no}: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def source_key(row: dict[str, Any]) -> str:
    for key in (
        "Source", "canonical_host", "assessed_canonical_host",
        "original_candidate_host", "candidate_domain",
    ):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value
    # Evidence-only fallback.
    return str(
        row.get("linkedin_url")
        or row.get("instagram_url")
        or row.get("tiktok_url")
        or row.get("requested_url")
        or ""
    ).strip().lower()


def merge_cumulative(path: Path, incoming: list[dict[str, Any]], disposition: str) -> int:
    existing = load_jsonl(path)
    merged: dict[str, dict[str, Any]] = {}

    for row in existing:
        key = source_key(row)
        if key:
            merged[key] = row

    for row in incoming:
        key = source_key(row)
        if not key:
            continue
        merged[key] = {
            **row,
            "persistent_disposition": disposition,
            "persisted_at_utc": utc_now_iso(),
        }

    ordered = [merged[k] for k in sorted(merged)]
    write_jsonl_atomic(path, ordered)
    return len(incoming)


def choose_golden_input() -> Path:
    explicit = os.getenv("REMOTE_GOLDEN_SOURCE_RECORDS", "").strip()
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"REMOTE_GOLDEN_SOURCE_RECORDS not found: {path}")
        return path

    for path in GOLDEN_CANDIDATES:
        if path.exists():
            return path

    raise FileNotFoundError(
        "Could not locate the Golden v3 source-record JSONL. "
        "Set REMOTE_GOLDEN_SOURCE_RECORDS explicitly."
    )


def collect_known_evidence(run_dir: Path) -> list[dict[str, Any]]:
    paths = (
        run_dir / "dedupe" / "already_known_source_evidence.jsonl",
        run_dir / "harvest" / "tiktok" / "dedupe" / "already_known_source_evidence.jsonl",
        run_dir / "harvest" / "linkedin" / "dedupe" / "already_known_source_evidence.jsonl",
    )
    rows: list[dict[str, Any]] = []
    for path in paths:
        for row in load_jsonl(path):
            rows.append({
                **row,
                "evidence_run": run_dir.name,
                "evidence_artifact": str(path),
            })
    return rows


def run_excel_integrity(workbook: Path, golden_input: Path) -> int:
    if not workbook.exists():
        raise FileNotFoundError(f"Golden workbook not found: {workbook}")
    if not golden_input.exists():
        raise FileNotFoundError(f"Golden source records not found: {golden_input}")

    os.environ["REMOTE_EXCEL_OUTPUT_PATH"] = str(workbook)
    os.environ["REMOTE_SOURCE_RECORDS_PATH"] = str(golden_input)

    from tools.storage import excel_source_integrity_validator
    return excel_source_integrity_validator.main()


def main() -> int:
    run_dir = latest_run_dir()
    reconciliation_dir = run_dir / "reconciliation"
    manifest_path = reconciliation_dir / "reconciliation_manifest.json"
    run_manifest_path = run_dir / "run_manifest.json"

    if not manifest_path.exists():
        raise RuntimeError(f"Reconciliation manifest not found: {manifest_path}")
    if not run_manifest_path.exists():
        raise RuntimeError(f"Run manifest not found: {run_manifest_path}")
    if not STATE_PATH.exists():
        raise RuntimeError(f"Refresh state not found: {STATE_PATH}")

    reconciliation = load_json(manifest_path)
    run_manifest = load_json(run_manifest_path)
    state = load_json(STATE_PATH)

    if reconciliation.get("status") != "ok":
        raise RuntimeError("Reconciliation status is not ok")

    unresolved_count = int(reconciliation.get("unresolved", -1))
    promote_count = int(reconciliation.get("promote", -1))

    if unresolved_count != 0:
        raise RuntimeError(f"Cannot finalize: unresolved={unresolved_count}")
    if promote_count != 0:
        raise RuntimeError(
            f"Cannot use zero-promotion finalizer: promote={promote_count}. "
            "Excel publication transaction is required."
        )

    workbook = Path(os.getenv("REMOTE_EXCEL_OUTPUT_PATH", str(DEFAULT_WORKBOOK)))
    golden_input = choose_golden_input()

    print("REMOTE REFRESH ZERO-PROMOTION FINALIZATION")
    print("=" * 64)
    print(f"Run directory:                 {run_dir}")
    print(f"Golden promotions:             {promote_count}")
    print(f"Unresolved:                    {unresolved_count}")
    print(f"Workbook:                      {workbook}")
    print(f"Golden source-record baseline: {golden_input}")
    print()
    print("Running read-only Golden workbook integrity validation...")
    print()

    integrity_rc = run_excel_integrity(workbook, golden_input)
    if integrity_rc != 0:
        print()
        print("FINALIZATION STOPPED: Excel integrity validation failed.")
        print("No persistent dispositions were written.")
        print("Watermark was NOT advanced.")
        return 1

    hold_rows = load_jsonl(reconciliation_dir / "hold_review.jsonl")
    exclude_rows = load_jsonl(reconciliation_dir / "exclude.jsonl")
    check_rows = load_jsonl(reconciliation_dir / "check_later.jsonl")
    known_rows = collect_known_evidence(run_dir)

    persisted = {
        "hold_review": merge_cumulative(PERSIST_PATHS["hold_review"], hold_rows, "hold_review"),
        "exclude": merge_cumulative(PERSIST_PATHS["exclude"], exclude_rows, "exclude"),
        "check_later": merge_cumulative(PERSIST_PATHS["check_later"], check_rows, "check_later"),
        "known_evidence": merge_cumulative(PERSIST_PATHS["known_evidence"], known_rows, "known_evidence"),
    }

    intake = run_manifest.get("intake") or {}
    candidate_uid = intake.get("candidate_newest_message_uid")
    candidate_watermark = str(intake.get("candidate_newest_email_datetime_utc") or "").strip()

    if candidate_uid in (None, ""):
        raise RuntimeError("Cannot advance state: candidate newest message UID is missing")
    if not candidate_watermark:
        raise RuntimeError("Cannot advance state: candidate newest email datetime is missing")

    old_uid = state.get("last_successful_message_uid")
    if old_uid not in (None, "") and int(candidate_uid) < int(old_uid):
        raise RuntimeError("Candidate UID would move watermark backwards")

    old_watermark = str(state.get("last_successful_watermark_utc") or "")
    if old_watermark and candidate_watermark < old_watermark:
        raise RuntimeError("Candidate timestamp would move watermark backwards")

    state["last_successful_watermark_utc"] = candidate_watermark
    state["last_successful_message_uid"] = int(candidate_uid)
    state["last_successful_run_utc"] = utc_now_iso()
    state["successful_run_count"] = int(state.get("successful_run_count", 0) or 0) + 1
    write_json_atomic(STATE_PATH, state)

    final_manifest = {
        "status": "success",
        "run_dir": str(run_dir),
        "golden_before": 46,
        "golden_promotions": 0,
        "golden_after": 46,
        "excel_written": False,
        "excel_integrity_passed": True,
        "persistent_rows_written": persisted,
        "state_after": state,
        "finalized_at_utc": utc_now_iso(),
    }
    final_path = run_dir / "finalization_manifest.json"
    write_json_atomic(final_path, final_manifest)

    print()
    print("FINALIZATION COMPLETE")
    print(f"Hold / Review persisted:       {persisted['hold_review']}")
    print(f"Exclude persisted:             {persisted['exclude']}")
    print(f"Check Later persisted:         {persisted['check_later']}")
    print(f"Known evidence persisted:      {persisted['known_evidence']}")
    print("Golden before:                 46")
    print("Golden promotions:             0")
    print("Golden after:                  46")
    print("Excel was NOT modified.")
    print("Excel integrity:               PASS")
    print(f"Watermark advanced to:         {candidate_watermark}")
    print(f"Last successful message UID:   {candidate_uid}")
    print(f"Finalization manifest:         {final_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
