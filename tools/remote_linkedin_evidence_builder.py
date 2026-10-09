#!/usr/bin/env python3
"""
remote_linkedin_evidence_builder.py

Build structured candidate-source evidence from LinkedIn harvest text, OCR, and
the original Remote email provenance.

Inputs:
  <run>/harvest/linkedin/results.jsonl
  <run>/harvest/linkedin/ocr_raw.jsonl
  <run>/email_audit.jsonl

Outputs:
  <run>/harvest/linkedin/candidate_source_evidence.jsonl
  <run>/harvest/linkedin/needs_resolution.jsonl
  <run>/harvest/linkedin/evidence_manifest.json

Principles:
- LinkedIn URLs are provenance/evidence, not automatically the final source.
- Explicit non-LinkedIn domains become candidate-domain evidence.
- For LinkedIn job pages, a company/employer name inferred from the page title
  is preserved as a name-resolution candidate when no canonical domain is known.
- Access-limited posts/groups are preserved as unresolved with their original
  email evidence; access failure is NOT exclusion.
- Email body text is used as provenance/context, not mined indiscriminately for
  domains because LinkedIn notification emails contain many tracking/template
  URLs.

No network resolution/validation, scoring, Excel publication, or watermark
advancement occurs here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


DOMAIN_RE = re.compile(
    r"\b(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})"
    r"(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?",
    re.IGNORECASE,
)

GENERIC_OR_INFRA_HOSTS = {
    "linkedin.com",
    "www.linkedin.com",
    "licdn.com",
    "media.licdn.com",
    "static.licdn.com",
    "example.com",
    "example.org",
    "example.net",
    "w3.org",
}

PLAUSIBLE_TLDS = {
    "ai", "app", "co", "com", "dev", "eu", "ie", "io", "jobs",
    "net", "org", "tech", "today", "uk", "work",
}

JOB_TITLE_PATTERNS = (
    # Typical public LinkedIn job page title:
    # "Acme hiring Senior Data Engineer in Dublin, County Dublin, Ireland | LinkedIn"
    re.compile(r"^\s*(?P<company>.+?)\s+hiring\s+.+?\s+in\s+.+?\s*\|\s*LinkedIn\s*$", re.I),
    # Alternate title form occasionally returned by LinkedIn.
    re.compile(r"^\s*.+?\s+-\s+(?P<company>[^|]+?)\s*\|\s*LinkedIn\s*$", re.I),
)


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


def slug_for(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    tail = re.sub(r"[^A-Za-z0-9_-]+", "_", url.rstrip("/").split("/")[-1])[:48]
    return f"{tail or 'linkedin'}_{digest}"


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


def infer_company_from_job_title(title: str) -> str:
    title = " ".join((title or "").split())
    if not title:
        return ""
    for pattern in JOB_TITLE_PATTERNS:
        match = pattern.match(title)
        if match:
            company = " ".join(match.group("company").split()).strip(" -|")
            if company and company.lower() not in {"linkedin", "jobs"}:
                return company
    return ""


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    linkedin_dir = run_dir / "harvest" / "linkedin"

    results_path = linkedin_dir / "results.jsonl"
    ocr_path = linkedin_dir / "ocr_raw.jsonl"
    email_audit_path = run_dir / "email_audit.jsonl"

    if not results_path.exists():
        raise RuntimeError(f"LinkedIn harvest results not found: {results_path}")
    if not ocr_path.exists():
        raise RuntimeError(f"LinkedIn OCR not found: {ocr_path}")
    if not email_audit_path.exists():
        raise RuntimeError(f"Email audit not found: {email_audit_path}")

    raw_results = read_jsonl(results_path)
    ocr_rows = [r for r in read_jsonl(ocr_path) if not r.get("event")]
    email_rows = read_jsonl(email_audit_path)

    # Results are append-only; keep the latest row for each requested target.
    results_by_key: dict[str, dict[str, Any]] = {}
    for row in raw_results:
        key = str(
            row.get("requested_url")
            or row.get("canonical_url")
            or row.get("normalized_url")
            or row.get("raw_url")
            or ""
        ).strip()
        if key:
            results_by_key[key] = row
    results = list(results_by_key.values())

    email_by_uid = {
        str(r.get("message_uid")): r
        for r in email_rows
        if r.get("message_uid") is not None
    }

    # OCR extractor records each screenshot stem as post_id.
    ocr_by_post_id: dict[str, list[dict[str, Any]]] = {}
    for row in ocr_rows:
        ocr_by_post_id.setdefault(str(row.get("post_id") or ""), []).append(row)

    evidence: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen_domains: set[tuple[str, str]] = set()
    seen_names: set[tuple[str, str]] = set()

    for result in sorted(results, key=lambda r: str(r.get("requested_url") or "")):
        requested_url = str(result.get("requested_url") or "")
        final_url = str(result.get("final_url") or "")
        content_kind = str(result.get("content_kind") or "linkedin")
        status = str(result.get("status") or "")
        source_uid = str(result.get("source_message_uid") or "")
        source_subject = str(result.get("source_subject") or "")

        slug = slug_for(requested_url)
        target_ocr_rows = ocr_by_post_id.get(slug, [])
        ocr_text = "\n".join(str(r.get("ocr_text_raw") or "") for r in target_ocr_rows)

        email_row = email_by_uid.get(source_uid, {})
        email_text = str(email_row.get("email_text") or "")

        page_title = str(result.get("page_title") or "")
        page_text = str(result.get("visible_text") or "")

        page_and_ocr_text = "\n".join([page_title, page_text, ocr_text])

        target_evidence_count = 0

        # Explicit candidate domains are taken only from rendered LinkedIn
        # evidence, not from raw notification-email URLs.
        for match in DOMAIN_RE.finditer(page_and_ocr_text):
            domain = normalized_domain(match.group(1))
            if (
                not domain
                or domain in GENERIC_OR_INFRA_HOSTS
                or domain.endswith(".linkedin.com")
                or domain.endswith(".licdn.com")
                or not plausible_domain(domain)
            ):
                continue

            key = (requested_url, domain)
            if key in seen_domains:
                continue
            seen_domains.add(key)

            evidence.append({
                "linkedin_url": requested_url,
                "final_linkedin_url": final_url,
                "content_kind": content_kind,
                "source_message_uid": result.get("source_message_uid"),
                "source_subject": source_subject,
                "evidence_type": "explicit_domain_in_linkedin_page_or_ocr",
                "evidence_provenance": "linkedin_page_or_screenshot_ocr",
                "ocr_observed": match.group(0),
                "candidate_name": None,
                "candidate_domain": domain,
                "candidate_url": canonical_url(domain),
                "confidence": "medium",
                "status": "needs_validation",
            })
            target_evidence_count += 1

        # A public LinkedIn job title usually exposes the employer reliably,
        # even when the employer's own canonical domain is not present.
        if content_kind == "job":
            company = infer_company_from_job_title(page_title)
            if company:
                key = (requested_url, company.lower())
                if key not in seen_names:
                    seen_names.add(key)
                    evidence.append({
                        "linkedin_url": requested_url,
                        "final_linkedin_url": final_url,
                        "content_kind": content_kind,
                        "source_message_uid": result.get("source_message_uid"),
                        "source_subject": source_subject,
                        "evidence_type": "linkedin_job_employer_name_needs_resolution",
                        "evidence_provenance": "linkedin_page_title",
                        "ocr_observed": page_title,
                        "candidate_name": company,
                        "candidate_domain": None,
                        "candidate_url": None,
                        "confidence": "medium",
                        "status": "needs_resolution",
                    })
                    target_evidence_count += 1

        # Preserve every target that did not yield a canonical candidate domain,
        # especially access-limited posts/groups. Original email text is
        # retained as context for the later name/domain resolver.
        has_domain = any(
            r.get("linkedin_url") == requested_url and r.get("candidate_domain")
            for r in evidence
        )
        if not has_domain:
            unresolved.append({
                "linkedin_url": requested_url,
                "final_linkedin_url": final_url,
                "content_kind": content_kind,
                "harvest_status": status,
                "access_reason": result.get("access_reason"),
                "source_message_uid": result.get("source_message_uid"),
                "source_subject": source_subject,
                "page_title": page_title,
                "candidate_names_observed": [
                    r.get("candidate_name")
                    for r in evidence
                    if r.get("linkedin_url") == requested_url and r.get("candidate_name")
                ],
                "email_text_excerpt": email_text[:4000],
                "page_text_excerpt": page_text[:4000],
                "ocr_text_excerpt": ocr_text[:4000],
                "status": "needs_resolution",
                "reason": (
                    "linkedin_access_limited_preserve_for_resolution"
                    if status == "access_limited"
                    else "no_canonical_candidate_domain_extracted"
                ),
            })

    evidence_path = linkedin_dir / "candidate_source_evidence.jsonl"
    unresolved_path = linkedin_dir / "needs_resolution.jsonl"
    manifest_path = linkedin_dir / "evidence_manifest.json"

    write_jsonl(evidence_path, evidence)
    write_jsonl(unresolved_path, unresolved)

    domain_rows = [r for r in evidence if r.get("candidate_domain")]
    name_rows = [r for r in evidence if r.get("candidate_name") and not r.get("candidate_domain")]

    manifest = {
        "status": "ok",
        "raw_linkedin_result_rows": len(raw_results),
        "unique_linkedin_targets": len(results),
        "duplicate_result_rows_collapsed": len(raw_results) - len(results),
        "ocr_rows": len(ocr_rows),
        "candidate_evidence_rows": len(evidence),
        "resolved_candidate_domain_rows": len(domain_rows),
        "candidate_name_resolution_rows": len(name_rows),
        "targets_needing_resolution": len(unresolved),
        "candidate_domains": sorted({r["candidate_domain"] for r in domain_rows}),
        "candidate_names": sorted({r["candidate_name"] for r in name_rows}),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("REMOTE LINKEDIN EVIDENCE BUILD OK")
    print("=" * 58)
    print(f"Run directory:                  {run_dir}")
    print(f"Raw LinkedIn result rows:       {len(raw_results)}")
    print(f"Unique LinkedIn targets:        {len(results)}")
    print(f"Duplicate rows collapsed:       {len(raw_results) - len(results)}")
    print(f"OCR evidence rows:              {len(ocr_rows)}")
    print(f"Candidate evidence rows:        {len(evidence)}")
    print(f"Resolved candidate domains:     {len(domain_rows)}")
    print(f"Candidate names for resolution: {len(name_rows)}")
    print(f"Targets needing resolution:     {len(unresolved)}")
    print()
    print("CANDIDATE DOMAINS")
    if domain_rows:
        for domain in sorted({r["candidate_domain"] for r in domain_rows}):
            print(f"  {domain}")
    else:
        print("  [NONE]")
    print()
    print("CANDIDATE NAMES NEEDING DOMAIN RESOLUTION")
    if name_rows:
        for name in sorted({r["candidate_name"] for r in name_rows}):
            print(f"  {name}")
    else:
        print("  [NONE]")
    print()
    print("No network resolution/validation was performed.")
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
