
# 📜 Logs Layer — Observability & Traceability (10X)

## 📌 Purpose

The `logs/` directory provides **observability, traceability, and debugging visibility** for the Prompt Extractor system.

It captures:

- Pipeline execution traces
- Stage-level outputs
- Errors and exceptions
- Runtime diagnostics

> If `data/` is the system memory,  
> `logs/` are the **system’s voice telling you what happened**.

---

## 🧭 Role in Architecture

```text
pipelines/ → tools/ → logs/
```

Logs are generated during execution and used for:

- Debugging failures
- Understanding pipeline behavior
- Auditing runs
- Performance analysis

---

## 📂 Structure

```text
logs/
├── pipeline/
├── stages/
├── errors/
├── debug/
```

*(structure may evolve as logging matures)*

---

## 🔧 Log Categories

### 🚀 1. Pipeline Logs

Track full pipeline execution.

Examples:

```text
logs/pipeline/run_2026-04-30.log
```

Contents:

- Start/end time
- Mode (CLEAN / INCREMENTAL)
- Stage progression
- Success/failure status

---

### 🧩 2. Stage Logs

Per-tool execution logs.

Examples:

```text
logs/stages/email_reader.log
logs/stages/scraper_instagram.log
```

Contents:

- Input/output summary
- Processing counts
- Warnings

---

### ❌ 3. Error Logs

Centralized failure tracking.

Examples:

```text
logs/errors/error_2026-04-30.log
```

Contents:

- Stack traces
- Failed stage
- Error message
- Context snapshot

---

### 🧪 4. Debug Logs

Verbose diagnostic output.

Examples:

```text
logs/debug/ocr_debug.log
```

Used for:

- Deep troubleshooting
- Development insights

---

## 🔄 Logging Flow

```text
Pipeline Start
  ↓
Stage Execution
  ↓
Log Write (pipeline + stage)
  ↓
Error? → error log
  ↓
Pipeline End
```

---

## ⚙️ Logging Strategy

Current (simple):

- Print to console
- Optional file logging

Recommended (future):

- Structured logging (JSON)
- Log levels (INFO, WARN, ERROR)
- Central logger module

---

## ⚠️ Critical Rules

Do NOT:

- Suppress errors silently
- Overwrite logs without reason
- Log sensitive data (passwords, tokens)

Always:

- Log failures clearly
- Include context (stage, file, input)
- Keep logs readable

---

## 🧪 Debugging Workflow

1. Identify failure stage
2. Check pipeline log
3. Check corresponding stage log
4. Inspect error log if present

Example:

```bash
tail -n 50 logs/pipeline/run_*.log
```

---

## 🔐 Security Considerations

Logs must NOT include:

- EMAIL_APP_PASSWORD
- API keys
- Tokens

If needed:

- Mask sensitive values
- Use placeholders

---

## 🚀 Future Enhancements

- Central logging module
- JSON structured logs
- Log rotation
- Log retention policy
- Integration with monitoring tools (ELK, Grafana)
- Correlation IDs per pipeline run

---

## 🧠 Design Philosophy

- Logs are first-class citizens
- Visibility > assumptions
- Debugging should be evidence-based

---

## 🏁 Summary

The `logs/` layer enables:

- Transparency
- Debugging efficiency
- Operational confidence

Core rule:

> **If you cannot see what happened, you cannot trust the system.**
