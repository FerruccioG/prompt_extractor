#!/usr/bin/env python3
"""
remote_email_reader.py

Remote Opportunities Gmail ingestion.

This reader deliberately leaves the original Prompt Extractor email reader
unchanged. It reuses the proven Gmail/IMAP helpers but changes URL discovery
for the Remote Opportunities pipeline:

- Gmail query is fixed to: subject:Remote
- Extract ALL HTTP/HTTPS URLs, not only known social-media domains
- Preserve per-email provenance
- Classify each URL as social or web and record the detected platform
- Deduplicate globally before writing the downstream URL queue

Outputs remain compatible with the existing pipeline:
- data/email_url_audit.jsonl
- data/url_queue.jsonl

Required environment variables:
- EMAIL_ADDRESS
- EMAIL_APP_PASSWORD

Optional environment variables:
- IMAP_SERVER   (default: imap.gmail.com)
- IMAP_PORT     (default: 993)
- MAILBOX       (default: INBOX)
- OUTPUT_DIR    (default: data)
"""

from __future__ import annotations

import html
import imaplib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

# Make standalone execution behave the same way as the pipeline orchestrator.
# Existing shell/environment values still take precedence.
load_dotenv(ROOT_DIR / ".env", override=False)

from tools.ingestion.email_reader import (
    SOCIAL_DOMAINS,
    decode_mime_header,
    fetch_email_by_uid,
    getenv_required,
    get_text_parts,
    normalize_email_datetime,
    search_message_ids,
)

GMAIL_QUERY = "subject:Remote"

URL_REGEX = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)

SOCIAL_PLATFORM_DOMAINS = {
    "instagram": {
        "instagram.com",
        "www.instagram.com",
        "m.instagram.com",
    },
    "tiktok": {
        "tiktok.com",
        "www.tiktok.com",
        "m.tiktok.com",
    },
    "facebook": {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "fb.watch",
    },
    "x": {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
    },
    "youtube": {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    },
    "linkedin": {
        "linkedin.com",
        "www.linkedin.com",
    },
    "pinterest": {
        "pinterest.com",
        "www.pinterest.com",
    },
}


def normalize_discovered_url(raw_url: str) -> str:
    """
    Perform conservative ingestion-time cleanup only.

    Full canonicalization belongs to the downstream URL normalizer. Here we
    decode HTML entities and remove punctuation commonly captured at the end
    of prose URLs.
    """
    value = html.unescape(raw_url or "").strip()
    return value.rstrip(".,);]}>\"'")


def extract_all_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_REGEX.findall(text or ""):
        url = normalize_discovered_url(match)
        if url:
            urls.append(url)
    return urls


def hostname_for(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def detect_platform(url: str) -> str:
    host = hostname_for(url)
    if not host:
        return "unknown"

    for platform, domains in SOCIAL_PLATFORM_DOMAINS.items():
        if host in domains:
            return platform

    return "web"


def landing_type_for(url: str) -> str:
    host = hostname_for(url)
    if host in SOCIAL_DOMAINS:
        return "social"
    return "web"


def main() -> int:
    email_address = getenv_required("EMAIL_ADDRESS")
    email_app_password = getenv_required("EMAIL_APP_PASSWORD")

    # Force the Remote-opportunity query for this pipeline only.
    os.environ["GMAIL_QUERY"] = GMAIL_QUERY

    imap_server = os.getenv("IMAP_SERVER", "imap.gmail.com").strip()
    imap_port = int(os.getenv("IMAP_PORT", "993"))
    mailbox = os.getenv("MAILBOX", "INBOX").strip()
    output_dir = Path(os.getenv("OUTPUT_DIR", "data")).resolve()

    output_dir.mkdir(parents=True, exist_ok=True)

    audit_path = output_dir / "email_url_audit.jsonl"
    queue_path = output_dir / "url_queue.jsonl"

    unique_urls: set[str] = set()
    audit_rows: list[dict] = []
    queue_rows: list[dict] = []

    social_url_count = 0
    web_url_count = 0

    mail: imaplib.IMAP4_SSL | None = None

    try:
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
        mail.login(email_address, email_app_password)

        uids = search_message_ids(mail, mailbox, GMAIL_QUERY)

        for uid in uids:
            try:
                msg = fetch_email_by_uid(mail, uid)
            except Exception as exc:
                print(
                    f"Warning: could not fetch UID {uid!r}: {exc}",
                    file=sys.stderr,
                )
                continue

            message_id = uid.decode(errors="replace")
            subject = decode_mime_header_safe(msg.get("Subject", ""))
            from_value = decode_mime_header_safe(msg.get("From", ""))
            email_datetime = normalize_email_datetime(msg.get("Date", ""))

            message_urls: set[str] = set()

            for text_part in get_text_parts_safe(msg):
                for url in extract_all_urls(text_part):
                    message_urls.add(url)

            if not message_urls:
                continue

            sorted_urls = sorted(message_urls)

            social_in_message = sum(
                1 for url in sorted_urls if landing_type_for(url) == "social"
            )
            web_in_message = len(sorted_urls) - social_in_message

            audit_rows.append(
                {
                    "message_id": message_id,
                    "subject": subject,
                    "from": from_value,
                    "email_datetime": email_datetime,
                    "gmail_query": GMAIL_QUERY,
                    "url_count": len(sorted_urls),
                    "social_url_count": social_in_message,
                    "web_url_count": web_in_message,
                    "urls": sorted_urls,
                }
            )

            for url in sorted_urls:
                if url in unique_urls:
                    continue

                unique_urls.add(url)
                landing_type = landing_type_for(url)
                platform = detect_platform(url)

                if landing_type == "social":
                    social_url_count += 1
                else:
                    web_url_count += 1

                queue_rows.append(
                    {
                        "url": url,
                        "source_message_id": message_id,
                        "source_subject": subject,
                        "source_from": from_value,
                        "email_datetime": email_datetime,
                        "gmail_query": GMAIL_QUERY,
                        "landing_type": landing_type,
                        "platform": platform,
                        "status": "pending",
                    }
                )

        with audit_path.open("w", encoding="utf-8") as handle:
            for row in audit_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        with queue_path.open("w", encoding="utf-8") as handle:
            for row in queue_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "gmail_query": GMAIL_QUERY,
                    "emails_matched": len(uids),
                    "emails_with_urls": len(audit_rows),
                    "unique_urls": len(queue_rows),
                    "unique_social_urls": social_url_count,
                    "unique_web_urls": web_url_count,
                    "audit_output": str(audit_path),
                    "queue_output": str(queue_path),
                },
                ensure_ascii=False,
            )
        )

        return 0

    except imaplib.IMAP4.error as exc:
        print(f"IMAP error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
