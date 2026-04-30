# 🛠️ Tools Layer — Prompt Extractor Execution Backbone

## 📌 Purpose

The `tools/` directory contains the executable building blocks of the **Prompt Extractor** pipeline.

This layer transforms raw discovery signals — URLs extracted from email — into structured prompt data that can later be processed, enriched, and loaded into MongoDB.

The design follows a simple production principle:

> **Each tool owns one stage, one responsibility, and one output contract.**

---

## 🧭 Architecture Overview

The tools layer is organized into functional domains:

```text
tools/
├── ingestion/       # Email ingestion and URL discovery
├── normalization/   # URL cleanup, deduplication, filtering
├── splitting/       # Platform-specific queue generation
├── scraping/        # Browser automation and screenshot capture
├── vision/          # OCR extraction from screenshots
├── processing/      # Text grouping and cleanup preparation
├── semantic/        # AI-assisted semantic extraction
└── storage/         # MongoDB loading
```

The pipeline runner orchestrates these tools in sequence.

```text
Email → URLs → Normalized Queue → Instagram Queue → Screenshots → OCR → Grouped Text → AI-ready Text → Mongo-ready Data
```

---

## 🔒 Execution Contract

The production pipeline runner calls stable wrapper entrypoints from the root of `tools/`.

Example:

```text
tools/email_reader.py
tools/url_normalizer.py
tools/url_filter.py
tools/platform_splitter.py
tools/scraper_instagram.py
tools/ocr_extractor.py
tools/text_group_builder.py
tools/text_manipulator_prep.py
```

These wrapper files exist to preserve compatibility with the frozen pipeline runner.

The actual implementation logic lives in the structured subfolders.

```text
Pipeline Runner → tools/*.py wrappers → tools/<domain>/<module>.py implementation
```

This separation allows the internal implementation to evolve without breaking the orchestration contract.

---

## ⚠️ Critical Stability Rules

Do **not** casually rename, move, or delete the wrapper files in `tools/*.py`.

Do **not** flatten the subfolder implementation structure.

Do **not** change JSONL field names without validating downstream stages.

Do **not** change the pipeline stage order without a full end-to-end test.

The safest rule is:

> **Wrappers are stable. Subfolder implementations are evolvable.**

---

## 🔧 Tool Domains

### 📥 1. Ingestion

```text
tools/ingestion/email_reader.py
```

Responsibilities:

- Connects to the configured email account
- Searches messages using `GMAIL_QUERY`
- Extracts social/media URLs
- Writes audit and queue files

Primary outputs:

```text
data/email_url_audit.jsonl
data/url_queue.jsonl
```

This stage is the source of truth for URL discovery.

---

### 🧹 2. Normalization

```text
tools/normalization/url_normalizer.py
tools/normalization/url_filter.py
```

Responsibilities:

- Normalize URL formats
- Remove duplicate or malformed URLs
- Separate scrape-ready URLs from rejected URLs

Primary outputs:

```text
data/url_queue_normalized.jsonl
data/url_queue_scrape_ready.jsonl
data/url_queue_rejected.jsonl
```

This stage turns noisy raw links into a controlled queue.

---

### 🔀 3. Platform Splitting

```text
tools/splitting/platform_splitter.py
```

Responsibilities:

- Classify URLs by platform
- Create platform-specific queues

Primary outputs:

```text
data/queues/instagram.jsonl
data/queues/facebook.jsonl
data/queues/linkedin.jsonl
data/queues/youtube.jsonl
```

Instagram is currently the main implemented scraping target.

---

### 🌐 4. Scraping

```text
tools/scraping/scraper_instagram.py
```

Responsibilities:

- Uses Playwright browser automation
- Opens Instagram URLs
- Navigates carousel posts
- Captures screenshots
- Stores raw HTML snapshots where applicable
- Writes scrape metadata

Primary outputs:

```text
data/instagram/results.jsonl
data/instagram/screenshots/
data/instagram/html/
```

This is usually one of the slowest stages because it depends on browser automation and external website behavior.

---

### 👁️ 5. Vision / OCR

```text
tools/vision/ocr_extractor.py
```

Responsibilities:

- Reads screenshots
- Runs OCR using Tesseract
- Extracts raw text from visual prompt slides

Primary output:

```text
data/instagram/ocr_raw.jsonl
```

This stage converts visual content into machine-readable text.

---

### 🧩 6. Processing

```text
tools/processing/text_group_builder.py
tools/processing/text_manipulator_prep.py
```

Responsibilities:

- Reconstructs multi-slide carousel text
- Groups OCR output by post
- Reduces noise
- Prepares clean text for semantic extraction

Primary outputs:

```text
data/instagram/ocr_grouped.jsonl
data/instagram/text_manipulator_input.jsonl
```

This stage is where scattered OCR fragments become coherent prompt candidates.

---

### 🤖 7. Semantic Extraction

```text
tools/semantic/semantic_extraction_agent.py
tools/semantic/semantic_to_mongo_ready.py
```

Responsibilities:

- Extract structured prompt meaning
- Identify prompt components
- Convert semantic output into MongoDB-ready records
- Support multi-prompt carousel outputs

Primary outputs:

```text
data/instagram/text_manipulator_semantic_output.jsonl
data/instagram/mongo_ready_prompts.jsonl
```

One Instagram carousel may produce multiple prompt records.

---

### 💾 8. Storage

```text
tools/storage/mongodb_loader.py
```

Responsibilities:

- Loads Mongo-ready JSONL records into MongoDB
- Uses upsert behavior for idempotent loading
- Prevents duplicate insertion when records already exist

Target collection:

```text
prompt_extractor.prompt_library
```

Recommended unique key:

```text
prompt_unique_id
```

---

## 🧪 Running Individual Tools

Tools are normally executed by the pipeline runner:

```bash
python pipelines/run_pipeline_instagram.py
```

However, individual stages can be tested manually.

Example:

```bash
python tools/url_normalizer.py
```

Important: individual stages require their upstream input files to already exist.

For example, `url_normalizer.py` requires:

```text
data/url_queue.jsonl
```

If the input file is missing, the tool should fail fast. That is expected behavior.

---

## 📦 Data Contract Pattern

Most stages communicate using JSON Lines:

```text
*.jsonl
```

Each line is one JSON object.

This makes the pipeline:

- easy to inspect
- easy to restart
- easy to debug
- compatible with MongoDB loading
- friendly for future validation scripts

---

## 🧯 Debugging Checklist

If a tool fails:

1. Confirm the wrapper exists:

```bash
ls tools/*.py
```

2. Confirm the real module exists:

```bash
find tools -mindepth 2 -type f -name "*.py" | sort
```

3. Confirm the upstream input file exists:

```bash
ls -lh data/
ls -lh data/instagram/
```

4. Run the stage directly:

```bash
python tools/<stage>.py
```

5. If import issues occur, check that you are running from the repository root.

---

## 🧱 Safe Change Policy

Safe changes:

- internal function improvements
- bug fixes inside subfolder modules
- better logging
- validation additions
- non-breaking extra fields in JSONL output

Risky changes:

- renaming wrapper files
- changing pipeline stage names
- changing output filenames
- changing JSON field names
- modifying stage order
- deleting intermediate artifacts during a run

---

## 🚀 Future Enhancements

Potential future improvements:

- formal schema validation between stages
- per-stage logging files
- retry logic for browser scraping
- resume flags by stage
- test fixtures with sample JSONL inputs
- CI validation for wrapper imports
- MongoDB index creation script
- richer CLI arguments per tool

These should be added only after preserving the current tested baseline.

---

## 🏁 Summary

The tools layer is the execution backbone of Prompt Extractor.

It provides:

- stable wrapper entrypoints
- modular implementation domains
- JSONL-based data contracts
- inspectable intermediate artifacts
- a clean path from raw links to structured prompt records

The guiding principle is:

> **Stable execution outside, flexible implementation inside.**
