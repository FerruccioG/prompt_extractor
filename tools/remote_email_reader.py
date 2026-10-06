#!/usr/bin/env python3
"""
Remote Opportunities email reader.

Reuses the existing Gmail ingestion logic but forces the Gmail search query
to messages whose subject contains the word "Remote".

This keeps the original Prompt Extractor pipeline unchanged.

Usage:
    python tools/remote_email_reader.py
"""

from pathlib import Path
import os
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

# Force the Remote-opportunity query for this pipeline.
os.environ["GMAIL_QUERY"] = "subject:Remote"

from tools.ingestion.email_reader import main

if __name__ == "__main__":
    raise SystemExit(main())
