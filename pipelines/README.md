# 🚀 Pipelines — Orchestration Layer (10X)

## 📌 Purpose

The `pipelines/` directory contains the orchestration logic that drives the **Prompt Extractor** system end-to-end.

While `tools/` are individual workers, **pipelines define the execution strategy**.

> Tools = muscles  
> Pipelines = nervous system

---

## 🧭 Core Pipeline

```text
pipelines/run_pipeline_instagram.py
```

This is the **primary production pipeline**.

It orchestrates all stages required to transform:

```text
Email → URLs → Screenshots → OCR → Structured Prompts
```

---

## 🔁 Execution Flow

```text
1. email_reader.py
2. url_normalizer.py
3. url_filter.py
4. platform_splitter.py
5. scraper_instagram.py
6. ocr_extractor.py
7. text_group_builder.py
8. text_manipulator_prep.py
```

Each stage:

- Executes sequentially
- Consumes previous stage output
- Produces next stage input

---

## ⚙️ Execution Model

Pipeline dynamically resolves tools:

```python
TOOLS_DIR = ROOT_DIR / "tools"
script_path = TOOLS_DIR / script_name
```

Execution call:

```bash
python tools/<stage>.py
```

---

## 🔒 Stability Principle

⚠️ This file is **frozen-critical**.

You explicitly stated:

> “do not touch this — it worked successfully”

So we define:

### ✅ Allowed changes

- Logging improvements
- Better error messages

### ❌ Forbidden changes

- Changing PIPELINE list order
- Renaming stages
- Changing execution logic
- Modifying environment variable handling

---

## 🧪 Pipeline Modes

Controlled via:

```bash
PIPELINE_MODE=CLEAN|INCREMENTAL
```

### CLEAN
- Resets Instagram working directories
- Rebuilds from scratch

### INCREMENTAL
- Processes only new data
- Preserves previous artifacts

---

## 🔐 Required Environment Variables

```text
EMAIL_ADDRESS
EMAIL_APP_PASSWORD
GMAIL_QUERY
```

Pipeline will fail fast if missing.

---

## 🧹 Reset Behavior

CLEAN mode wipes:

```text
data/instagram/screenshots/
data/instagram/html/
```

This ensures deterministic rebuilds.

---

## 🧠 Design Philosophy

- Deterministic execution
- File-based state (no hidden memory)
- Fail fast on missing inputs
- Transparent stage-by-stage processing

---

## 🧯 Debugging Strategy

If pipeline fails:

1. Identify failing stage
2. Run stage manually:
   ```bash
   python tools/<stage>.py
   ```
3. Inspect input/output files
4. Resume from fixed point

---

## 🧱 Contract with Tools Layer

Pipeline depends on:

- Stable wrapper filenames in `tools/`
- Consistent JSONL formats
- Predictable output file locations

Breaking any of these = pipeline failure

---

## 🚀 Future Evolution (Controlled)

Possible upgrades:

- Parallel stage execution
- Retry logic per stage
- Partial resume flags
- Stage-level timing metrics
- DAG-based execution (instead of linear)

⚠️ Only after preserving baseline behavior

---

## 🏁 Summary

Pipelines orchestrate the full system.

They provide:

- Execution order
- Failure boundaries
- Environment control
- Deterministic rebuild capability

Core rule:

> **Do not destabilize orchestration for local improvements.**
