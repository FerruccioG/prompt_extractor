#!/usr/bin/env python3

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
REMOTE_DATA_DIR = ROOT_DIR / "data" / "remote"
STATE_PATH = REMOTE_DATA_DIR / "remote_golden_refresh_state.json"

HISTORICAL_CUTOFF_LOCAL_DATE = "2026-10-06"
INITIAL_WATERMARK_UTC = "2026-10-06T23:00:00Z"
STATE_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def default_state() -> dict:
    return {
        "state_version": STATE_VERSION,
        "historical_cutoff_local_date": HISTORICAL_CUTOFF_LOCAL_DATE,
        "initial_watermark_utc": INITIAL_WATERMARK_UTC,
        "last_successful_watermark_utc": INITIAL_WATERMARK_UTC,
        "last_successful_run_utc": None,
        "last_successful_message_uid": None,
        "successful_run_count": 0,
    }


def write_state(state: dict) -> None:
    REMOTE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    temp_path = STATE_PATH.with_suffix(".json.tmp")

    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")

    temp_path.replace(STATE_PATH)


def load_state() -> dict:
    REMOTE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not STATE_PATH.exists():
        state = default_state()
        write_state(state)
        return state

    with STATE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    state = load_state()

    print("Remote Job Opportunities Golden Master List Refresh")
    print("=" * 58)
    print(f"State file:                {STATE_PATH}")
    print(f"Historical cutoff:         {state['historical_cutoff_local_date']} (frozen)")
    print(f"Last successful watermark: {state['last_successful_watermark_utc']}")
    print(f"Last successful run:       {state['last_successful_run_utc'] or 'never'}")
    print(f"Successful run count:      {state['successful_run_count']}")
    print(f"Current UTC time:           {utc_now_iso()}")
    print()
    print("STATE PREFLIGHT OK")
    print("No email was read.")
    print("No source was processed.")
    print("Watermark was NOT advanced.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
