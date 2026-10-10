#!/usr/bin/env python3
"""
excel_source_loader.py

Non-destructive Excel loader for the Remote Opportunities pipeline.

Reads JSONL source records from data/remote/source_records.jsonl and upserts
them into the "Source Directory" worksheet of the Remote Opportunities master
workbook.

Design goals:
- Preserve the existing workbook and worksheet layout.
- Match columns by header name instead of hard-coded column positions.
- De-duplicate by durable source identity first, using URL as supporting evidence.
- Match existing rows by Source + URL, URL, then Source-only fallback.
- Update existing rows when a matching durable source already exists.
- Append new rows for genuinely new sources.
- Create a timestamped local backup before any workbook write.
- Save through a temporary file and replace the workbook only after a
  successful save.
- If no source-record input exists yet, perform a read-only workbook
  validation and exit successfully. This allows Phase 1 of the Remote
  pipeline to be deployed before the generic browser resolver is added.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from copy import copy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openpyxl import load_workbook

ROOT_DIR = Path(__file__).resolve().parents[2]

DEFAULT_WORKBOOK = Path(
    "/mnt/remote/Remote_Opportunities_Master_List_Expanded_Sources.xlsx"
)
DEFAULT_INPUT = ROOT_DIR / "data/remote/source_records.jsonl"
DEFAULT_SHEET = "Source Directory"
BACKUP_DIR = ROOT_DIR / "data/remote/backups"

# Golden relationship-intelligence columns. Existing workbook columns are
# preserved; these are appended to the right only when missing.
EXTENDED_HEADERS = [
    "Candidate ↔ Source Relationship Score",
    "Relationship Tier",
    "Candidate Fit",
    "Remote Strength",
    "Ireland/EU Accessibility",
    "Opportunity Density",
    "Specialization Fit",
    "Hidden Potential",
    "Directness",
    "Trust/Risk",
    "Earning Potential",
    "Signal Quality",
    "Freshness/Activity",
    "Generic Relationship Score",
    "Verification Status",
    "Email Evidence Count",
    "Discovery Evidence Count",
    "Direct Evidence URL Count",
    "Profile Signal Groups",
    "Representative Evidence URLs",
    "Scoring Notes",
]


def now_utc_compact() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def workbook_path() -> Path:
    configured = os.getenv("REMOTE_EXCEL_OUTPUT_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_WORKBOOK


def input_path() -> Path:
    configured = os.getenv("REMOTE_SOURCE_RECORDS_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_INPUT


def sheet_name() -> str:
    return os.getenv("REMOTE_EXCEL_SOURCE_SHEET", DEFAULT_SHEET).strip() or DEFAULT_SHEET


def clean_header(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalized_key(value: Any) -> str:
    return clean_header(value).casefold()


def normalized_host(value: Any) -> str:
    raw = clean_header(value)
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = (parsed.hostname or "").casefold().strip(".")
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    aliases = {
        "m.hays.ie": "hays.ie",
        "onlineapi-internet.hays.com": "hays.ie",
        "candidate-support.crossover.com": "crossover.com",
        "apply2.computerfutures.com": "computerfutures.com",
        "ie.gcsrecruitment.com": "gcsrecruitment.com",
        "gcstechtalent.com": "gcsrecruitment.com",
        "in.indeed.com": "indeed.com",
        "uk.indeed.com": "indeed.com",
        "in.talent.com": "talent.com",
        "ie.neuvoo.com": "talent.com",
        "jobs.zendesk.com": "zendesk.com",
        "careers.microsoft.com": "microsoft.com",
        "members.microsoft.com": "microsoft.com",
        "careers.bunq.com": "bunq.com",
        "press.bunq.com": "bunq.com",
        "careers.accelerationpartners.com": "accelerationpartners.com",
        "remotelyx-3.careers-page.com": "remotelyx.com",
        "javascript.jobs": "jsremotely.com",
        "diversityjobs.com": "latpro.com",
    }
    return aliases.get(host, host)


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Invalid JSON at {path}:{line_number}: {exc}"
                ) from exc

            if not isinstance(value, dict):
                raise RuntimeError(
                    f"Expected JSON object at {path}:{line_number}, "
                    f"got {type(value).__name__}"
                )
            records.append(value)

    return records


def find_header_row(ws, max_scan_rows: int = 15) -> int:
    required_markers = {"source", "url"}
    best_row = 0
    best_score = -1

    for row_index in range(1, min(ws.max_row, max_scan_rows) + 1):
        values = {
            normalized_key(ws.cell(row=row_index, column=col).value)
            for col in range(1, ws.max_column + 1)
        }
        score = len(required_markers.intersection(values))
        if score > best_score:
            best_row = row_index
            best_score = score
        if required_markers.issubset(values):
            return row_index

    if best_row and best_score > 0:
        return best_row

    raise RuntimeError(
        "Could not locate a header row containing Source/URL columns "
        f"in worksheet '{ws.title}'."
    )


def build_header_map(ws, header_row: int) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        header = clean_header(ws.cell(row=header_row, column=col).value)
        if header:
            mapping[header.casefold()] = col
    return mapping


def ensure_extended_headers(ws, header_row: int, header_map: dict[str, int]) -> tuple[dict[str, int], list[str]]:
    added: list[str] = []
    last_col = ws.max_column
    template = ws.cell(row=header_row, column=last_col) if last_col else None

    for header in EXTENDED_HEADERS:
        key = header.casefold()
        if key in header_map:
            continue
        last_col += 1
        cell = ws.cell(row=header_row, column=last_col, value=header)
        if template is not None and template.has_style:
            cell._style = copy(template._style)
        header_map[key] = last_col
        added.append(header)

    return header_map, added


def first_record_value(record: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).casefold(): value for key, value in record.items()}
    for name in names:
        if name.casefold() in lowered:
            return lowered[name.casefold()]
    return None


def row_identity(record: dict[str, Any]) -> tuple[str, str]:
    url = normalized_key(
        first_record_value(
            record,
            "URL",
            "Final URL",
            "Verification URL",
            "Evidence/Verification URL",
        )
    )
    source = normalized_key(first_record_value(record, "Source"))
    return url, source


def existing_index(ws, header_row: int, header_map: dict[str, int]) -> dict[str, int]:
    url_col = header_map.get("url")
    source_col = header_map.get("source")
    index: dict[str, int] = {}

    if not url_col:
        return index

    for row in range(header_row + 1, ws.max_row + 1):
        url = normalized_key(ws.cell(row=row, column=url_col).value)
        source = normalized_key(
            ws.cell(row=row, column=source_col).value if source_col else ""
        )
        if source:
            # Source Directory is a one-row-per-durable-source table. A source-
            # only index is therefore required so canonical URL cleanup does not
            # create duplicate rows.
            index.setdefault(f"source::{source}", row)
        if url:
            host = normalized_host(url)
            index[f"url::{url}"] = row
            if host:
                index.setdefault(f"host::{host}", row)
            if source:
                index[f"source_url::{source}::{url}"] = row

    return index


def match_existing_row(
    record: dict[str, Any],
    index: dict[str, int],
) -> int | None:
    url, source = row_identity(record)
    if url and source:
        row = index.get(f"source_url::{source}::{url}")
        if row:
            return row
    if url:
        row = index.get(f"url::{url}")
        if row:
            return row
        host = normalized_host(url)
        if host:
            row = index.get(f"host::{host}")
            if row:
                return row
    # Source-name fallback is safe only when the incoming record has no URL.
    # When a URL/host exists it is the durable source identity: the same brand
    # may legitimately have distinct .com/.ie/.co.uk regional sources.
    if source and not url:
        return index.get(f"source::{source}")
    return None


def copy_row_format(ws, source_row: int, target_row: int) -> None:
    """Copy visual formatting from an existing data row without copying values."""
    if source_row <= 0 or target_row <= 0 or source_row == target_row:
        return

    for col in range(1, ws.max_column + 1):
        source = ws.cell(row=source_row, column=col)
        target = ws.cell(row=target_row, column=col)
        if source.has_style:
            target._style = copy(source._style)
        if source.number_format:
            target.number_format = source.number_format

    source_dim = ws.row_dimensions[source_row]
    target_dim = ws.row_dimensions[target_row]
    if source_dim.height is not None:
        target_dim.height = source_dim.height
    target_dim.hidden = source_dim.hidden
    target_dim.outlineLevel = source_dim.outlineLevel


def write_record(
    ws,
    row: int,
    record: dict[str, Any],
    header_map: dict[str, int],
) -> int:
    written = 0
    for key, value in record.items():
        column = header_map.get(str(key).strip().casefold())
        if not column:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        ws.cell(row=row, column=column, value=value)
        written += 1
    return written


def validate_workbook(path: Path, target_sheet: str):
    if not path.exists():
        raise FileNotFoundError(f"Workbook not found: {path}")
    if not path.is_file():
        raise RuntimeError(f"Workbook path is not a file: {path}")

    wb = load_workbook(path)
    if target_sheet not in wb.sheetnames:
        available = ", ".join(wb.sheetnames)
        wb.close()
        raise RuntimeError(
            f"Worksheet '{target_sheet}' not found. Available sheets: {available}"
        )

    ws = wb[target_sheet]
    header_row = find_header_row(ws)
    header_map = build_header_map(ws, header_row)
    return wb, ws, header_row, header_map


def create_backup(path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"{path.stem}_{now_utc_compact()}{path.suffix}"
    shutil.copy2(path, backup)
    return backup


def atomic_save(wb, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.stem}.",
            suffix=destination.suffix,
            dir=str(destination.parent),
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)

        wb.save(temp_path)
        os.replace(temp_path, destination)
        temp_path = None
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def main() -> int:
    output = workbook_path()
    source_input = input_path()
    target_sheet = sheet_name()

    print(f"Workbook: {output}")
    print(f"Worksheet: {target_sheet}")
    print(f"Source records: {source_input}")
    dry_run = os.getenv("REMOTE_EXCEL_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "y"}
    extend_headers = os.getenv("REMOTE_EXCEL_EXTEND_HEADERS", "1").strip().lower() not in {"0", "false", "no", "n"}

    wb = None
    try:
        wb, ws, header_row, header_map = validate_workbook(output, target_sheet)
        print(
            f"Workbook validation OK: header row {header_row}, "
            f"{len(header_map)} mapped columns."
        )

        records = load_records(source_input)
        if not records:
            print(
                "No source records found. Read-only workbook validation completed; "
                "nothing was written."
            )
            return 0

        added_headers: list[str] = []
        if extend_headers:
            header_map, added_headers = ensure_extended_headers(ws, header_row, header_map)
            if added_headers:
                print(f"Golden columns to add: {len(added_headers)}")
                for header in added_headers:
                    print(f"  + {header}")

        index = existing_index(ws, header_row, header_map)
        appended = 0
        updated = 0
        ignored = 0
        cells_written = 0
        source_fallback_updates = 0
        host_fallback_updates = 0

        for record in records:
            url, source = row_identity(record)
            exact_source_url = bool(
                url and source and index.get(f"source_url::{source}::{url}")
            )
            exact_url = bool(url and index.get(f"url::{url}"))
            host = normalized_host(url)
            host_only = bool(host and index.get(f"host::{host}"))
            source_only = bool(source and index.get(f"source::{source}"))

            existing_row = match_existing_row(record, index)

            if existing_row is not None:
                if host_only and not exact_source_url and not exact_url:
                    host_fallback_updates += 1
                elif source_only and not exact_source_url and not exact_url and not host_only:
                    source_fallback_updates += 1
                written = write_record(ws, existing_row, record, header_map)
                cells_written += written
                if written:
                    updated += 1
                else:
                    ignored += 1
                continue

            new_row = ws.max_row + 1
            # Preserve the established Source Directory row appearance for every
            # newly appended Golden relationship. This removes the need for
            # manual Format Painter fixes after an incremental refresh.
            template_row = new_row - 1 if new_row - 1 > header_row else header_row + 1
            if template_row < new_row and template_row <= ws.max_row:
                copy_row_format(ws, template_row, new_row)
            written = write_record(ws, new_row, record, header_map)
            cells_written += written

            if not written:
                ignored += 1
                continue

            appended += 1
            if source:
                index.setdefault(f"source::{source}", new_row)
            if url:
                index[f"url::{url}"] = new_row
                host = normalized_host(url)
                if host:
                    index.setdefault(f"host::{host}", new_row)
                if source:
                    index[f"source_url::{source}::{url}"] = new_row

        changes_required = appended > 0 or updated > 0 or bool(added_headers)

        if dry_run:
            print(
                "DRY RUN complete: "
                f"records={len(records)}, would_append={appended}, "
                f"would_update={updated}, ignored={ignored}, "
                f"cells_written={cells_written}, "
                f"would_add_columns={len(added_headers)}, "
                f"host_fallback_updates={host_fallback_updates}, "
                f"source_fallback_updates={source_fallback_updates}"
            )
            print("Workbook was NOT saved.")
            return 0

        if not changes_required:
            print(
                f"No workbook changes required. ignored={ignored}, "
                f"records={len(records)}"
            )
            return 0

        backup = create_backup(output)
        atomic_save(wb, output)

        print(
            "Excel load complete: "
            f"records={len(records)}, appended={appended}, updated={updated}, "
            f"ignored={ignored}, cells_written={cells_written}, "
            f"columns_added={len(added_headers)}, "
            f"host_fallback_updates={host_fallback_updates}, "
            f"source_fallback_updates={source_fallback_updates}"
        )
        print(f"Backup: {backup}")
        return 0

    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if wb is not None:
            wb.close()


if __name__ == "__main__":
    raise SystemExit(main())
