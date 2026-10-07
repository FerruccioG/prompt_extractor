#!/usr/bin/env python3
"""Prepare Recall Expansion P2 validation queue.

Reads the consolidated Recall Expansion queue and extracts exactly the P2
candidates into a dedicated file so the live validator can process them
independently from P1/P3.

Inputs:
- data/remote/recall_expansion_queue.jsonl

Outputs:
- data/remote/recall_p2_validation_queue.jsonl
- data/remote/recall_p2_prepare_summary.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = ROOT_DIR / "data/remote/recall_expansion_queue.jsonl"
OUTPUT_PATH = ROOT_DIR / "data/remote/recall_p2_validation_queue.jsonl"
SUMMARY_PATH = ROOT_DIR / "data/remote/recall_p2_prepare_summary.json"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def main() -> int:
    rows = load_jsonl(INPUT_PATH)
    p2 = [row for row in rows if str(row.get("recall_priority", "")) == "P2"]

    p2.sort(key=lambda r: (
        -int(r.get("recall_score", 0) or 0),
        -int(r.get("recall_group_evidence_total", 0) or 0),
        str(r.get("recall_canonical_identity", "")),
    ))

    write_jsonl(OUTPUT_PATH, p2)

    summary = {
        "status": "ok",
        "recall_queue_rows": len(rows),
        "p2_rows": len(p2),
        "expected_p2_rows": 43,
        "count_matches_expected": len(p2) == 43,
        "output": str(OUTPUT_PATH),
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if len(p2) == 43 else 2


if __name__ == "__main__":
    raise SystemExit(main())
