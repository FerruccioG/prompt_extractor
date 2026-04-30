# 📊 Data Layer — Prompt Extractor Data Architecture (10X)

## 📌 Purpose

The `data/` directory represents the **state layer** of the Prompt Extractor system.

It is the single source of truth for:

- Raw inputs
- Intermediate transformations
- Final structured outputs

> If `tools/` are execution and `pipelines/` are orchestration,  
> `data/` is **memory + evidence**.

---

## 🧭 Architecture Overview

```text
data/
├── raw/           # Unprocessed inputs (external sources)
├── intermediate/  # Transitional pipeline artifacts
├── instagram/     # Platform-specific working data
├── final/         # Mongo-ready structured outputs
├── logs/          # Execution logs and traces
```

---

## 🔄 End-to-End Data Flow

```text
Email
  ↓
raw/
  ↓
intermediate/
  ↓
instagram/
  ↓
final/
  ↓
MongoDB
```

Each layer represents a **progressive refinement of data quality and structure**.

---

## 📂 Directory Responsibilities

### 📥 raw/

Contains untouched data from external systems.

Examples:

```text
email exports
initial URL captures
```

Characteristics:

- No transformation
- No guarantees on structure
- Used for auditing and replay

---

### 🔁 intermediate/

Holds pipeline transition artifacts.

Examples:

```text
url_queue.jsonl
url_queue_normalized.jsonl
url_queue_scrape_ready.jsonl
```

Characteristics:

- Structured but not final
- Frequently overwritten
- Acts as pipeline checkpoints

---

### 📸 instagram/

Platform-specific working directory.

Examples:

```text
results.jsonl
screenshots/
html/
ocr_raw.jsonl
ocr_grouped.jsonl
text_manipulator_input.jsonl
mongo_ready_prompts.jsonl
```

Characteristics:

- Heavy I/O (screenshots, HTML)
- Multi-stage transformations
- Core working dataset for current pipeline

---

### 🧾 final/

Contains fully processed, validated outputs.

Examples:

```text
mongo_ready_prompts.jsonl
```

Characteristics:

- Clean
- Structured
- Ready for MongoDB ingestion

---

### 📜 logs/

Stores execution traces and debugging information.

Examples:

```text
pipeline runs
error logs
stage outputs
```

---

## 🧱 Data Contract Principles

Across all layers:

- JSONL is the standard format
- One record per line
- Append-friendly
- Easily streamable
- MongoDB-compatible

---

## ⚠️ Critical Rules

Do NOT:

- Manually edit intermediate JSONL files
- Delete data during active pipeline runs
- Change file names without updating tools
- Mix data from different pipeline runs

Always:

- Keep runs isolated when testing
- Backup before major changes
- Inspect outputs at each stage

---

## 🧪 Debugging Strategy

If something breaks:

1. Locate failing stage
2. Inspect corresponding data folder
3. Validate input file integrity
4. Trace backward to previous stage

Example:

```bash
head data/instagram/ocr_raw.jsonl
```

---

## 🔁 CLEAN vs INCREMENTAL Behavior

CLEAN mode:

- Resets working directories
- Rebuilds all artifacts

INCREMENTAL mode:

- Reuses existing data
- Processes only new inputs

---

## 🧠 Design Philosophy

- Data is immutable per stage
- Every stage leaves evidence
- Debugging is file-based, not guess-based
- Transparency over abstraction

---

## 🚀 Future Enhancements

- Data versioning per run
- Run IDs for traceability
- Compression for screenshots
- Archival strategy for old runs
- Data lineage tracking

---

## 🏁 Summary

The `data/` layer is the backbone of traceability and reliability.

It enables:

- Full pipeline replay
- Deterministic debugging
- Safe iteration
- Auditability

Core rule:

> **If the data is correct, the system is correct.**
