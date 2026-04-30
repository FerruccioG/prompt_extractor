# 🛠️ Tools — Executable Pipeline Stages

## 📌 Overview

The `tools/` directory contains the **core executable units** of the Prompt Extractor pipeline.

Each tool is a **single-responsibility Python script** responsible for transforming data from one stage to the next.

---

## 🧠 Design Philosophy

- 🔹 One tool = One responsibility
- 🔹 Stateless execution (no shared memory)
- 🔹 Communication via JSONL files
- 🔹 Pipeline-driven (not interactive)

---

## 📂 Folder Structure

tools/
├── ingestion/
├── normalization/
├── splitting/
├── scraping/
├── vision/
├── processing/
├── semantic/
├── storage/

---

## 🔧 Tool Categories

### 📥 Ingestion
- email_reader.py  
  Extracts URLs from emails  
  Outputs:
  - data/url_queue.jsonl
  - data/email_url_audit.jsonl

---

### 🧹 Normalization
- url_normalizer.py  
- url_filter.py  

Cleans and filters URLs

---

### 🔀 Splitting
- platform_splitter.py  

Routes URLs by platform (e.g., Instagram)

---

### 🌐 Scraping
- scraper_instagram.py  

Uses Playwright to:
- Open posts
- Capture screenshots
- Extract metadata

Outputs:
data/instagram/
├── results.jsonl
├── screenshots/
├── html/

---

### 👁️ Vision (OCR)
- ocr_extractor.py  

Uses Tesseract OCR

Output:
data/instagram/ocr_raw.jsonl

---

### 🧩 Processing
- text_group_builder.py  
- text_manipulator_prep.py  

Prepares text for AI processing

---

### 🤖 Semantic (AI)
- semantic_extraction_agent.py  
- semantic_to_mongo_ready.py  

Transforms text into structured data

---

### 💾 Storage
- mongodb_loader.py  

Loads final data into MongoDB

---

## ▶️ Execution Model

Executed via:

pipelines/run_pipeline_instagram.py

Each stage runs:

python tools/<category>/<tool>.py

---

## ⚠️ Rules

- Do NOT rename tools without updating pipeline
- Do NOT change JSONL structure casually
- Maintain backward compatibility

---

## 🧪 Testing Example

python tools/normalization/url_normalizer.py

Requires:
data/url_queue.jsonl

---

## 🏁 Summary

Tools are the **execution backbone** of the pipeline.

Each stage transforms data step-by-step in a modular, scalable way.
