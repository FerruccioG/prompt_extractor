#!/usr/bin/env python3
"""
remote_instagram_name_resolver.py

Resolve name-only Instagram candidate-source evidence using explicit verified
name-to-domain mappings. No guessing.

Input:
  <run>/harvest/instagram/candidate_source_evidence.jsonl

Output:
  <run>/harvest/instagram/candidate_source_evidence_v2.jsonl

No live validation, scoring, Excel publication, or watermark advancement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


VERIFIED_NAME_TO_DOMAIN = {
    "careerhound": "careerhound.io",
    "career hound": "careerhound.io",
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


def norm_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    instagram_dir = run_dir / "harvest" / "instagram"
    input_path = instagram_dir / "candidate_source_evidence.jsonl"
    output_path = instagram_dir / "candidate_source_evidence_v2.jsonl"

    if not input_path.exists():
        raise RuntimeError(f"Instagram candidate evidence not found: {input_path}")

    rows = read_jsonl(input_path)
    out: list[dict[str, Any]] = []
    resolved = 0

    for row in rows:
        if row.get("candidate_domain") or row.get("candidate_url"):
            out.append(row)
            continue

        name = (
            row.get("candidate_name")
            or row.get("source_name")
            or row.get("observed_name")
            or row.get("ocr_observed")
            or ""
        )
        domain = VERIFIED_NAME_TO_DOMAIN.get(norm_name(name))
        if not domain:
            out.append(row)
            continue

        out.append({
            **row,
            "candidate_domain": domain,
            "candidate_url": f"https://{domain}/",
            "evidence_type": "instagram_name_resolved_to_candidate_domain",
            "resolution_provenance": "explicit_verified_name_to_domain_mapping",
            "confidence": "medium",
            "status": "needs_validation",
        })
        resolved += 1

    write_jsonl(output_path, out)

    print("REMOTE INSTAGRAM NAME RESOLUTION OK")
    print("=" * 58)
    print(f"Run directory:              {run_dir}")
    print(f"Input evidence rows:        {len(rows)}")
    print(f"Name-only rows resolved:    {resolved}")
    print(f"Output evidence rows:       {len(out)}")
    print(f"Output:                     {output_path}")
    print()
    print("RESOLVED")
    if resolved:
        print("  Careerhound -> careerhound.io")
    else:
        print("  [NONE]")
    print()
    print("No live validation was performed.")
    print("No source was scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
