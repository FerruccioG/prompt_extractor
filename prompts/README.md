
# 🧠 Prompts Layer — AI Intelligence Assets (10X)

## 📌 Purpose

The `prompts/` directory contains the **intelligence layer** of the Prompt Extractor system.

This is where raw extracted text becomes **usable, structured, and valuable prompt knowledge**.

> If `tools/` extract data and `schemas/` define structure,  
> `prompts/` define **meaning and usability**.

---

## 🧭 Role in Architecture

```text
Screenshots → OCR → Text → prompts/ → AI Extraction → Structured Prompts → MongoDB
```

The prompts layer bridges:

- ❌ Raw text (noisy, unstructured)
- ✅ Usable AI prompts (clean, reusable, categorized)

---

## 📂 Structure

```text
prompts/
├── templates/        # Prompt templates for AI agents
├── examples/         # Sample extracted prompts
├── cleaned/          # Post-processed prompt text
├── enriched/         # Tagged and categorized prompts
├── experimental/     # Testing and iteration prompts
```

*(Structure may evolve as system matures)*

---

## 🔧 Prompt Lifecycle

```text
OCR Text
  ↓
Cleaning
  ↓
Grouping
  ↓
Prompt Extraction
  ↓
Semantic Structuring
  ↓
Final Prompt Object
```

---

## 🧹 1. Cleaning Stage

Responsibilities:

- Remove OCR noise
- Fix broken lines
- Normalize formatting
- Remove irrelevant text

Goal:

> Turn messy OCR text into readable content

---

## 🧩 2. Structuring Stage

Responsibilities:

- Identify prompt boundaries
- Separate multiple prompts in a single post
- Detect titles, instructions, parameters

Example:

```text
"Write a LinkedIn post about AI"
```

becomes:

```json
{
  "prompt_text": "Write a LinkedIn post about AI",
  "type": "content_generation"
}
```

---

## 🤖 3. Semantic Enrichment

Responsibilities:

- Tag prompts (e.g., marketing, coding, storytelling)
- Add metadata
- Identify use cases
- Detect prompt intent

Example:

```json
{
  "tags": ["linkedin", "ai", "marketing"],
  "intent": "content_generation"
}
```

---

## 🧠 Prompt Intelligence Model

Each prompt should ideally evolve toward:

```json
{
  "prompt_text": "string",
  "category": "string",
  "tags": ["string"],
  "intent": "string",
  "source": {
    "platform": "instagram",
    "url": "string"
  },
  "quality_score": "float"
}
```

---

## ⚠️ Critical Rules

Do NOT:

- Store raw OCR text here
- Mix experimental prompts with production-ready prompts
- Change prompt formats without updating schemas

Always:

- Keep prompts clean and structured
- Maintain consistency with Mongo schema
- Ensure prompts are reusable

---

## 🧪 Testing Prompts

Prompts should be:

- Readable by humans
- Executable by AI models
- Contextually complete

Quick test:

```text
Can this prompt be directly used in ChatGPT or OpenAI API?
```

If not → refine it

---

## 🧠 Design Philosophy

- Prompts are **assets**, not artifacts
- Quality > quantity
- Structure enables reuse
- Meaning matters more than extraction

---

## 🚀 Future Enhancements

- Prompt scoring system
- Prompt deduplication
- Prompt similarity clustering
- Prompt versioning
- Prompt marketplace/export layer
- Integration with RAG pipelines

---

## 🏁 Summary

The `prompts/` layer transforms extracted content into **AI-ready intelligence**.

It enables:

- Reusable prompt libraries
- Structured AI workflows
- High-value data extraction from social content

Core rule:

> **If the prompt is not usable, the pipeline has failed.**
