#!/usr/bin/env python3
"""
remote_tiktok_evidence_builder.py

Build structured candidate-source evidence from run-scoped TikTok harvest text
and full-frame OCR.

Conservative rules:
- explicit domains/URLs become candidate evidence;
- clearly named known candidate/employer sources may become named candidates;
- TikTok itself is provenance, never the final Remote source;
- OCR-only ambiguity remains unresolved/check-later;
- no network validation, scoring, Excel publication, or watermark advance.

Inputs:
  <run>/harvest/tiktok/results.jsonl
  <run>/harvest/tiktok/ocr_raw.jsonl

Outputs:
  <run>/harvest/tiktok/candidate_source_evidence.jsonl
  <run>/harvest/tiktok/needs_resolution.jsonl
  <run>/harvest/tiktok/evidence_manifest.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


DOMAIN_RE = re.compile(
    r"\b(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})"
    r"(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?",
    re.IGNORECASE,
)

KNOWN_NAMED_SOURCES = {
    "replit": "https://replit.com/",
    "linkedin": "https://linkedin.com/",
    "indeed": "https://indeed.com/",
    "glassdoor": "https://glassdoor.com/",
    "ziprecruiter": "https://ziprecruiter.com/",
    "zip recruiter": "https://ziprecruiter.com/",
    "wellfound": "https://wellfound.com/",
    "we work remotely": "https://weworkremotely.com/",
    "weworkremotely": "https://weworkremotely.com/",
    "remoteok": "https://remoteok.com/",
    "remote ok": "https://remoteok.com/",
    "himalayas": "https://himalayas.app/",
    "built in": "https://builtin.com/",
}

GENERIC_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    # Documentation/test placeholders sometimes appear in social page boilerplate.
    "example.com",
    "example.org",
    "example.net",
}

PLAUSIBLE_TLDS = {
    "ai", "app", "co", "com", "dev", "eu", "ie", "io", "jobs",
    "net", "org", "tech", "today", "uk", "work",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def normalized_domain(value: str) -> str:
    value = value.strip().lower().rstrip(".,);]}>\"'")
    value = value.removeprefix("https://").removeprefix("http://")
    value = value.removeprefix("www.")
    return value.split("/", 1)[0]


def plausible_domain(domain: str) -> bool:
    parts = domain.lower().rstrip(".").split(".")
    return len(parts) >= 2 and parts[-1] in PLAUSIBLE_TLDS


def canonical_url(domain: str) -> str:
    return f"https://{domain}/"


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    tiktok_dir = run_dir / "harvest" / "tiktok"
    results_path = tiktok_dir / "results.jsonl"
    ocr_path = tiktok_dir / "ocr_raw.jsonl"

    if not results_path.exists():
        raise RuntimeError(f"TikTok harvest results not found: {results_path}")
    if not ocr_path.exists():
        raise RuntimeError(f"TikTok OCR not found: {ocr_path}")

    raw_results = read_jsonl(results_path)
    ocr_rows = [r for r in read_jsonl(ocr_path) if not r.get("event")]

    # Harvest results are append-only/resume-friendly. Collapse duplicate reruns
    # of the same TikTok target before evidence extraction.
    results_by_key: dict[str, dict[str, Any]] = {}
    for row in raw_results:
        key = str(
            row.get("requested_url")
            or row.get("normalized_url")
            or row.get("canonical_url")
            or row.get("final_url")
            or ""
        ).strip()
        if key:
            results_by_key[key] = row
    results = list(results_by_key.values())

    evidence: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen: set[str] = set()

    for result in results:
        tiktok_url = str(result.get("final_url") or result.get("requested_url") or "")
        requested_url = str(result.get("requested_url") or "")
        source_subject = str(result.get("source_subject") or "")

        # This refresh currently has one TikTok target, so OCR output belongs to
        # this result. The evidence retains each frame's provenance.
        page_and_ocr_parts = [
            str(result.get("page_title") or ""),
            str(result.get("visible_text") or ""),
        ]
        page_and_ocr_parts.extend(str(r.get("ocr_text_raw") or "") for r in ocr_rows)
        page_and_ocr_text = "\n".join(page_and_ocr_parts)
        page_and_ocr_lower = " ".join(page_and_ocr_text.lower().split())

        combined_text = page_and_ocr_text + "\n" + source_subject
        lower = " ".join(combined_text.lower().split())

        for match in DOMAIN_RE.finditer(combined_text):
            domain = normalized_domain(match.group(1))
            if not domain or domain in GENERIC_HOSTS or not plausible_domain(domain):
                continue
            if domain in seen:
                continue
            seen.add(domain)
            evidence.append({
                "tiktok_url": tiktok_url,
                "requested_url": requested_url,
                "evidence_type": "explicit_text_or_ocr_domain",
                "ocr_observed": match.group(0),
                "candidate_domain": domain,
                "candidate_url": canonical_url(domain),
                "confidence": "medium",
                "status": "needs_validation",
            })

        for label, url in KNOWN_NAMED_SOURCES.items():
            if label not in lower:
                continue
            domain = normalized_domain(urlparse(url).hostname or "")
            if not domain or domain in seen:
                continue

            if label in page_and_ocr_lower:
                evidence_type = "named_source_in_tiktok_page_or_ocr"
                confidence = "medium"
                provenance = "tiktok_page_or_video_ocr"
            else:
                evidence_type = "named_source_in_email_subject_only"
                confidence = "low"
                provenance = "email_subject"

            seen.add(domain)
            evidence.append({
                "tiktok_url": tiktok_url,
                "requested_url": requested_url,
                "evidence_type": evidence_type,
                "evidence_provenance": provenance,
                "ocr_observed": label,
                "candidate_domain": domain,
                "candidate_url": url,
                "confidence": confidence,
                "status": "needs_validation",
            })

        if not evidence:
            unresolved.append({
                "tiktok_url": tiktok_url,
                "requested_url": requested_url,
                "source_subject": source_subject,
                "status": "needs_resolution",
                "reason": "tiktok_harvest_and_ocr_exposed_no_resolvable_candidate_source",
                "ocr_frame_count": len(ocr_rows),
            })

    evidence_path = tiktok_dir / "candidate_source_evidence.jsonl"
    unresolved_path = tiktok_dir / "needs_resolution.jsonl"
    manifest_path = tiktok_dir / "evidence_manifest.json"

    write_jsonl(evidence_path, evidence)
    write_jsonl(unresolved_path, unresolved)

    manifest = {
        "status": "ok",
        "tiktok_result_rows_raw": len(raw_results),
        "tiktok_targets": len(results),
        "duplicate_result_rows_collapsed": len(raw_results) - len(results),
        "ocr_rows": len(ocr_rows),
        "candidate_evidence_rows": len(evidence),
        "needs_resolution": len(unresolved),
        "candidate_domains": [r.get("candidate_domain") for r in evidence],
        "evidence_output": str(evidence_path),
        "needs_resolution_output": str(unresolved_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE TIKTOK EVIDENCE BUILD OK")
    print("=" * 58)
    print(f"Run directory:              {run_dir}")
    print(f"Raw TikTok result rows:     {len(raw_results)}")
    print(f"Unique TikTok targets:      {len(results)}")
    print(f"Duplicate rows collapsed:   {len(raw_results) - len(results)}")
    print(f"OCR evidence rows:          {len(ocr_rows)}")
    print(f"Candidate evidence rows:    {len(evidence)}")
    print(f"Needs resolution:           {len(unresolved)}")
    print()
    print("CANDIDATE DOMAINS")
    if evidence:
        for row in evidence:
            print(
                f"  {row.get('candidate_domain')} | "
                f"{row.get('evidence_type')} | "
                f"observed={row.get('ocr_observed')}"
            )
    else:
        print("  [NONE]")
    print()
    print("No network validation was performed.")
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
