#!/usr/bin/env python3
"""
remote_golden_refresh.py

Remote Job Opportunities Golden Master List Refresh.

Checkpoint 2:
- persistent state/watermark foundation
- incremental Gmail intake for subject:Remote
- run-specific audit and URL queue
- watermark is intentionally NOT advanced yet
"""

from __future__ import annotations

import imaplib
import json
import os
import sys
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env", override=False)

from tools.ingestion.email_reader import (
    fetch_email_by_uid,
    getenv_required,
    search_message_ids,
)
from tools.remote_email_reader import (
    decode_mime_header_safe,
    detect_platform,
    extract_all_urls,
    get_text_parts_safe,
    landing_type_for,
)


REMOTE_DATA_DIR = ROOT_DIR / "data" / "remote"
STATE_PATH = REMOTE_DATA_DIR / "remote_golden_refresh_state.json"
RUNS_DIR = REMOTE_DATA_DIR / "refresh_runs"

HISTORICAL_CUTOFF_LOCAL_DATE = "2026-10-06"
INITIAL_WATERMARK_UTC = "2026-10-06T23:00:00Z"
BOOTSTRAP_GMAIL_QUERY = "subject:Remote after:2026/10/06"
NORMAL_GMAIL_QUERY = "subject:Remote"
STATE_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def email_date_utc(raw_date: str) -> datetime | None:
    if not raw_date:
        return None

    try:
        value = parsedate_to_datetime(raw_date)
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    except Exception:
        return None


def iso_or_blank(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat().replace("+00:00", "Z")


def default_state() -> dict:
    return {
        "state_version": STATE_VERSION,
        "historical_cutoff_local_date": HISTORICAL_CUTOFF_LOCAL_DATE,
        "initial_watermark_utc": INITIAL_WATERMARK_UTC,
        "last_successful_watermark_utc": INITIAL_WATERMARK_UTC,
        "last_successful_run_utc": None,
        "last_successful_message_uid": None,
        "successful_run_count": 0,
    }


def write_state(state: dict) -> None:
    REMOTE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_PATH.with_suffix(".json.tmp")

    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    temp_path.replace(STATE_PATH)


def load_state() -> dict:
    REMOTE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not STATE_PATH.exists():
        state = default_state()
        write_state(state)
        return state

    with STATE_PATH.open("r", encoding="utf-8") as handle:
        state = json.load(handle)

    if state.get("historical_cutoff_local_date") != HISTORICAL_CUTOFF_LOCAL_DATE:
        raise RuntimeError(
            "Historical cutoff mismatch: "
            f"expected {HISTORICAL_CUTOFF_LOCAL_DATE}, "
            f"found {state.get('historical_cutoff_local_date')!r}"
        )

    return state


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def create_run_dir(run_started: datetime) -> Path:
    run_id = run_started.strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def uid_number(uid: bytes | str) -> int:
    if isinstance(uid, bytes):
        uid = uid.decode(errors="replace")
    return int(uid)


def ingest_new_remote_emails(state: dict, run_started: datetime, run_dir: Path) -> dict:
    email_address = getenv_required("EMAIL_ADDRESS")
    email_app_password = getenv_required("EMAIL_APP_PASSWORD")

    imap_server = os.getenv("IMAP_SERVER", "imap.gmail.com").strip()
    imap_port = int(os.getenv("IMAP_PORT", "993"))
    mailbox = os.getenv("MAILBOX", "INBOX").strip()

    last_uid_raw = state.get("last_successful_message_uid")
    last_uid = int(last_uid_raw) if last_uid_raw not in (None, "") else None
    watermark = parse_utc(state["last_successful_watermark_utc"])

    gmail_query = NORMAL_GMAIL_QUERY if last_uid is not None else BOOTSTRAP_GMAIL_QUERY

    email_rows: list[dict] = []
    queue_rows: list[dict] = []
    unique_urls: set[str] = set()

    matched_uids = 0
    eligible_uids = 0
    skipped_before_watermark = 0
    skipped_old_uid = 0
    fetch_errors = 0
    newest_uid: int | None = None
    newest_email_datetime: datetime | None = None

    mail: imaplib.IMAP4_SSL | None = None

    try:
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
        mail.login(email_address, email_app_password)

        uids = search_message_ids(mail, mailbox, gmail_query)
        matched_uids = len(uids)

        for uid in uids:
            numeric_uid = uid_number(uid)

            if last_uid is not None and numeric_uid <= last_uid:
                skipped_old_uid += 1
                continue

            try:
                msg = fetch_email_by_uid(mail, uid)
            except Exception as exc:
                fetch_errors += 1
                email_rows.append({
                    "message_uid": numeric_uid,
                    "status": "fetch_error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            parsed_email_dt = email_date_utc(msg.get("Date", ""))

            # Bootstrap safety: Gmail's after: query is day-granular, so enforce
            # the precise historical watermark ourselves.
            if last_uid is None:
                if parsed_email_dt is None or parsed_email_dt <= watermark:
                    skipped_before_watermark += 1
                    continue

            eligible_uids += 1
            newest_uid = numeric_uid if newest_uid is None else max(newest_uid, numeric_uid)

            if parsed_email_dt is not None:
                if newest_email_datetime is None or parsed_email_dt > newest_email_datetime:
                    newest_email_datetime = parsed_email_dt

            subject = decode_mime_header_safe(msg.get("Subject", ""))
            from_value = decode_mime_header_safe(msg.get("From", ""))

            message_urls: set[str] = set()
            for text_part in get_text_parts_safe(msg):
                message_urls.update(extract_all_urls(text_part))

            sorted_urls = sorted(message_urls)

            email_rows.append({
                "message_uid": numeric_uid,
                "status": "ok",
                "subject": subject,
                "from": from_value,
                "email_datetime_utc": iso_or_blank(parsed_email_dt),
                "url_count": len(sorted_urls),
                "urls": sorted_urls,
            })

            for url in sorted_urls:
                if url in unique_urls:
                    continue

                unique_urls.add(url)
                queue_rows.append({
                    "url": url,
                    "source_message_uid": numeric_uid,
                    "source_subject": subject,
                    "email_datetime_utc": iso_or_blank(parsed_email_dt),
                    "landing_type": landing_type_for(url),
                    "platform": detect_platform(url),
                    "status": "pending",
                })

    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass

    email_audit_path = run_dir / "email_audit.jsonl"
    url_queue_path = run_dir / "url_queue.jsonl"

    write_jsonl(email_audit_path, email_rows)
    write_jsonl(url_queue_path, queue_rows)

    result = {
        "gmail_query": gmail_query,
        "matched_uids": matched_uids,
        "eligible_new_emails": eligible_uids,
        "emails_with_fetch_errors": fetch_errors,
        "skipped_old_uid": skipped_old_uid,
        "skipped_before_watermark": skipped_before_watermark,
        "unique_urls": len(queue_rows),
        "candidate_newest_message_uid": newest_uid,
        "candidate_newest_email_datetime_utc": iso_or_blank(newest_email_datetime),
        "email_audit_path": str(email_audit_path),
        "url_queue_path": str(url_queue_path),
    }

    return result


def main() -> int:
    state = load_state()
    run_started = utc_now()
    run_dir = create_run_dir(run_started)

    print("Remote Job Opportunities Golden Master List Refresh")
    print("=" * 58)
    print(f"State file:                {STATE_PATH}")
    print(f"Run directory:             {run_dir}")
    print(f"Historical cutoff:         {state['historical_cutoff_local_date']} (frozen)")
    print(f"Last successful watermark: {state['last_successful_watermark_utc']}")
    print(f"Last successful message:   {state['last_successful_message_uid'] or 'none'}")
    print(f"Last successful run:       {state['last_successful_run_utc'] or 'never'}")
    print()

    try:
        intake = ingest_new_remote_emails(state, run_started, run_dir)
    except Exception as exc:
        print(f"INTAKE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Watermark was NOT advanced.")
        return 1

    manifest = {
        "status": "checkpoint_2_intake_complete",
        "run_started_utc": iso_or_blank(run_started),
        "state_before": state,
        "intake": intake,
        "watermark_advanced": False,
    }

    manifest_path = run_dir / "run_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print("GMAIL INTAKE OK")
    print(f"Matched by Gmail query:     {intake['matched_uids']}")
    print(f"Eligible new emails:        {intake['eligible_new_emails']}")
    print(f"Unique URLs extracted:      {intake['unique_urls']}")
    print(f"Fetch errors:               {intake['emails_with_fetch_errors']}")
    print(f"Candidate newest UID:       {intake['candidate_newest_message_uid'] or 'none'}")
    print(f"Email audit:                {intake['email_audit_path']}")
    print(f"URL queue:                  {intake['url_queue_path']}")
    print(f"Run manifest:               {manifest_path}")
    print()
    print("CHECKPOINT 2 COMPLETE")
    print("No social/web content was harvested.")
    print("No source was validated or scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
