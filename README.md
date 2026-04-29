# 🚀 Prompt Extractor

## 🧠 Overview

Prompt Extractor is an end-to-end data pipeline designed to collect, process, and structure prompts from social media platforms.

The system ingests URLs from historical email data, extracts content through scraping and OCR, applies text normalization and structuring, and loads the final dataset into MongoDB for downstream analysis or export.

---

## 🏗️ Architecture

```
EMAILS  
↓  
Email Reader  
↓  
URL Dataset  
↓  
URL Normalizer  
↓  
Scraper (HTML + Metadata)  
↓  
Screenshot Capture  
↓  
OCR Extraction  
↓  
Raw Text Dataset  
↓  
Text Cleaning & Structuring  
↓  
MongoDB (prompt_extract collection)  
↓  
Export (CSV/JSON → Excel)
```

---

## 🔧 Key Components

### 📩 Email Ingestion
- Extracts social media URLs from filtered email history
- Outputs structured dataset (`email_url_dataset.jsonl`)

### 🔗 URL Normalization
- Deduplicates and standardizes URLs

### 🌐 Scraping Layer
- Retrieves HTML and metadata

### 📸 Screenshot + OCR
- Captures visual content and extracts text

### 🧹 Text Processing
- Cleans OCR noise
- Structures content into:
  - Hook
  - Body
  - CTA

### 🗄️ Data Storage
- MongoDB (`prompt_extract` collection)

---

## 📊 Data Model

```json
{
  "source": "instagram",
  "url": "...",
  "raw_text": "...",
  "clean_text": "...",
  "structure": {
    "hook": "...",
    "body": "...",
    "cta": "..."
  },
  "timestamp": "..."
}
```

---

## 📁 Project Structure

```
prompt-extractor/
│
├── tools/
├── pipelines/
├── data/
├── configs/
├── schemas/
├── logs/
├── README.md
└── requirements.txt
```

---

## ⚙️ Tech Stack

- Python 🐍
- OCR 🧾
- MongoDB 🍃
- JSONL 📄

---

## 📌 Current Status

- Email ingestion: ✅  
- URL normalization: ✅  
- Scraping: 🔄  
- OCR: 🔄  
- Text structuring: ❗ Pending  
- MongoDB loading: ❗ Pending  

---

## 🎯 Design Principles

- Modular pipeline
- Idempotent processing
- Clean data contracts
- Separation of concerns

---

## 🔮 Future Enhancements

- Multi-platform scraping
- AI classification
- Prompt clustering
- Analytics dashboard

---

## ▶️ Usage (Planned)

```
python pipelines/run_full_pipeline.py
```

---

## 👤 Author

Ferruccio Guicciardi
