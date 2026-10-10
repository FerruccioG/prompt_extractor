#!/usr/bin/env python3
"""
remote_generic_web_harvester.py

Conservative run-scoped harvester for actionable Remote URLs that are not
handled by the Instagram, TikTok, or LinkedIn pipelines.

Input:
  <run>/harvest/unclassified_queue.jsonl

Outputs:
  <run>/harvest/generic/results.jsonl
  <run>/harvest/generic/candidate_source_evidence.jsonl
  <run>/harvest/generic/needs_resolution.jsonl
  <run>/harvest/generic/screenshots/
  <run>/harvest/generic/generic_manifest.json

This stage discovers candidate remote-source domains only. It does not validate,
score, publish to Excel, or advance the refresh checkpoint.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from tools.remote_source_identity import canonical_host_from_value


DOMAIN_RE = re.compile(
    r"(?<!@)\b(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?",
    re.IGNORECASE,
)

CANDIDATE_TERMS = (
    "remote", "job", "jobs", "career", "careers", "hiring", "hire",
    "vacancy", "vacancies", "recruit", "apply", "position", "opening",
    "opportunity", "opportunities", "freelance", "contract", "talent",
)

SOCIAL_OR_CONTEXT_HOSTS = {
    "facebook.com", "m.facebook.com", "www.facebook.com",
    "instagram.com", "www.instagram.com", "m.instagram.com",
    "tiktok.com", "www.tiktok.com",
    "linkedin.com", "www.linkedin.com",
    "x.com", "www.x.com", "twitter.com", "www.twitter.com",
    "youtube.com", "www.youtube.com", "youtu.be",
    "reddit.com", "www.reddit.com",
}

NOISE_HOSTS = {
    "google.com", "www.google.com", "googleapis.com",
    "gstatic.com", "doubleclick.net", "googletagmanager.com",
    "fonts.googleapis.com", "fonts.gstatic.com",
    "apple.com", "www.apple.com", "play.google.com",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "a" and self._href:
            anchor = " ".join(self._anchor_parts).strip()
            self.links.append((self._href, anchor))
            self._href = None
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        clean = " ".join(data.split())
        if not clean:
            return
        self.text_parts.append(clean)
        if self._href is not None:
            self._anchor_parts.append(clean)


def has_candidate_terms(value: str) -> bool:
    lowered = value.lower()
    return any(term in lowered for term in CANDIDATE_TERMS)


def allowed_candidate_host(host: str, page_host: str = "") -> bool:
    if not host:
        return False
    if host in SOCIAL_OR_CONTEXT_HOSTS or host in NOISE_HOSTS:
        return False
    if host.endswith(".googleusercontent.com") or host.endswith(".googleapis.com"):
        return False
    return True


def add_evidence(
    evidence: dict[str, dict[str, Any]],
    host: str,
    row: dict[str, Any],
    evidence_type: str,
    observed: str,
    final_url: str,
) -> None:
    host = canonical_host_from_value(host)
    if not host or not allowed_candidate_host(host):
        return

    item = evidence.setdefault(host, {
        "candidate_domain": host,
        "candidate_url": f"https://{host}/",
        "source_platform": "generic_web",
        "source_url": row.get("canonical_url") or row.get("normalized_url") or row.get("raw_url"),
        "source_message_uid": row.get("source_message_uid"),
        "source_subject": row.get("source_subject"),
        "intake_channel": row.get("intake_channel"),
        "email_datetime_utc": row.get("email_datetime_utc"),
        "final_harvest_url": final_url,
        "evidence_type": evidence_type,
        "confidence": "medium",
        "observations": [],
    })
    item["observations"].append(observed[:1000])


def browser_fallback(url: str, screenshot_path: Path) -> tuple[str, str]:
    """Return browser-visible text and OCR text. Empty strings mean fallback unavailable."""
    try:
        from playwright.sync_api import sync_playwright
        import pytesseract
        from PIL import Image
    except Exception:
        return "", ""

    visible_text = ""
    ocr_text = ""
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1200})
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            try:
                visible_text = page.locator("body").inner_text(timeout=5000)
            except Exception:
                visible_text = ""
            page.screenshot(path=str(screenshot_path), full_page=True)
            browser.close()

        with Image.open(screenshot_path) as image:
            ocr_text = pytesseract.image_to_string(image.convert("RGB"), lang="eng")
    except Exception:
        return visible_text, ocr_text

    return visible_text, ocr_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(ROOT_DIR)
    input_path = run_dir / "harvest" / "unclassified_queue.jsonl"
    out_dir = run_dir / "harvest" / "generic"
    screenshots_dir = out_dir / "screenshots"

    if not input_path.exists():
        raise RuntimeError(f"Generic/unclassified queue not found: {input_path}")

    rows = load_jsonl(input_path)
    results: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/154.0 Safari/537.36"
    })

    print("REMOTE GENERIC WEB HARVEST")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Targets:                   {len(rows)}")
    print()

    for index, row in enumerate(rows, start=1):
        source_url = str(row.get("canonical_url") or row.get("normalized_url") or row.get("raw_url") or "")
        print(f"[{index}/{len(rows)}] {source_url}")

        harvested: dict[str, Any] = {
            "source_url": source_url,
            "source_message_uid": row.get("source_message_uid"),
            "source_subject": row.get("source_subject"),
            "intake_channel": row.get("intake_channel"),
            "status": "pending",
            "processed_at_utc": utc_now_iso(),
        }

        candidate_map: dict[str, dict[str, Any]] = {}

        try:
            response = session.get(source_url, timeout=30, allow_redirects=True)
            final_url = response.url
            final_host = canonical_host_from_value(final_url)
            content_type = (response.headers.get("Content-Type") or "").lower()
            harvested.update({
                "status": "http_ok" if response.ok else "http_error",
                "http_status": response.status_code,
                "final_url": final_url,
                "final_host": final_host,
                "content_type": content_type,
            })

            parser_obj = PageParser()
            page_text = ""
            links: list[tuple[str, str]] = []

            if "html" in content_type or "<html" in response.text[:1000].lower():
                parser_obj.feed(response.text)
                page_text = " ".join(parser_obj.text_parts)
                links = parser_obj.links

            # A directly submitted non-social URL is itself meaningful evidence.
            if row.get("intake_channel") == "self_submitted" and allowed_candidate_host(final_host):
                add_evidence(
                    candidate_map,
                    final_host,
                    row,
                    "direct_self_submitted_url",
                    final_url,
                    final_url,
                )

            # Explicit domains visible in page text are useful even when not linked.
            for match in DOMAIN_RE.finditer(page_text):
                host = canonical_host_from_value(match.group(1))
                if host and host != final_host and allowed_candidate_host(host, final_host):
                    add_evidence(
                        candidate_map,
                        host,
                        row,
                        "visible_domain_text",
                        match.group(0),
                        final_url,
                    )

            # Keep external links only when their anchor or URL looks opportunity-related.
            for href, anchor in links:
                absolute = urljoin(final_url, href)
                host = canonical_host_from_value(absolute)
                if not host or host == final_host or not allowed_candidate_host(host, final_host):
                    continue
                if has_candidate_terms(anchor) or has_candidate_terms(absolute):
                    add_evidence(
                        candidate_map,
                        host,
                        row,
                        "opportunity_link",
                        f"{anchor} | {absolute}",
                        final_url,
                    )

            # If direct HTML did not expose anything, use browser-visible text and
            # full-page OCR as a conservative fallback for image/screenshot content.
            if not candidate_map:
                screenshot_path = screenshots_dir / f"generic_{index:03d}.png"
                visible_text, ocr_text = browser_fallback(final_url, screenshot_path)
                combined = "\n".join(x for x in (visible_text, ocr_text) if x)
                harvested["browser_fallback_used"] = bool(combined)
                harvested["screenshot_path"] = str(screenshot_path) if screenshot_path.exists() else ""

                for match in DOMAIN_RE.finditer(combined):
                    host = canonical_host_from_value(match.group(1))
                    if host and host != final_host and allowed_candidate_host(host, final_host):
                        add_evidence(
                            candidate_map,
                            host,
                            row,
                            "browser_or_ocr_domain_text",
                            match.group(0),
                            final_url,
                        )

            evidence_rows.extend(candidate_map.values())
            harvested["candidate_count"] = len(candidate_map)
            results.append(harvested)

            if not candidate_map:
                unresolved.append({
                    **row,
                    "source_platform": "generic_web",
                    "harvest_status": harvested["status"],
                    "final_url": final_url,
                    "final_host": final_host,
                    "resolution_reason": "no_candidate_remote_source_extracted",
                })

        except Exception as exc:
            harvested.update({
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            results.append(harvested)
            unresolved.append({
                **row,
                "source_platform": "generic_web",
                "resolution_reason": "generic_harvest_error",
                "error": harvested["error"],
            })

    results_path = out_dir / "results.jsonl"
    evidence_path = out_dir / "candidate_source_evidence.jsonl"
    unresolved_path = out_dir / "needs_resolution.jsonl"
    manifest_path = out_dir / "generic_manifest.json"

    write_jsonl(results_path, results)
    write_jsonl(evidence_path, evidence_rows)
    write_jsonl(unresolved_path, unresolved)

    manifest = {
        "status": "ok",
        "input_targets": len(rows),
        "candidate_evidence_rows": len(evidence_rows),
        "needs_resolution_rows": len(unresolved),
        "results": str(results_path),
        "candidate_evidence": str(evidence_path),
        "needs_resolution": str(unresolved_path),
        "excel_touched": False,
        "watermark_advanced": False,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print()
    print("REMOTE GENERIC WEB HARVEST COMPLETE")
    print(f"Candidate evidence rows:    {len(evidence_rows)}")
    print(f"Needs resolution:           {len(unresolved)}")
    print("No source was validated or scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
