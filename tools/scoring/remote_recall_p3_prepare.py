#!/usr/bin/env python3
"""Prepare Recall Expansion P3 validation queues.

Reads the consolidated Recall Expansion queue, extracts exactly the P3
candidates, sorts them deterministically by recall/evidence strength, and
splits them into controlled validation batches.

Inputs:
- data/remote/recall_expansion_queue.jsonl

Outputs:
- data/remote/recall_p3_validation_queue.jsonl
- data/remote/recall_p3_validation_batch_01.jsonl
- data/remote/recall_p3_validation_batch_02.jsonl
- data/remote/recall_p3_validation_batch_03.jsonl
- data/remote/recall_p3_validation_batch_04.jsonl
- data/remote/recall_p3_prepare_summary.json

No Golden files are modified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

INPUT_PATH = ROOT_DIR / "data/remote/recall_expansion_queue.jsonl"
FULL_OUTPUT_PATH = ROOT_DIR / "data/remote/recall_p3_validation_queue.jsonl"
SUMMARY_PATH = ROOT_DIR / "data/remote/recall_p3_prepare_summary.json"

EXPECTED_P3 = 117
BATCH_SIZE = 30


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


def to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def identity(row: dict[str, Any]) -> str:
    return str(
        row.get("recall_canonical_identity")
        or row.get("canonical_host")
        or row.get("canonical_root_url")
        or ""
    ).strip().lower()


def main() -> int:
    rows = load_jsonl(INPUT_PATH)
    p3 = [row for row in rows if str(row.get("recall_priority", "")) == "P3"]

    # Strongest P3 evidence first.  Deterministic ordering allows clean
    # resumability and exact reconciliation across runs.
    p3.sort(key=lambda r: (
        -to_int(r.get("recall_score")),
        -to_int(r.get("recall_group_evidence_total")),
        -to_int(r.get("email_evidence_count")),
        -to_int(r.get("evidence_url_count")),
        identity(r),
    ))

    write_jsonl(FULL_OUTPUT_PATH, p3)

    batch_paths: list[str] = []
    batch_counts: list[int] = []
    for idx, start in enumerate(range(0, len(p3), BATCH_SIZE), start=1):
        batch = p3[start:start + BATCH_SIZE]
        path = ROOT_DIR / f"data/remote/recall_p3_validation_batch_{idx:02d}.jsonl"
        write_jsonl(path, batch)
        batch_paths.append(str(path))
        batch_counts.append(len(batch))

    summary = {
        "status": "ok",
        "recall_queue_rows": len(rows),
        "p3_rows": len(p3),
        "expected_p3_rows": EXPECTED_P3,
        "count_matches_expected": len(p3) == EXPECTED_P3,
        "batch_size": BATCH_SIZE,
        "batch_count": len(batch_counts),
        "batch_counts": batch_counts,
        "full_output": str(FULL_OUTPUT_PATH),
        "batch_outputs": batch_paths,
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if len(p3) == EXPECTED_P3 else 2


if __name__ == "__main__":
    raise SystemExit(main())
