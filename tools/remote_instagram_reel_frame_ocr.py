#!/usr/bin/env python3
"""
remote_instagram_reel_frame_ocr.py

OCR deeper Instagram reel frames harvested for Remote Golden refresh.

Input:
  <run_dir>/harvest/instagram/reel_frames/<post_id>/*.png

Output:
  <run_dir>/harvest/instagram/reel_frame_ocr.jsonl

The full frame is OCR'd because remote-source names, domains, and list items may
appear anywhere on screen.

No validation, scoring, Excel publication, or watermark advancement.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
import pytesseract


FRAME_RE = re.compile(r"^(?P<post_id>.+?)_frame_(?P<frame_index>\d{2})\.png$")


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


def verify_tesseract() -> None:
    subprocess.run(
        ["tesseract", "--version"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_frame(path: Path) -> tuple[str, int]:
    m = FRAME_RE.match(path.name)
    if not m:
        return path.parent.name, 0
    return m.group("post_id"), int(m.group("frame_index"))


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir(root)
    instagram_dir = run_dir / "harvest" / "instagram"
    frames_root = instagram_dir / "reel_frames"
    output_path = instagram_dir / "reel_frame_ocr.jsonl"

    if not frames_root.exists():
        raise RuntimeError(f"Reel-frame directory not found: {frames_root}")

    verify_tesseract()

    frame_paths = sorted(frames_root.glob("*/*.png"))
    rows: list[dict] = []

    print("REMOTE INSTAGRAM DEEP REEL OCR")
    print("=" * 58)
    print(f"Run directory:             {run_dir}")
    print(f"Frame images found:        {len(frame_paths)}")
    print(f"OCR output:                {output_path}")
    print("OCR region:                full frame (100%)")
    print()

    for idx, frame_path in enumerate(frame_paths, start=1):
        post_id, frame_index = parse_frame(frame_path)
        print(f"[{idx}/{len(frame_paths)}] {frame_path.name}")

        try:
            with Image.open(frame_path) as img:
                text = pytesseract.image_to_string(img.convert("RGB"), lang="eng")

            rows.append({
                "post_id": post_id,
                "frame_index": frame_index,
                "frame_filename": frame_path.name,
                "source_image": str(frame_path.resolve()),
                "ocr_region": "full_image",
                "ocr_text_raw": text,
                "ocr_status": "ok",
                "processed_at": datetime.now(UTC).isoformat(),
            })

        except Exception as exc:
            rows.append({
                "post_id": post_id,
                "frame_index": frame_index,
                "frame_filename": frame_path.name,
                "source_image": str(frame_path.resolve()),
                "ocr_region": "full_image",
                "ocr_text_raw": "",
                "ocr_status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "processed_at": datetime.now(UTC).isoformat(),
            })
            print(f"    ERROR: {type(exc).__name__}: {exc}")

    write_jsonl(output_path, rows)

    ok_count = sum(1 for r in rows if r["ocr_status"] == "ok")
    error_count = len(rows) - ok_count

    print()
    print("REMOTE INSTAGRAM DEEP REEL OCR COMPLETE")
    print(f"OCR successful:            {ok_count}")
    print(f"OCR errors:                {error_count}")
    print("No source was validated or scored.")
    print("Excel was NOT touched.")
    print("Watermark was NOT advanced.")
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
