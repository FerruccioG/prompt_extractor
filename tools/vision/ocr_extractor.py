#!/usr/bin/env python3
"""
ocr_extractor.py

Reads Instagram screenshot outputs and performs OCR on the TOP region only.
This tool is intentionally "dumb":
- extract all visible text
- no cleaning
- no prompt selection
- no interpretation

Input:
- data/instagram/screenshots/

Output:
- data/instagram/ocr_raw.jsonl

Supported screenshot structures:
1. Reel/single-image screenshots:
   data/instagram/screenshots/C7DHtAFuPtB.png

2. Post/carousel screenshots:
   data/instagram/screenshots/C8aZcFxiv_k/C8aZcFxiv_k_slide_01_of_08.png
   data/instagram/screenshots/C8aZcFxiv_k/C8aZcFxiv_k_slide_02_of_08.png
   ...

Requirements:
- Tesseract installed on system
- Python packages:
    pip install pillow pytesseract
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from PIL import Image
import pytesseract


BASE_PATH = Path(__file__).resolve().parent.parent
SCREENSHOTS_DIR = BASE_PATH / "data/instagram/screenshots"
OUTPUT_JSONL = BASE_PATH / "data/instagram/ocr_raw.jsonl"

# OCR only the top region of the image.
TOP_REGION_RATIO = 0.55

# Optional throttle for testing.
# Set to None to process all screenshots.
MAX_IMAGES = None


SINGLE_REEL_RE = re.compile(
    r"^(?P<post_id>[^/\\]+)\.png$"
)

CAROUSEL_SLIDE_RE = re.compile(
    r"^(?P<post_id>.+?)_slide_(?P<slide_index>\d{2})_of_(?P<slide_total>\d{2})\.png$"
)


def verify_tesseract_available() -> None:
    try:
        subprocess.run(
            ["tesseract", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        raise RuntimeError(
            "Tesseract is not available. Install it first with:\n"
            "sudo apt update && sudo apt install -y tesseract-ocr"
        ) from exc


def ensure_output_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_start_marker(path: Path) -> None:
    append_jsonl(
        path,
        {
            "event": "ocr_extractor_started",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


def load_processed_images(path: Path) -> set[str]:
    if not path.exists():
        return set()

    processed: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                source_image = row.get("source_image")
                if source_image:
                    processed.add(source_image)
            except Exception:
                continue
    return processed


def iter_screenshot_files(root: Path) -> Iterator[Path]:
    # reel/single png at root
    for p in sorted(root.glob("*.png")):
        if p.is_file():
            yield p

    # carousel slide pngs inside post folders
    for d in sorted(root.iterdir()):
        if d.is_dir():
            for p in sorted(d.glob("*.png")):
                if p.is_file():
                    yield p


def parse_slide_metadata(image_path: Path) -> dict:
    """
    Supports both:
    - C7DHtAFuPtB.png
    - C8aZcFxiv_k_slide_01_of_08.png
    """
    name = image_path.name

    m_carousel = CAROUSEL_SLIDE_RE.match(name)
    if m_carousel:
        return {
            "post_id": m_carousel.group("post_id"),
            "slide_filename": name,
            "slide_index": int(m_carousel.group("slide_index")),
            "slide_total": int(m_carousel.group("slide_total")),
            "content_type": "post",
        }

    m_single = SINGLE_REEL_RE.match(name)
    if m_single:
        return {
            "post_id": m_single.group("post_id"),
            "slide_filename": name,
            "slide_index": 1,
            "slide_total": 1,
            "content_type": "reel_or_single",
        }

    # fallback
    stem = image_path.stem
    return {
        "post_id": stem,
        "slide_filename": name,
        "slide_index": 1,
        "slide_total": 1,
        "content_type": "unknown",
    }


def crop_top_region(img: Image.Image, ratio: float) -> Image.Image:
    width, height = img.size
    crop_height = int(height * ratio)
    return img.crop((0, 0, width, crop_height))


def run_ocr(image: Image.Image) -> str:
    """
    Extract raw text exactly as Tesseract sees it.
    No cleaning here by design.
    """
    text = pytesseract.image_to_string(image, lang="eng")
    return text


def main() -> int:
    verify_tesseract_available()

    ensure_output_file(OUTPUT_JSONL)
    write_start_marker(OUTPUT_JSONL)

    if not SCREENSHOTS_DIR.exists():
        print(f"Input screenshots directory not found: {SCREENSHOTS_DIR}")
        return 1

    processed_images = load_processed_images(OUTPUT_JSONL)

    screenshot_files = list(iter_screenshot_files(SCREENSHOTS_DIR))

    print(f"Total screenshot files found: {len(screenshot_files)}")
    print(f"Already OCR-processed: {len(processed_images)}")
    print(f"MAX_IMAGES: {MAX_IMAGES}")

    processed_count = 0

    for image_path in screenshot_files:
        image_path_str = str(image_path.resolve())

        if image_path_str in processed_images:
            continue

        if MAX_IMAGES is not None and processed_count >= MAX_IMAGES:
            print(f"Reached MAX_IMAGES limit ({MAX_IMAGES}). Stopping early.")
            break

        metadata = parse_slide_metadata(image_path)
        print(f"OCR processing: {image_path.name}")

        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                cropped = crop_top_region(img, TOP_REGION_RATIO)
                ocr_text_raw = run_ocr(cropped)

            row = {
                "post_id": metadata["post_id"],
                "content_type": metadata["content_type"],
                "slide_filename": metadata["slide_filename"],
                "slide_index": metadata["slide_index"],
                "slide_total": metadata["slide_total"],
                "source_image": image_path_str,
                "ocr_region": f"top_{int(TOP_REGION_RATIO * 100)}_percent",
                "ocr_text_raw": ocr_text_raw,
                "ocr_status": "ok",
                "processed_at": datetime.now(UTC).isoformat(),
            }

            append_jsonl(OUTPUT_JSONL, row)
            processed_count += 1

        except Exception as exc:
            row = {
                "post_id": metadata["post_id"],
                "content_type": metadata["content_type"],
                "slide_filename": metadata["slide_filename"],
                "slide_index": metadata["slide_index"],
                "slide_total": metadata["slide_total"],
                "source_image": image_path_str,
                "ocr_region": f"top_{int(TOP_REGION_RATIO * 100)}_percent",
                "ocr_text_raw": "",
                "ocr_status": "error",
                "error": str(exc),
                "processed_at": datetime.now(UTC).isoformat(),
            }

            append_jsonl(OUTPUT_JSONL, row)
            processed_count += 1
            print(f"Error: {exc}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
