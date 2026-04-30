# 🧬 Schemas — Data Contracts Layer (10X)

## 📌 Purpose

The `schemas/` directory defines the **data contracts** for the Prompt Extractor pipeline.

This layer ensures that data flowing between pipeline stages is:

- ✅ Structured
- ✅ Validated
- ✅ Consistent
- ✅ MongoDB-ready

> If `tools/` transform data,  
> `schemas/` define what “correct data” means.

---

## 🧭 Role in Architecture

```text
Email → Tools → JSONL → Schemas → Validated Data → MongoDB
```

Schemas act as a **quality gate** between processing and storage.

---

## 🧱 Why Schemas Matter

Without schemas:

- Silent data corruption can occur
- Downstream stages break unpredictably
- MongoDB collections become inconsistent

With schemas:

- Data integrity is enforced
- Debugging becomes deterministic
- Pipelines become production-ready

---

## 📂 Structure

```text
schemas/
├── prompt_schema.json
├── mongo_schema.json
├── validation_rules.md
```

*(actual files may evolve as schemas mature)*

---

## 🔧 Schema Types

### 1. Prompt Extraction Schema

Defines structure of extracted prompts:

```json
{
  "prompt_text": "string",
  "source_url": "string",
  "platform": "string",
  "extracted_at": "datetime",
  "confidence_score": "float"
}
```

---

### 2. Mongo Ready Schema

Defines final structure for database insertion:

```json
{
  "prompt_id": "string",
  "prompt_text": "string",
  "tags": ["string"],
  "source": {
    "platform": "string",
    "url": "string"
  },
  "created_at": "datetime"
}
```

---

## 🔄 Schema Lifecycle

```text
Raw OCR → Processed Text → Semantic Extraction → Schema Validation → Mongo Insert
```

Schemas are applied **just before storage**.

---

## ⚙️ Validation Strategy

Validation can be:

- Manual (inspection during development)
- Programmatic (future enhancement)

Recommended tools:

- `pydantic`
- JSON Schema validation
- Custom validation scripts

---

## ⚠️ Stability Rules

Do NOT:

- Change field names casually
- Remove fields used by downstream tools
- Modify schema without updating loaders

Always:

- Version schema changes
- Document changes in commit messages

---

## 🧪 Testing Schema Integrity

Example:

```bash
python tools/semantic/semantic_to_mongo_ready.py
```

Then inspect:

```bash
head data/instagram/mongo_ready_prompts.jsonl
```

Validate:

- Required fields exist
- Data types are consistent
- No null critical values

---

## 🧠 Design Philosophy

- Schemas are contracts, not suggestions
- Validation should fail fast
- Data must be predictable across runs

---

## 🚀 Future Enhancements

- Full JSON Schema validation
- Automated validation step in pipeline
- Schema versioning
- Backward compatibility layer
- Data quality scoring

---

## 🏁 Summary

Schemas define the **truth model** of your data.

They ensure:

- Consistency across pipeline stages
- Safe MongoDB insertion
- Long-term maintainability

Core rule:

> **No schema = no trust in data.**
