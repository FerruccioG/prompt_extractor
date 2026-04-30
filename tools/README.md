# Tools — Executable Pipeline Stages

## Overview

The `tools/` directory contains the **core executable units** of the Prompt Extractor pipeline.

Each tool is a **self-contained Python script** responsible for a specific transformation step in the pipeline.

---

## 🧠 Design Philosophy

- **One tool = One responsibility**
- Tools communicate via **JSONL files**
- Tools are **stateless**
- Tools are **pipeline-driven**, not interactive

---

## 📂 Folder Structure

```text
tools/
├── ingestion/
├── normalization/
├── splitting/
├── scraping/
├── vision/
├── processing/
├── semantic/
├── storage/
````

## 🔧 Tool Categories

1. Ingestion
email_reader.py
Extracts URLs from emails
Outputs:
data/url_queue.jsonl
data/email_url_audit.jsonl
2. Normalization
url_normalizer.py
Cleans and standardizes URLs
url_filter.py
Removes duplicates or invalid URLs
3. Splitting
platform_splitter.py
Routes URLs by platform
Example:
Instagram → data/queues/instagram.jsonl
4. Scraping
scraper_instagram.py
Uses Playwright to:
Open posts
Capture screenshots
Extract metadata

Outputs:
```tect
data/instagram/
├── results.jsonl
├── screenshots/
├── html/
````

5. Vision (OCR)
ocr_extractor.py
Uses Tesseract OCR
Extracts text from screenshots

Outputs:
```tect
data/instagram/ocr_raw.jsonl
````
6. Processing
text_group_builder.py
Combines OCR text per post
text_manipulator_prep.py
Cleans text
Prepares for AI processing
7. Semantic (AI)
semantic_extraction_agent.py
Extracts structured meaning from text
semantic_to_mongo_ready.py
Converts output into MongoDB-ready format
8. Storage
mongodb_loader.py
Loads final prompts into MongoDB

## Execution Model

Tools are executed by:
Outputs:
```tect
pipelines/run_pipeline_instagram.py
````
Each tool is called as:
```tect
python tools/<category>/<tool>.py
````

## ⚠️ Important Rules
Do NOT rename tools without updating pipeline
Do NOT change input/output formats casually
Always preserve JSONL structure
Maintain backward compatibility
🧪 Testing Tools Individually

Example:
```text
python tools/normalization/url_normalizer.py
````
Ensure required input files exist:
```text
data/url_queue.jsonl
````
## 🔮 Future Improvements
Add CLI arguments for flexible runs
Add logging per tool
Add schema validation between stages
Introduce retry mechanisms

## 🏁 Summary
Tools are the execution backbone
Each stage transforms data step-by-step
Pipeline orchestrates tools in sequence

This modular design enables scalability, debugging, and safe iteration.

