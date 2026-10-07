#!/usr/bin/env python3
"""Audit Recall P1 promotion candidates before writing to the Golden workbook.

This stage is intentionally stricter than the comparison script.  The
comparison script answers "which new sources scored into A/B/C?"  This audit
answers "which of those have enough evidence to promote NOW?"

Inputs:
- data/remote/recall_p1_promotion_candidates.jsonl
- data/remote/source_records.jsonl

Outputs:
- data/remote/recall_p1_promote_now.jsonl
- data/remote/recall_p1_targeted_verification.jsonl
- data/remote/recall_p1_hold.jsonl
- data/remote/recall_p1_promotion_audit_summary.json

No Golden files are modified.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = Path(os.getenv(
    "REMOTE_RECALL_PROMOTION_INPUT",
    ROOT_DIR / "data/remote/recall_p1_promotion_candidates.jsonl",
))
BASELINE_PATH = Path(os.getenv(
    "REMOTE_RECALL_BASELINE_RECORDS",
    ROOT_DIR / "data/remote/source_records.jsonl",
))

PROMOTE_PATH = Path(os.getenv(
    "REMOTE_RECALL_PROMOTE_NOW_OUTPUT",
    ROOT_DIR / "data/remote/recall_p1_promote_now.jsonl",
))
VERIFY_PATH = Path(os.getenv(
    "REMOTE_RECALL_TARGETED_VERIFICATION_OUTPUT",
    ROOT_DIR / "data/remote/recall_p1_targeted_verification.jsonl",
))
HOLD_PATH = Path(os.getenv(
    "REMOTE_RECALL_HOLD_OUTPUT",
    ROOT_DIR / "data/remote/recall_p1_hold.jsonl",
))
SUMMARY_PATH = Path(os.getenv(
    "REMOTE_RECALL_PROMOTION_AUDIT_SUMMARY",
    ROOT_DIR / "data/remote/recall_p1_promotion_audit_summary.json",
))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def num(row: dict[str, Any], key: str) -> int:
    return int(row.get(key, 0) or 0)


def classify(row: dict[str, Any]) -> tuple[str, list[str]]:
    source = str(row.get("Source", "")).lower()
    tier = str(row.get("Relationship Tier", ""))
    score = num(row, "Candidate ↔ Source Relationship Score")
    fit = num(row, "Candidate Fit")
    remote = num(row, "Remote Strength")
    geo = num(row, "Ireland/EU Accessibility")
    density = num(row, "Opportunity Density")
    signal = num(row, "Signal Quality")

    reasons: list[str] = []

    # Strong A-tier sources can be promoted immediately when their core
    # candidate, remote, geography, density and signal dimensions all clear
    # conservative evidence floors.  This avoids requiring the much stronger
    # 70/60 remote/geography pair when targeted verification has already
    # produced solid evidence (for example Built In).
    if (
        tier == "A"
        and score >= 75
        and fit >= 50
        and remote >= 50
        and geo >= 30
        and density >= 60
        and signal >= 40
    ):
        reasons.append("A-tier with strong core evidence across fit, remote, geography, density, and signal quality")
        return "promote_now", reasons

    # B/C sources can still promote now if all core dimensions clear a more
    # conservative evidence floor.
    if (
        tier in {"B", "C"}
        and score >= 50
        and fit >= 40
        and remote >= 50
        and geo >= 30
        and density >= 60
        and signal >= 40
    ):
        reasons.append("Core evidence floors cleared despite lower overall tier")
        return "promote_now", reasons

    # Evidence gaps that are plausibly crawler/extraction related should trigger
    # targeted verification instead of rejection.
    if remote < 30:
        reasons.append("Remote-strength evidence below 30")
    if geo < 30:
        reasons.append("Ireland/EU accessibility evidence below 30")
    if signal < 40:
        reasons.append("Signal quality below 40")
    if fit < 40:
        reasons.append("Candidate-fit evidence below 40")

    if source in {"linkedin.com", "builtin.com", "dice.com", "ycombinator.com", "openjobseu.com", "malt.com", "a.team", "catalant.com"}:
        reasons.append("Known/strategic source warrants targeted verification before Golden write")
        return "targeted_verification", reasons

    # Anything else remains preserved but does not advance now.
    if not reasons:
        reasons.append("Did not clear immediate-promotion evidence gate")
    return "hold", reasons


def main() -> int:
    candidates = load_jsonl(INPUT_PATH)
    baseline = load_jsonl(BASELINE_PATH)
    baseline_sources = {str(r.get("Source", "")).lower() for r in baseline}

    promote: list[dict[str, Any]] = []
    verify: list[dict[str, Any]] = []
    hold: list[dict[str, Any]] = []

    for row in candidates:
        source = str(row.get("Source", "")).lower()
        out = dict(row)

        if source in baseline_sources:
            out["Promotion Audit Decision"] = "hold"
            out["Promotion Audit Reasons"] = ["Already present in Golden baseline"]
            hold.append(out)
            continue

        decision, reasons = classify(row)
        out["Promotion Audit Decision"] = decision
        out["Promotion Audit Reasons"] = reasons

        if decision == "promote_now":
            promote.append(out)
        elif decision == "targeted_verification":
            verify.append(out)
        else:
            hold.append(out)

    def sort_key(r: dict[str, Any]) -> tuple[int, str]:
        return (
            -num(r, "Candidate ↔ Source Relationship Score"),
            str(r.get("Source", "")),
        )

    promote.sort(key=sort_key)
    verify.sort(key=sort_key)
    hold.sort(key=sort_key)

    write_jsonl(PROMOTE_PATH, promote)
    write_jsonl(VERIFY_PATH, verify)
    write_jsonl(HOLD_PATH, hold)

    summary = {
        "status": "ok",
        "input_candidates": len(candidates),
        "promote_now": len(promote),
        "targeted_verification": len(verify),
        "hold": len(hold),
        "accounted": len(promote) + len(verify) + len(hold),
        "promote_now_sources": [
            {
                "source": r.get("Source"),
                "score": num(r, "Candidate ↔ Source Relationship Score"),
                "tier": r.get("Relationship Tier"),
            }
            for r in promote
        ],
        "targeted_verification_sources": [
            {
                "source": r.get("Source"),
                "score": num(r, "Candidate ↔ Source Relationship Score"),
                "tier": r.get("Relationship Tier"),
                "reasons": r.get("Promotion Audit Reasons"),
            }
            for r in verify
        ],
        "hold_sources": [
            {
                "source": r.get("Source"),
                "score": num(r, "Candidate ↔ Source Relationship Score"),
                "tier": r.get("Relationship Tier"),
            }
            for r in hold
        ],
        "promote_output": str(PROMOTE_PATH),
        "targeted_verification_output": str(VERIFY_PATH),
        "hold_output": str(HOLD_PATH),
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
