# 🚀 Prompt Extractor — End-to-End AI Prompt Intelligence Pipeline (10X)

## 🧭 Overview

**Prompt Extractor** is a production-grade pipeline that converts **unstructured social content** into **structured, reusable AI prompts**.

It ingests content from email-discovered URLs (e.g., Instagram), processes visual and textual data, and outputs **MongoDB-ready prompt assets**.

> From noise → to signal → to structured AI intelligence.

---

## 🧱 System Architecture

```text
Email
  ↓
Ingestion (tools/)
  ↓
Normalization & Filtering
  ↓
Platform Splitting
  ↓
Scraping (Playwright)
  ↓
OCR (Tesseract)
  ↓
Text Processing
  ↓
Semantic Extraction (AI)
  ↓
Schema Validation
  ↓
MongoDB Storage
```

---

## 📦 Project Structure

```text
prompt-extractor/
├── tools/        # Execution units (single-responsibility scripts)
├── pipelines/    # Orchestration layer
├── schemas/      # Data contracts
├── data/         # Pipeline state & artifacts
├── prompts/      # AI-ready prompt intelligence
├── config/       # Environment & runtime config
├── logs/         # Observability layer
```

---

## ⚙️ How It Works

### 1. Pipeline Runner

```bash
python pipelines/run_pipeline_instagram.py
```

This orchestrates all stages sequentially.

---

### 2. Environment Setup

Create `.env` at repo root:

```env
EMAIL_ADDRESS=your_email@gmail.com
EMAIL_APP_PASSWORD=your_app_password
GMAIL_QUERY=in:inbox subject:prompt
PIPELINE_MODE=CLEAN
```

---

### 3. Install Dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install
```

---

## 🔄 Pipeline Modes

### CLEAN
- Full rebuild
- Clears working directories

### INCREMENTAL
- Processes only new data
- Faster iteration

---

## 🧠 Core Principles

- **Modularity** — each tool does one thing
- **Determinism** — reproducible runs
- **Transparency** — file-based state
- **Traceability** — every stage leaves evidence
- **Data Contracts** — enforced via schemas

---

## 📊 Data Flow

```text
data/raw → data/intermediate → data/instagram → data/final
```

All transformations are persisted as JSONL.

---

## 🧪 Debugging

Run individual stages:

```bash
python tools/url_normalizer.py
```

Inspect outputs:

```bash
head data/instagram/ocr_raw.jsonl
```

Check logs:

```bash
tail logs/pipeline/*.log
```

---

## 🧩 Example Output

```json
{
  "prompt_text": "Write a LinkedIn post about AI trends",
  "tags": ["ai", "linkedin", "marketing"],
  "source": {
    "platform": "instagram",
    "url": "https://instagram.com/..."
  }
}
```

---

## 🔐 Security

- `.env` is NOT committed
- Secrets are externalized
- Logs must not contain sensitive data

---

## 🚀 Roadmap

- Schema validation layer
- Prompt scoring engine
- RAG integration
- Parallel execution
- CI/CD validation

---

## 🏁 Summary

Prompt Extractor is not just a scraper — it is a **data-to-intelligence pipeline**.

It transforms:

> 📸 Visual content → 🧾 Text → 🧠 Meaning → 🧩 Structured prompts

---

## 👤 Author

Ferruccio Guicciardi  
AI Architect | Data Engineer | Prompt Systems Builder

---

## 📜 License

MIT License
