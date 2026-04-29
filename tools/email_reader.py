#!/usr/bin/env python3
"""
email_reader.py

Purpose:
- Connect to Gmail via IMAP
- Use a Gmail-native search query via X-GM-RAW
- Read all matching emails
- Extract social-media URLs from the email body
- Deduplicate globally across all emails
- Write structured JSONL outputs

Outputs:
- data/email_url_audit.jsonl   -> one record per email that produced URLs
- data/url_queue.jsonl         -> one record per unique URL for downstream processing

Required environment variables:
- EMAIL_ADDRESS
- EMAIL_APP_PASSWORD
- GMAIL_QUERY

Optional environment variables:
- IMAP_SERVER   (default: imap.gmail.com)
- IMAP_PORT     (default: 993)
- MAILBOX       (default: INBOX)
- OUTPUT_DIR    (default: data)
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import re
import sys
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


SOCIAL_DOMAINS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "fb.watch",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "linkedin.com",
    "www.linkedin.com",
    "pinterest.com",
    "www.pinterest.com",
}

URL_REGEX = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)


def getenv_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def decode_mime_header(raw_value: str | None) -> str:
    if not raw_value:
        return ""
    decoded_parts: list[str] = []
    for value, encoding in decode_header(raw_value):
        if isinstance(value, bytes):
            decoded_parts.append(value.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded_parts.append(value)
    return "".join(decoded_parts)


def normalize_email_datetime(raw_date: str) -> str:
    """
    Convert email Date header into ISO 8601 UTC string when possible.
    Fallback is the raw input if parsing fails.
    """
    if not raw_date:
        return ""
    try:
        dt = parsedate_to_datetime(raw_date)
        if dt.tzinfo is None:
            # Rare case: no timezone present. Keep naive as-is in ISO format.
            return dt.isoformat()
        return dt.astimezone().isoformat().replace("+00:00", "Z")
    except Exception:
        return raw_date


def normalize_url(url: str) -> str:
    return url.strip().rstrip(".,);]>\"'")


def is_social_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
        return host in SOCIAL_DOMAINS
    except Exception:
        return False


def extract_urls(text: str) -> list[str]:
    matches = [normalize_url(u) for u in URL_REGEX.findall(text or "")]
    return [u for u in matches if is_social_url(u)]


def get_text_parts(msg: Message) -> Iterable[str]:
    """
    Yield text/plain and text/html payloads, skipping attachments.
    """
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", "")).lower()

            if "attachment" in disposition:
                continue

            if content_type in {"text/plain", "text/html"}:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                yield payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            charset = msg.get_content_charset() or "utf-8"
            yield payload.decode(charset, errors="replace")


def search_message_ids(
    mail: imaplib.IMAP4_SSL,
    mailbox: str,
    gmail_query: str,
) -> list[bytes]:
    status, _ = mail.select(mailbox, readonly=True)
    if status != "OK":
        raise RuntimeError(f"Failed to select mailbox: {mailbox}")

    # Gmail-specific raw search syntax
    status, data = mail.uid("SEARCH", "X-GM-RAW", f'"{gmail_query}"')
    if status != "OK":
        raise RuntimeError(f"Gmail X-GM-RAW search failed for query: {gmail_query}")

    raw_ids = data[0].split() if data and data[0] else []
    return raw_ids


def fetch_email_by_uid(mail: imaplib.IMAP4_SSL, uid: bytes) -> Message:
    status, data = mail.uid("FETCH", uid, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        raise RuntimeError(f"Failed to fetch email UID {uid!r}")

    raw_email = data[0][1]
    return email.message_from_bytes(raw_email)


def main() -> int:
    email_address = getenv_required("EMAIL_ADDRESS")
    email_app_password = getenv_required("EMAIL_APP_PASSWORD")
    gmail_query = getenv_required("GMAIL_QUERY")

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

    try:
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
        mail.login(email_address, email_app_password)

        uids = search_message_ids(mail, mailbox, gmail_query)

        for uid in uids:
            try:
                msg = fetch_email_by_uid(mail, uid)
            except Exception as exc:
                print(f"Warning: could not fetch UID {uid!r}: {exc}", file=sys.stderr)
                continue

            subject = decode_mime_header(msg.get("Subject", ""))
            from_value = decode_mime_header(msg.get("From", ""))
            email_datetime = normalize_email_datetime(msg.get("Date", ""))

            message_urls: set[str] = set()
            for text_part in get_text_parts(msg):
                for url in extract_urls(text_part):
                    message_urls.add(url)

            if not message_urls:
                continue

            sorted_urls = sorted(message_urls)

            audit_rows.append(
                {
                    "message_id": uid.decode(errors="replace"),
                    "subject": subject,
                    "from": from_value,
                    "email_datetime": email_datetime,
                    "gmail_query": gmail_query,
                    "url_count": len(sorted_urls),
                    "urls": sorted_urls,
                }
            )

            for url in sorted_urls:
                if url in unique_urls:
                    continue
                unique_urls.add(url)

                queue_rows.append(
                    {
                        "url": url,
                        "source_message_id": uid.decode(errors="replace"),
                        "source_subject": subject,
                        "email_datetime": email_datetime,
                        "gmail_query": gmail_query,
                        "status": "pending",
                    }
                )

        with audit_path.open("w", encoding="utf-8") as f:
            for row in audit_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        with queue_path.open("w", encoding="utf-8") as f:
            for row in queue_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "gmail_query": gmail_query,
                    "emails_matched": len(uids),
                    "emails_with_urls": len(audit_rows),
                    "unique_social_urls": len(queue_rows),
                    "audit_output": str(audit_path),
                    "queue_output": str(queue_path),
                },
                ensure_ascii=False,
            )
        )

        mail.logout()
        return 0

    except imaplib.IMAP4.error as exc:
        print(f"IMAP error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
