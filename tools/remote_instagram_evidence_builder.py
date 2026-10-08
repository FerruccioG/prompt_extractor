#!/usr/bin/env python3
"""
remote_instagram_evidence_builder.py

Build structured candidate-source evidence from run-scoped Instagram OCR and
harvest metadata.

This stage is deliberately conservative:
- explicit domains/URLs become candidate evidence
- a small set of clearly named remote-job platforms can become named candidates
- OCR corrections are NEVER silently treated as verified facts
- reels whose single screenshot does not expose the promised source/list are
  routed to needs_deeper_harvest

No network validation, scoring, Excel publication, or watermark advancement.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse


DOMAIN_RE = re.compile(
    r"\b(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?",
    re.IGNORECASE,
)

KNOWN_NAMED_SOURCES = {
    "wellfound": "https://wellfound.com/",
    "we work remotely": "https://weworkremotely.com/",
    "remoteok": "https://remoteok.com/",
    "remote ok": "https://remoteok.com/",
    "himalayas": "https://himalayas.app/",
}

# OCR variants that are plausible but MUST be verified before canonical use.
OCR_CORRECTION_CANDIDATES = {
    "sillhiring.today": "stillhiring.today",
}

GENERIC_HOSTS = {
    "instagram.com",
    "www.instagram.com",
}


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
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


def canonical_from_domain(domain: str) -> str:
    return f"https://{domain}/"


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    instagram_dir = run_dir / "harvest" / "instagram"
    ocr_path = instagram_dir / "ocr_raw.jsonl"
    results_path = instagram_dir / "results.jsonl"

    if not ocr_path.exists():
        raise RuntimeError(f"OCR file not found: {ocr_path}")
    if not results_path.exists():
        raise RuntimeError(f"Instagram results not found: {results_path}")

    ocr_rows = [r for r in read_jsonl(ocr_path) if not r.get("event")]
    result_rows = read_jsonl(results_path)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in ocr_rows:
        grouped[row.get("post_id", "")].append(row)

    result_by_id = {}
    for row in result_rows:
        url = row.get("normalized_url") or ""
        post_id = url.rstrip("/").split("/")[-1]
        result_by_id[post_id] = row

    evidence_rows: list[dict] = []
    deeper_rows: list[dict] = []
    seen_candidates: set[tuple[str, str]] = set()

    for post_id, rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda r: r.get("slide_index", 0))
        combined_text = "\n".join((r.get("ocr_text_raw") or "") for r in rows)
        lower_text = combined_text.lower()
        result = result_by_id.get(post_id, {})
        source_url = result.get("normalized_url")
        content_type = result.get("content_type")

        explicit_found = False

        for match in DOMAIN_RE.finditer(combined_text):
            raw_domain = normalized_domain(match.group(1))
            if not raw_domain or raw_domain in GENERIC_HOSTS:
                continue

            corrected = OCR_CORRECTION_CANDIDATES.get(raw_domain)
            if corrected:
                key = (post_id, corrected)
                if key not in seen_candidates:
                    seen_candidates.add(key)
                    evidence_rows.append({
                        "post_id": post_id,
                        "instagram_url": source_url,
                        "content_type": content_type,
                        "evidence_type": "ocr_domain_needs_verification",
                        "ocr_observed": raw_domain,
                        "candidate_domain": corrected,
                        "candidate_url": canonical_from_domain(corrected),
                        "confidence": "low",
                        "status": "needs_validation",
                        "note": "Candidate involves an OCR correction; do not treat as verified until resolved externally.",
                    })
                explicit_found = True
                continue

            key = (post_id, raw_domain)
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            evidence_rows.append({
                "post_id": post_id,
                "instagram_url": source_url,
                "content_type": content_type,
                "evidence_type": "explicit_ocr_domain",
                "ocr_observed": raw_domain,
                "candidate_domain": raw_domain,
                "candidate_url": canonical_from_domain(raw_domain),
                "confidence": "medium",
                "status": "needs_validation",
            })
            explicit_found = True

        for label, canonical_url in KNOWN_NAMED_SOURCES.items():
            if label not in lower_text:
                continue
            domain = (urlparse(canonical_url).hostname or "").removeprefix("www.")
            key = (post_id, domain)
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            evidence_rows.append({
                "post_id": post_id,
                "instagram_url": source_url,
                "content_type": content_type,
                "evidence_type": "named_remote_source",
                "ocr_observed": label,
                "candidate_domain": domain,
                "candidate_url": canonical_url,
                "confidence": "medium",
                "status": "needs_validation",
            })
            explicit_found = True

        # A reel can advertise a list/resource while the single screenshot does
        # not expose the actual names. Those need richer video/frame extraction.
        list_signal = any(
            phrase in lower_text
            for phrase in (
                "remote jobs hiring",
                "remote job sites",
                "ranking job portals",
                "best resource",
            )
        )
        if content_type == "reel" and list_signal and not explicit_found:
            deeper_rows.append({
                "post_id": post_id,
                "instagram_url": source_url,
                "content_type": content_type,
                "reason": "single_reel_screenshot_did_not_expose_candidate_sources",
                "status": "needs_deeper_harvest",
            })

    evidence_path = instagram_dir / "candidate_source_evidence.jsonl"
    deeper_path = instagram_dir / "needs_deeper_harvest.jsonl"

    write_jsonl(evidence_path, evidence_rows)
    write_jsonl(deeper_path, deeper_rows)

    print("REMOTE INSTAGRAM EVIDENCE BUILD OK")
    print(f"Run directory:              {run_dir}")
    print(f"Candidate evidence rows:    {len(evidence_rows)}")
    print(f"Needs deeper harvest:       {len(deeper_rows)}")
    print(f"Evidence output:            {evidence_path}")
    print(f"Deeper-harvest output:      {deeper_path}")
    print()
    print("No network validation was performed.")
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
