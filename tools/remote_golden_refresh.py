#!/usr/bin/env python3
"""
remote_golden_refresh.py

Remote Job Opportunities Golden Master List Refresh.

Current foundation:
- persistent state/watermark foundation
- incremental Gmail intake for subject:Remote
- preserve raw email text and raw URLs
- classify URLs into actionable/context/noise
- canonicalize actionable LinkedIn job/post/group targets
- run-specific queues
- UID is the operational checkpoint authority
- any email fetch error blocks commit/watermark advancement
- watermark is intentionally NOT advanced by this intake stage
"""

from __future__ import annotations

import imaplib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

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


def compact_email_text(parts: list[str]) -> str:
    """
    Preserve the readable email body as evidence without altering the raw message.

    We retain decoded text/plain and text/html content because notification text can
    expose job title, employer, remote scope, location, salary, and other signals
    before a downstream browser visit.
    """
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def classify_harvest_url(url: str, intake_channel: str) -> dict:
    """
    Classify one raw email URL.

    Buckets:
    - actionable: worth downstream harvesting
    - context: useful evidence, but not a primary harvest target
    - noise: template/CDN/navigation/app-store/tracking material

    Raw URLs are always preserved separately in email_audit.jsonl/url_queue.jsonl.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    if intake_channel == "self_submitted":
        if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
            clean_path = path.rstrip("/") + "/"
            if clean_path.startswith("/reel/"):
                return {
                    "bucket": "actionable",
                    "canonical_url": f"https://www.instagram.com{clean_path}",
                    "reason": "instagram_reel",
                }
            if clean_path.startswith("/p/"):
                return {
                    "bucket": "actionable",
                    "canonical_url": f"https://www.instagram.com{clean_path}",
                    "reason": "instagram_post",
                }

        return {
            "bucket": "actionable",
            "canonical_url": url,
            "reason": "self_submitted_discovery",
        }

    hard_noise_hosts = {
        "media.licdn.com",
        "static.licdn.com",
        "www.w3.org",
        "itunes.apple.com",
        "play.google.com",
    }
    if host in hard_noise_hosts:
        return {
            "bucket": "noise",
            "canonical_url": None,
            "reason": "template_asset_or_app_link",
        }

    linkedin_hosts = {"linkedin.com", "www.linkedin.com"}
    if host in linkedin_hosts:
        job_match = re.search(r"/comm/jobs/view/(\d+)", path)
        if job_match:
            job_id = job_match.group(1)
            return {
                "bucket": "actionable",
                "canonical_url": f"https://www.linkedin.com/jobs/view/{job_id}/",
                "reason": "linkedin_job",
            }

        update_match = re.search(r"/comm/feed/update/(urn:li:[^/?]+)", path)
        if update_match:
            urn = update_match.group(1)
            return {
                "bucket": "actionable",
                "canonical_url": f"https://www.linkedin.com/feed/update/{urn}",
                "reason": "linkedin_post",
            }

        group_match = re.search(r"/comm/groups/(\d+)", path)
        if group_match:
            group_id = group_match.group(1)
            return {
                "bucket": "actionable",
                "canonical_url": f"https://www.linkedin.com/groups/{group_id}/",
                "reason": "linkedin_group",
            }

        if path.startswith("/comm/jobs/search-results/"):
            return {
                "bucket": "context",
                "canonical_url": url,
                "reason": "linkedin_job_search_context",
            }

        linkedin_noise_prefixes = (
            "/comm/feed/",
            "/comm/jobs/alerts",
            "/comm/messaging/",
            "/comm/mynetwork/",
            "/comm/mypreferences/",
            "/comm/notifications/",
            "/comm/premium/",
            "/help/",
            "/emimp/",
        )
        if path.startswith(linkedin_noise_prefixes):
            return {
                "bucket": "noise",
                "canonical_url": None,
                "reason": "linkedin_template_or_navigation",
            }

        return {
            "bucket": "context",
            "canonical_url": url,
            "reason": "linkedin_unclassified_context",
        }

    # Preserve recall for unfamiliar inbound providers.
    return {
        "bucket": "actionable",
        "canonical_url": url,
        "reason": "inbound_non_linkedin_preserve_recall",
    }


def ingest_new_remote_emails(state: dict, run_started: datetime, run_dir: Path) -> dict:
    email_address = getenv_required("EMAIL_ADDRESS")
    email_app_password = getenv_required("EMAIL_APP_PASSWORD")

    imap_server = os.getenv("IMAP_SERVER", "imap.gmail.com").strip()
    imap_port = int(os.getenv("IMAP_PORT", "993"))
    mailbox = os.getenv("MAILBOX", "INBOX").strip()

    last_uid_raw = state.get("last_successful_message_uid")
    last_uid = int(last_uid_raw) if last_uid_raw not in (None, "") else None
    watermark = parse_utc(state["last_successful_watermark_utc"])

    base_query = "subject:Remote"
    gmail_query = (
        base_query
        if last_uid is not None
        else f"{base_query} after:2026/10/06"
    )

    email_rows: list[dict] = []
    queue_rows: list[dict] = []
    actionable_rows: list[dict] = []
    context_rows: list[dict] = []
    noise_rows: list[dict] = []

    unique_urls: set[str] = set()
    unique_actionable: set[str] = set()
    unique_context: set[str] = set()

    matched_uids = 0
    eligible_uids = 0
    skipped_before_watermark = 0
    skipped_old_uid = 0
    self_submitted_emails = 0
    inbound_notification_emails = 0
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

            from_value = decode_mime_header_safe(msg.get("From", ""))
            sender_address = parseaddr(from_value)[1].strip().lower()

            intake_channel = (
                "self_submitted"
                if sender_address == email_address.strip().lower()
                else "inbound_notification"
            )

            parsed_email_dt = email_date_utc(msg.get("Date", ""))

            # Bootstrap safety: Gmail's after: query is day-granular, so enforce
            # the precise historical watermark ourselves.
            if last_uid is None:
                if parsed_email_dt is None or parsed_email_dt <= watermark:
                    skipped_before_watermark += 1
                    continue

            eligible_uids += 1

            if intake_channel == "self_submitted":
                self_submitted_emails += 1
            else:
                inbound_notification_emails += 1

            newest_uid = numeric_uid if newest_uid is None else max(newest_uid, numeric_uid)

            if parsed_email_dt is not None:
                if newest_email_datetime is None or parsed_email_dt > newest_email_datetime:
                    newest_email_datetime = parsed_email_dt

            subject = decode_mime_header_safe(msg.get("Subject", ""))

            text_parts = list(get_text_parts_safe(msg))
            message_text = compact_email_text(text_parts)

            message_urls: set[str] = set()
            for text_part in text_parts:
                message_urls.update(extract_all_urls(text_part))

            sorted_urls = sorted(message_urls)

            email_rows.append({
                "message_uid": numeric_uid,
                "status": "ok",
                "subject": subject,
                "from": from_value,
                "intake_channel": intake_channel,
                "email_datetime_utc": iso_or_blank(parsed_email_dt),
                "url_count": len(sorted_urls),
                "urls": sorted_urls,
                "email_text": message_text,
            })

            for url in sorted_urls:
                if url in unique_urls:
                    continue

                unique_urls.add(url)

                raw_row = {
                    "url": url,
                    "source_message_uid": numeric_uid,
                    "source_subject": subject,
                    "intake_channel": intake_channel,
                    "email_datetime_utc": iso_or_blank(parsed_email_dt),
                    "landing_type": landing_type_for(url),
                    "platform": detect_platform(url),
                    "status": "pending",
                }
                queue_rows.append(raw_row)

                decision = classify_harvest_url(url, intake_channel)
                classified_row = {
                    **raw_row,
                    "bucket": decision["bucket"],
                    "canonical_url": decision["canonical_url"],
                    "classification_reason": decision["reason"],
                }

                if decision["bucket"] == "actionable":
                    key = decision["canonical_url"] or url
                    if key not in unique_actionable:
                        unique_actionable.add(key)
                        actionable_rows.append(classified_row)
                elif decision["bucket"] == "context":
                    key = decision["canonical_url"] or url
                    if key not in unique_context:
                        unique_context.add(key)
                        context_rows.append(classified_row)
                else:
                    noise_rows.append(classified_row)

    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass

    email_audit_path = run_dir / "email_audit.jsonl"
    url_queue_path = run_dir / "url_queue.jsonl"
    actionable_path = run_dir / "actionable_url_queue.jsonl"
    context_path = run_dir / "context_url_queue.jsonl"
    noise_path = run_dir / "noise_url_queue.jsonl"

    write_jsonl(email_audit_path, email_rows)
    write_jsonl(url_queue_path, queue_rows)
    write_jsonl(actionable_path, actionable_rows)
    write_jsonl(context_path, context_rows)
    write_jsonl(noise_path, noise_rows)

    result = {
        "gmail_query": gmail_query,
        "matched_uids": matched_uids,
        "eligible_new_emails": eligible_uids,
        "emails_with_fetch_errors": fetch_errors,
        "skipped_old_uid": skipped_old_uid,
        "self_submitted_emails": self_submitted_emails,
        "inbound_notification_emails": inbound_notification_emails,
        "skipped_before_watermark": skipped_before_watermark,
        "unique_urls": len(queue_rows),
        "actionable_urls": len(actionable_rows),
        "context_urls": len(context_rows),
        "noise_urls": len(noise_rows),
        "candidate_newest_message_uid": newest_uid,
        "candidate_newest_email_datetime_utc": iso_or_blank(newest_email_datetime),
        "checkpoint_authority": "gmail_uid",
        "commit_eligible": fetch_errors == 0,
        "blocking_errors": (["email_fetch_errors"] if fetch_errors else []),
        "no_new_email_noop": eligible_uids == 0 and fetch_errors == 0,
        "email_audit_path": str(email_audit_path),
        "url_queue_path": str(url_queue_path),
        "actionable_url_queue_path": str(actionable_path),
        "context_url_queue_path": str(context_path),
        "noise_url_queue_path": str(noise_path),
    }

    return result



def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def run_stage(label: str, script_name: str, run_dir: Path, manifest_path: Path) -> None:
    """Run one existing run-scoped task and record it in the run manifest."""
    script = ROOT_DIR / "tools" / script_name
    if not script.exists():
        raise RuntimeError(f"Required stage script not found: {script}")

    manifest = load_json(manifest_path)
    stages = manifest.setdefault("pipeline_stages", [])
    started = utc_now_iso()

    print()
    print("=" * 72)
    print(f"STAGE: {label}")
    print(f"Script: {script.relative_to(ROOT_DIR)}")
    print("=" * 72)

    result = subprocess.run(
        [sys.executable, "-u", str(script), "--run-dir", str(run_dir)],
        cwd=str(ROOT_DIR),
        check=False,
    )

    stages.append({
        "label": label,
        "script": str(script.relative_to(ROOT_DIR)),
        "started_utc": started,
        "finished_utc": utc_now_iso(),
        "return_code": result.returncode,
        "status": "ok" if result.returncode == 0 else "failed",
    })

    if result.returncode != 0:
        manifest["status"] = "pipeline_failed"
        manifest["failed_stage"] = label
        manifest["commit_eligible"] = False
        manifest.setdefault("blocking_errors", []).append(f"stage_failed:{label}")
        write_json_atomic(manifest_path, manifest)
        raise RuntimeError(
            f"Stage failed: {label} ({script.name}) rc={result.returncode}"
        )

    manifest["status"] = "pipeline_running"
    write_json_atomic(manifest_path, manifest)



def run_terminal_finalizer(run_dir: Path, manifest_path: Path) -> None:
    """
    Run finalization without any successful-path writes afterward.

    The finalizer commits the refresh state/watermark last. Therefore this
    orchestrator records the stage start before invoking it and, on success,
    performs no further filesystem mutation.
    """
    script = ROOT_DIR / "tools" / "remote_refresh_finalize.py"
    manifest = load_json(manifest_path)
    stages = manifest.setdefault("pipeline_stages", [])
    stages.append({
        "label": "publication and finalization",
        "script": str(script.relative_to(ROOT_DIR)),
        "started_utc": utc_now_iso(),
        "return_code": None,
        "status": "started_terminal_stage",
    })
    manifest["status"] = "finalization_started"
    write_json_atomic(manifest_path, manifest)

    print()
    print("=" * 72)
    print("STAGE: publication and finalization")
    print(f"Script: {script.relative_to(ROOT_DIR)}")
    print("=" * 72)

    result = subprocess.run(
        [sys.executable, "-u", str(script), "--run-dir", str(run_dir)],
        cwd=str(ROOT_DIR),
        check=False,
    )
    if result.returncode != 0:
        # Finalizer guarantees that a failed return does not advance state, so
        # recording the failure here is safe.
        manifest = load_json(manifest_path)
        manifest["status"] = "pipeline_failed"
        manifest["failed_stage"] = "publication and finalization"
        manifest["commit_eligible"] = False
        manifest.setdefault("blocking_errors", []).append(
            "stage_failed:publication and finalization"
        )
        write_json_atomic(manifest_path, manifest)
        raise RuntimeError(
            f"Finalization failed rc={result.returncode}"
        )


def run_general_scoring_if_needed(run_dir: Path, manifest_path: Path) -> None:
    queue = run_dir / "dedupe" / "new_source_validation_queue.jsonl"
    if jsonl_count(queue) == 0:
        print("GENERAL VALIDATION: no genuinely new candidate sources.")
        return

    run_stage(
        "general source validation",
        "remote_incremental_source_validator.py",
        run_dir,
        manifest_path,
    )
    run_stage(
        "general semantic validation assessment",
        "remote_incremental_validation_assessor.py",
        run_dir,
        manifest_path,
    )

    eligible = run_dir / "validation" / "eligible_new_sources.jsonl"
    if jsonl_count(eligible) == 0:
        print("GENERAL SCORING: no semantically eligible new sources.")
        return

    run_stage(
        "general relationship scoring",
        "remote_incremental_relationship_scorer.py",
        run_dir,
        manifest_path,
    )
    run_stage(
        "general candidate-source profiling",
        "remote_incremental_candidate_profiler.py",
        run_dir,
        manifest_path,
    )
    run_stage(
        "general final candidate-source scoring",
        "remote_incremental_final_scorer.py",
        run_dir,
        manifest_path,
    )


def run_instagram_and_generic(run_dir: Path, manifest_path: Path) -> None:
    instagram_queue = run_dir / "harvest" / "instagram_queue.jsonl"
    generic_queue = run_dir / "harvest" / "unclassified_queue.jsonl"

    if jsonl_count(instagram_queue):
        run_stage("Instagram harvest", "remote_instagram_harvester.py", run_dir, manifest_path)
        run_stage("Instagram OCR", "remote_instagram_ocr.py", run_dir, manifest_path)
        run_stage(
            "Instagram evidence extraction",
            "remote_instagram_evidence_builder.py",
            run_dir,
            manifest_path,
        )

        deeper = run_dir / "harvest" / "instagram" / "needs_deeper_harvest.jsonl"
        if jsonl_count(deeper):
            run_stage(
                "Instagram reel frame harvest",
                "remote_instagram_reel_frame_harvester.py",
                run_dir,
                manifest_path,
            )
            run_stage(
                "Instagram reel frame OCR",
                "remote_instagram_reel_frame_ocr.py",
                run_dir,
                manifest_path,
            )
            # Rebuild evidence with deep-frame OCR now available.
            run_stage(
                "Instagram evidence rebuild after deep reel OCR",
                "remote_instagram_evidence_builder.py",
                run_dir,
                manifest_path,
            )

        run_stage(
            "Instagram candidate-name resolution",
            "remote_instagram_name_resolver.py",
            run_dir,
            manifest_path,
        )

    if jsonl_count(generic_queue):
        run_stage(
            "generic web harvest",
            "remote_generic_web_harvester.py",
            run_dir,
            manifest_path,
        )

    if jsonl_count(instagram_queue) or jsonl_count(generic_queue):
        run_stage(
            "general hard dedupe",
            "remote_incremental_candidate_dedupe.py",
            run_dir,
            manifest_path,
        )
        run_general_scoring_if_needed(run_dir, manifest_path)


def run_tiktok(run_dir: Path, manifest_path: Path) -> None:
    queue = run_dir / "harvest" / "tiktok_queue.jsonl"
    if jsonl_count(queue) == 0:
        return

    for label, script in (
        ("TikTok harvest", "remote_tiktok_harvester.py"),
        ("TikTok OCR", "remote_tiktok_ocr.py"),
        ("TikTok evidence extraction", "remote_tiktok_evidence_builder.py"),
        ("TikTok hard dedupe", "remote_tiktok_candidate_dedupe.py"),
    ):
        run_stage(label, script, run_dir, manifest_path)

    new_queue = (
        run_dir / "harvest" / "tiktok" / "dedupe" / "new_source_validation_queue.jsonl"
    )
    if jsonl_count(new_queue) == 0:
        print("TIKTOK VALIDATION: no genuinely new candidate sources.")
        return

    run_stage("TikTok source validation", "remote_tiktok_source_validator.py", run_dir, manifest_path)
    run_stage(
        "TikTok semantic validation assessment",
        "remote_tiktok_validation_assessor.py",
        run_dir,
        manifest_path,
    )

    eligible = run_dir / "harvest" / "tiktok" / "validation" / "eligible_new_sources.jsonl"
    if jsonl_count(eligible) == 0:
        print("TIKTOK SCORING: no semantically eligible new sources.")
        return

    for label, script in (
        ("TikTok relationship scoring", "remote_tiktok_relationship_scorer.py"),
        ("TikTok candidate-source profiling", "remote_tiktok_candidate_profiler.py"),
        ("TikTok final candidate-source scoring", "remote_tiktok_final_scorer.py"),
    ):
        run_stage(label, script, run_dir, manifest_path)


def run_linkedin(run_dir: Path, manifest_path: Path) -> None:
    queue = run_dir / "harvest" / "linkedin_queue.jsonl"
    if jsonl_count(queue) == 0:
        return

    for label, script in (
        ("LinkedIn harvest", "remote_linkedin_harvester.py"),
        ("LinkedIn OCR", "remote_linkedin_ocr.py"),
        ("LinkedIn evidence extraction", "remote_linkedin_evidence_builder.py"),
        ("LinkedIn name resolution", "remote_linkedin_name_resolver.py"),
        ("LinkedIn community resolution", "remote_linkedin_community_resolver.py"),
        ("LinkedIn hard dedupe", "remote_linkedin_candidate_dedupe.py"),
    ):
        run_stage(label, script, run_dir, manifest_path)

    new_queue = (
        run_dir / "harvest" / "linkedin" / "dedupe" / "new_source_validation_queue.jsonl"
    )
    if jsonl_count(new_queue) == 0:
        print("LINKEDIN VALIDATION: no genuinely new candidate sources.")
        return

    run_stage(
        "LinkedIn source validation",
        "remote_linkedin_source_validator.py",
        run_dir,
        manifest_path,
    )
    run_stage(
        "LinkedIn semantic validation assessment",
        "remote_linkedin_validation_assessor.py",
        run_dir,
        manifest_path,
    )

    eligible = run_dir / "harvest" / "linkedin" / "validation" / "eligible_new_sources.jsonl"
    if jsonl_count(eligible) == 0:
        print("LINKEDIN SCORING: no semantically eligible new sources.")
        return

    for label, script in (
        ("LinkedIn relationship scoring", "remote_linkedin_relationship_scorer.py"),
        ("LinkedIn candidate-source profiling", "remote_linkedin_candidate_profiler.py"),
        ("LinkedIn final candidate-source scoring", "remote_linkedin_final_scorer.py"),
    ):
        run_stage(label, script, run_dir, manifest_path)


def print_final_summary(run_dir: Path) -> None:
    reconciliation_path = run_dir / "reconciliation" / "reconciliation_manifest.json"
    finalization_path = run_dir / "finalization_manifest.json"

    reconciliation = load_json(reconciliation_path) if reconciliation_path.exists() else {}
    finalization = load_json(finalization_path) if finalization_path.exists() else {}

    print()
    print("=" * 72)
    print("REMOTE GOLDEN REFRESH COMPLETE")
    print("=" * 72)
    print(f"Run directory:                {run_dir}")
    print(f"Promote:                      {reconciliation.get('promote', 0)}")
    print(f"Hold / Review:                {reconciliation.get('hold_review', 0)}")
    print(f"Exclude:                      {reconciliation.get('exclude', 0)}")
    print(f"Check Later:                  {reconciliation.get('check_later', 0)}")
    print(f"Unresolved preserved:         {reconciliation.get('unresolved', 0)}")
    print(f"Golden before:                {finalization.get('golden_before', 'n/a')}")
    print(f"Golden after:                 {finalization.get('golden_after', 'n/a')}")
    print(f"Excel written:                {finalization.get('excel_written', False)}")
    print(f"Excel integrity:              {'PASS' if finalization.get('excel_integrity_passed') else 'n/a'}")
    print(f"Watermark advanced:           {finalization.get('watermark_advanced', False)}")


def main() -> int:
    state = load_state()
    run_started = utc_now()
    run_dir = create_run_dir(run_started)

    print("Remote Job Opportunities Golden Master List Refresh")
    print("=" * 72)
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

    if intake["emails_with_fetch_errors"]:
        manifest_status = "intake_incomplete_fetch_errors"
    elif intake["no_new_email_noop"]:
        manifest_status = "intake_noop_no_new_email"
    else:
        manifest_status = "intake_ok"

    manifest = {
        "status": manifest_status,
        "run_started_utc": iso_or_blank(run_started),
        "state_before": state,
        "intake": intake,
        "blocking_errors": intake.get("blocking_errors", []),
        "commit_eligible": bool(intake.get("commit_eligible")),
        "watermark_advanced": False,
        "pipeline_stages": [],
    }

    manifest_path = run_dir / "run_manifest.json"
    write_json_atomic(manifest_path, manifest)

    print("GMAIL INTAKE + URL CLASSIFICATION OK")
    print(f"Matched by Gmail query:     {intake['matched_uids']}")
    print(f"Eligible new emails:        {intake['eligible_new_emails']}")
    print(f"Self-submitted emails:      {intake['self_submitted_emails']}")
    print(f"Inbound notifications:      {intake['inbound_notification_emails']}")
    print(f"Unique raw URLs:            {intake['unique_urls']}")
    print(f"Actionable harvest URLs:    {intake['actionable_urls']}")
    print(f"Context-only URLs:          {intake['context_urls']}")
    print(f"Noise URLs:                 {intake['noise_urls']}")
    print(f"Fetch errors:               {intake['emails_with_fetch_errors']}")
    print(f"Candidate newest UID:       {intake['candidate_newest_message_uid'] or 'none'}")
    print()

    if intake["emails_with_fetch_errors"]:
        print("INTAKE INCOMPLETE: one or more emails could not be fetched.")
        print("This run is NOT eligible for commit or watermark advancement.")
        return 2

    try:
        if intake["no_new_email_noop"]:
            run_terminal_finalizer(run_dir, manifest_path)
            print_final_summary(run_dir)
            return 0

        run_stage("harvest routing", "remote_harvest_router.py", run_dir, manifest_path)
        run_instagram_and_generic(run_dir, manifest_path)
        run_tiktok(run_dir, manifest_path)
        run_linkedin(run_dir, manifest_path)

        run_stage(
            "whole-run reconciliation",
            "remote_refresh_reconcile.py",
            run_dir,
            manifest_path,
        )

        manifest = load_json(manifest_path)
        manifest["status"] = "reconciliation_ok"
        write_json_atomic(manifest_path, manifest)

        run_terminal_finalizer(run_dir, manifest_path)

        # Do not write anything after successful finalization: the state/UID
        # checkpoint committed by the finalizer must remain the last durable write.
        print_final_summary(run_dir)
        return 0

    except Exception as exc:
        print()
        print(f"REMOTE GOLDEN REFRESH FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Watermark was NOT advanced unless finalization had already completed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
