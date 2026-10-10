#!/usr/bin/env python3
"""
remote_refresh_finalize.py

Finalize one completed Remote Golden refresh run, including Golden promotions,
zero-promotion runs, and the valid no-new-email no-op case.

Safety contract
---------------
- the exact run directory can be supplied with --run-dir (orchestrator must do so)
- any Gmail fetch error blocks finalization
- Gmail UID is the operational checkpoint authority; email Date is metadata only
- candidate-level unresolved evidence is persisted as Check Later, not discarded
- zero-promotion/no-op runs perform read-only Golden Excel integrity validation
- promotion runs merge Golden JSONL, back up the workbook, publish through the
  existing Excel loader, and validate workbook integrity before committing state
- prepares all cumulative disposition artifacts before committing them
- advances state only after every preceding step succeeds
- restores workbook/Golden JSONL if a promotion transaction fails before state commit
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.remote_source_identity import source_key_from_row

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


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def source_key(row: dict[str, Any]) -> str:
    return source_key_from_row(row, allow_evidence_fallback=True)


def merged_cumulative_rows(
    path: Path,
    incoming: list[dict[str, Any]],
    disposition: str,
) -> tuple[list[dict[str, Any]], int]:
    existing = load_jsonl(path)
    merged: dict[str, dict[str, Any]] = {}

    for row in existing:
        key = source_key(row)
        if key:
            merged[key] = row

    accepted = 0
    for row in incoming:
        key = source_key(row)
        if not key:
            continue
        merged[key] = {
            **row,
            "persistent_disposition": disposition,
            "persisted_at_utc": utc_now_iso(),
        }
        accepted += 1

    return [merged[k] for k in sorted(merged)], accepted


def stage_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return tmp


def commit_staged(staged: dict[Path, Path]) -> None:
    # Every destination has been fully written before any live file is replaced.
    # If replacement is interrupted, rerunning is safe because merges are keyed
    # and idempotent; state is still committed last.
    for destination, tmp in staged.items():
        tmp.replace(destination)


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
        "Could not locate the Golden source-record JSONL. "
        "Set REMOTE_GOLDEN_SOURCE_RECORDS explicitly."
    )


def golden_count(path: Path) -> int:
    return len(load_jsonl(path))


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


def safe_metadata_watermark(old_value: str, candidate_value: str) -> str:
    """Email Date is metadata only; never let it move the stored value backwards."""
    old_value = str(old_value or "").strip()
    candidate_value = str(candidate_value or "").strip()
    if not candidate_value:
        return old_value
    if not old_value:
        return candidate_value
    return candidate_value if candidate_value > old_value else old_value



def merge_golden_records(
    baseline: list[dict[str, Any]],
    promotions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    """Merge promotions by durable source identity while preserving baseline order."""
    merged = [dict(row) for row in baseline]
    index: dict[str, int] = {}
    for i, row in enumerate(merged):
        key = source_key(row)
        if key:
            index[key] = i

    appended = 0
    updated = 0
    for row in promotions:
        key = source_key(row)
        if not key:
            raise RuntimeError("Promotion row has no canonical source identity")
        clean = {
            k: v for k, v in row.items()
            if not str(k).startswith("_")
        }
        if key in index:
            pos = index[key]
            merged[pos] = {**merged[pos], **clean}
            updated += 1
        else:
            index[key] = len(merged)
            merged.append(clean)
            appended += 1

    return merged, appended, updated


def run_excel_loader(workbook: Path, source_records: Path) -> int:
    os.environ["REMOTE_EXCEL_OUTPUT_PATH"] = str(workbook)
    os.environ["REMOTE_SOURCE_RECORDS_PATH"] = str(source_records)
    os.environ.pop("REMOTE_EXCEL_DRY_RUN", None)
    from tools.storage import excel_source_loader
    return excel_source_loader.main()


def backup_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def restore_file(backup: Path, destination: Path) -> None:
    if backup.exists():
        shutil.copy2(backup, destination)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir()
    run_manifest_path = run_dir / "run_manifest.json"
    final_path = run_dir / "finalization_manifest.json"
    if not run_manifest_path.exists():
        raise RuntimeError(f"Run manifest not found: {run_manifest_path}")
    if not STATE_PATH.exists():
        raise RuntimeError(f"Refresh state not found: {STATE_PATH}")

    # Idempotency guard: a successfully finalized run must never publish twice
    # or increment successful_run_count twice if an operator reruns this exact
    # run directory manually.
    if final_path.exists():
        prior_final = load_json(final_path)
        if str(prior_final.get("status") or "").startswith("success"):
            print("REMOTE REFRESH ALREADY FINALIZED")
            print("=" * 64)
            print(f"Run directory:                 {run_dir}")
            print(f"Finalization status:           {prior_final.get('status')}")
            print(f"Golden after:                  {prior_final.get('golden_after')}")
            print(f"Excel written:                 {prior_final.get('excel_written')}")
            print(f"Watermark advanced:            {prior_final.get('watermark_advanced')}")
            print("No files were modified.")
            return 0

    run_manifest = load_json(run_manifest_path)
    state = load_json(STATE_PATH)
    intake = run_manifest.get("intake") or {}

    fetch_errors = int(intake.get("emails_with_fetch_errors", 0) or 0)
    if fetch_errors:
        raise RuntimeError(
            f"Cannot finalize: email fetch errors={fetch_errors}. "
            "UID checkpoint must not advance past an unfetched message."
        )
    if run_manifest.get("commit_eligible") is False:
        raise RuntimeError("Cannot finalize: run manifest marks this run commit_eligible=false")

    workbook = Path(os.getenv("REMOTE_EXCEL_OUTPUT_PATH", str(DEFAULT_WORKBOOK)))
    golden_input = choose_golden_input()
    before_count = golden_count(golden_input)

    # A no-new-email run is a first-class successful no-op. It requires no
    # reconciliation artifacts and leaves the message UID/watermark unchanged.
    no_new = bool(intake.get("no_new_email_noop")) or int(intake.get("eligible_new_emails", 0) or 0) == 0
    if no_new:
        print("REMOTE REFRESH NO-NEW-EMAIL FINALIZATION")
        print("=" * 64)
        print(f"Run directory:                 {run_dir}")
        print("New Remote emails:             0")
        print("Running read-only Golden workbook integrity validation...")
        integrity_rc = run_excel_integrity(workbook, golden_input)
        if integrity_rc != 0:
            print("FINALIZATION STOPPED: Excel integrity validation failed.")
            return 1

        state["last_successful_run_utc"] = utc_now_iso()
        state["successful_run_count"] = int(state.get("successful_run_count", 0) or 0) + 1
        final_manifest = {
            "status": "success_noop_no_new_email",
            "run_dir": str(run_dir),
            "golden_before": before_count,
            "golden_promotions": 0,
            "golden_after": before_count,
            "excel_written": False,
            "excel_integrity_passed": True,
            "watermark_advanced": False,
            "state_after": state,
            "finalized_at_utc": utc_now_iso(),
        }
        write_json_atomic(final_path, final_manifest)
        try:
            # State is the final durable commit for a successful refresh.
            write_json_atomic(STATE_PATH, state)
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        print("No new Remote emails — successful no-op.")
        print(f"Golden rows:                   {before_count}")
        print("Excel was NOT modified.")
        print("Watermark/UID unchanged.")
        return 0

    reconciliation_dir = run_dir / "reconciliation"
    manifest_path = reconciliation_dir / "reconciliation_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Reconciliation manifest not found: {manifest_path}")

    reconciliation = load_json(manifest_path)
    if reconciliation.get("status") != "ok":
        raise RuntimeError("Reconciliation status is not ok")

    promote_count = int(reconciliation.get("promote", -1))
    unresolved_count = int(reconciliation.get("unresolved", 0) or 0)

    hold_rows = load_jsonl(reconciliation_dir / "hold_review.jsonl")
    exclude_rows = load_jsonl(reconciliation_dir / "exclude.jsonl")
    check_rows = load_jsonl(reconciliation_dir / "check_later.jsonl")
    promote_rows = load_jsonl(reconciliation_dir / "promote.jsonl")
    unresolved_rows = [
        {
            **row,
            "_disposition": "check_later",
            "_check_later_reason": row.get("_check_later_reason")
            or row.get("_unresolved_reason")
            or row.get("dedupe_reason")
            or "candidate_unresolved_preserved_for_retry",
            "_original_disposition": "unresolved",
        }
        for row in load_jsonl(reconciliation_dir / "unresolved.jsonl")
    ]
    check_rows = check_rows + unresolved_rows
    known_rows = collect_known_evidence(run_dir)

    if promote_count != len(promote_rows):
        raise RuntimeError(
            f"Reconciliation promotion count mismatch: manifest={promote_count}, "
            f"rows={len(promote_rows)}"
        )

    prepared: dict[str, tuple[Path, list[dict[str, Any]], int]] = {}
    for name, incoming, disposition in (
        ("hold_review", hold_rows, "hold_review"),
        ("exclude", exclude_rows, "exclude"),
        ("check_later", check_rows, "check_later"),
        ("known_evidence", known_rows, "known_evidence"),
    ):
        destination = PERSIST_PATHS[name]
        rows, accepted = merged_cumulative_rows(destination, incoming, disposition)
        prepared[name] = (destination, rows, accepted)

    staged: dict[Path, Path] = {}
    for destination, rows, _accepted in prepared.values():
        staged[destination] = stage_jsonl(destination, rows)

    excel_written = False
    publication_dir = run_dir / "publication"
    merged_golden_path = publication_dir / "merged_golden.jsonl"
    workbook_backup = publication_dir / "workbook_before_publication.xlsx"
    golden_backup = publication_dir / "golden_before_publication.jsonl"
    golden_appended = 0
    golden_updated = 0

    if promote_count == 0:
        print("REMOTE REFRESH ZERO-PROMOTION FINALIZATION")
        print("=" * 64)
        print(f"Run directory:                 {run_dir}")
        print("Golden promotions:             0")
        print(f"Candidate unresolved:          {unresolved_count} (persisted as Check Later)")
        print(f"Workbook:                      {workbook}")
        print(f"Golden source-record baseline: {golden_input}")
        print()
        print("Running read-only Golden workbook integrity validation...")
        print()

        integrity_rc = run_excel_integrity(workbook, golden_input)
        if integrity_rc != 0:
            print("FINALIZATION STOPPED: Excel integrity validation failed.")
            print("No persistent dispositions were written.")
            print("Watermark was NOT advanced.")
            return 1
    else:
        print("REMOTE REFRESH GOLDEN PUBLICATION")
        print("=" * 64)
        print(f"Run directory:                 {run_dir}")
        print(f"Golden promotions:             {promote_count}")
        print(f"Workbook:                      {workbook}")
        print(f"Golden source-record baseline: {golden_input}")
        print()

        baseline_rows = load_jsonl(golden_input)
        merged_rows, golden_appended, golden_updated = merge_golden_records(
            baseline_rows,
            promote_rows,
        )
        publication_dir.mkdir(parents=True, exist_ok=True)
        merged_tmp = stage_jsonl(merged_golden_path, merged_rows)
        merged_tmp.replace(merged_golden_path)

        if not workbook.exists():
            raise FileNotFoundError(f"Golden workbook not found: {workbook}")

        backup_file(workbook, workbook_backup)
        backup_file(golden_input, golden_backup)

        print(f"Golden rows before:             {before_count}")
        print(f"Golden rows after merge:        {len(merged_rows)}")
        print(f"New canonical rows appended:    {golden_appended}")
        print(f"Existing canonical rows updated:{golden_updated}")
        print("Publishing merged Golden records to Excel...")

        loader_rc = run_excel_loader(workbook, merged_golden_path)
        if loader_rc != 0:
            restore_file(workbook_backup, workbook)
            print("PUBLICATION STOPPED: Excel loader failed.")
            print("Workbook restored from pre-publication backup.")
            print("Watermark was NOT advanced.")
            return 1

        integrity_rc = run_excel_integrity(workbook, merged_golden_path)
        if integrity_rc != 0:
            restore_file(workbook_backup, workbook)
            print("PUBLICATION STOPPED: post-write Excel integrity validation failed.")
            print("Workbook restored from pre-publication backup.")
            print("Golden JSONL was NOT changed.")
            print("Watermark was NOT advanced.")
            return 1

        try:
            golden_tmp = stage_jsonl(golden_input, merged_rows)
            golden_tmp.replace(golden_input)
            excel_written = True
        except Exception:
            restore_file(workbook_backup, workbook)
            restore_file(golden_backup, golden_input)
            raise

    # Persist non-Golden dispositions only after Excel/Golden validation succeeds.
    commit_staged(staged)
    persisted = {name: info[2] for name, info in prepared.items()}

    candidate_uid = intake.get("candidate_newest_message_uid")
    candidate_watermark = str(intake.get("candidate_newest_email_datetime_utc") or "").strip()
    if candidate_uid in (None, ""):
        if excel_written:
            restore_file(workbook_backup, workbook)
            restore_file(golden_backup, golden_input)
        raise RuntimeError("Cannot advance state: candidate newest message UID is missing")

    old_uid = state.get("last_successful_message_uid")
    if old_uid not in (None, "") and int(candidate_uid) < int(old_uid):
        if excel_written:
            restore_file(workbook_backup, workbook)
            restore_file(golden_backup, golden_input)
        raise RuntimeError("Candidate UID would move checkpoint backwards")

    state["last_successful_message_uid"] = int(candidate_uid)
    state["last_successful_watermark_utc"] = safe_metadata_watermark(
        str(state.get("last_successful_watermark_utc") or ""),
        candidate_watermark,
    )
    state["last_successful_run_utc"] = utc_now_iso()
    state["successful_run_count"] = int(state.get("successful_run_count", 0) or 0) + 1

    after_count = golden_count(golden_input)
    final_manifest = {
        "status": "success",
        "run_dir": str(run_dir),
        "golden_before": before_count,
        "golden_promotions": promote_count,
        "golden_appended": golden_appended,
        "golden_updated_existing": golden_updated,
        "golden_after": after_count,
        "excel_written": excel_written,
        "excel_integrity_passed": True,
        "candidate_unresolved_persisted_as_check_later": unresolved_count,
        "persistent_rows_written": persisted,
        "checkpoint_authority": "gmail_uid",
        "watermark_advanced": True,
        "state_after": state,
        "finalized_at_utc": utc_now_iso(),
    }
    write_json_atomic(final_path, final_manifest)

    try:
        # State/watermark is deliberately the final durable commit.
        write_json_atomic(STATE_PATH, state)
    except Exception:
        final_path.unlink(missing_ok=True)
        if excel_written:
            restore_file(workbook_backup, workbook)
            restore_file(golden_backup, golden_input)
        raise

    print()
    print("FINALIZATION COMPLETE")
    print(f"Hold / Review persisted:       {persisted['hold_review']}")
    print(f"Exclude persisted:             {persisted['exclude']}")
    print(f"Check Later persisted:         {persisted['check_later']}")
    print(f"Known evidence persisted:      {persisted['known_evidence']}")
    print(f"Golden before:                 {before_count}")
    print(f"Golden promotions:             {promote_count}")
    print(f"Golden after:                  {after_count}")
    print(f"Excel written:                 {'YES' if excel_written else 'NO'}")
    print("Excel integrity:               PASS")
    print(f"Last successful message UID:   {candidate_uid}")
    print(f"Watermark metadata:            {state['last_successful_watermark_utc']}")
    print(f"Finalization manifest:         {final_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
