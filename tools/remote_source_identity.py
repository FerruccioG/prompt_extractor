#!/usr/bin/env python3
"""Shared source-identity helpers for the Remote Golden refresh pipeline.

Identity rule
-------------
The canonical host is the source key. Only the cosmetic ``www.`` prefix is
collapsed. Regional domains remain distinct (example.com != example.ie !=
example.co.uk). Cross-domain equivalence is established only by explicit live
redirect/final-URL evidence in validation, not by brand-name similarity.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

DOMAINISH_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,})(?:[/:?#].*)?$",
    re.IGNORECASE,
)

SOURCE_KEYS = (
    "Source",
    "source",
    "canonical_host",
    "assessed_canonical_host",
    "final_host",
    "original_candidate_host",
    "candidate_domain",
    "host",
    "domain",
)

URL_KEYS = (
    "canonical_root_url",
    "candidate_url",
    "final_url",
    "url",
    "URL",
    "website",
    "Website",
)

EVIDENCE_URL_KEYS = (
    "linkedin_url",
    "instagram_url",
    "tiktok_url",
    "requested_url",
)


def canonical_host_from_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""

    if "://" in text:
        try:
            host = (urlparse(text).hostname or "").lower().strip(".")
        except Exception:
            return ""
    else:
        match = DOMAINISH_RE.match(text)
        if not match:
            return ""
        host = match.group(1).lower().strip(".")

    return host[4:] if host.startswith("www.") else host


def row_hosts(row: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for key in SOURCE_KEYS:
        if key in row:
            host = canonical_host_from_value(row.get(key))
            if host:
                hosts.add(host)
    for key in URL_KEYS:
        if key in row:
            host = canonical_host_from_value(row.get(key))
            if host:
                hosts.add(host)
    return hosts


def source_key_from_row(row: dict[str, Any], *, allow_evidence_fallback: bool = True) -> str:
    # Prefer the explicitly canonical/final identity fields before historical
    # aliases. This matters when validation proved a redirect to another host.
    preferred = (
        "assessed_canonical_host",
        "canonical_host",
        "final_host",
        "Source",
        "source",
        "original_candidate_host",
        "candidate_domain",
    )
    for key in preferred:
        host = canonical_host_from_value(row.get(key))
        if host:
            return host

    for key in URL_KEYS:
        host = canonical_host_from_value(row.get(key))
        if host:
            return host

    if allow_evidence_fallback:
        for key in EVIDENCE_URL_KEYS:
            value = str(row.get(key) or "").strip().lower()
            if value:
                return value

    return ""
