# AGENTS.md — Prompt Extractor Agent Ecosystem

## Purpose

This document defines how the Prompt Extractor agent ecosystem fits together, with special focus on the boundary between deterministic Python pipeline stages and true AI-driven semantic reasoning.

The system is designed as a **modular, agentic pipeline**, where each stage has a clear responsibility and strict boundaries.

The main architectural rule is:

- **Python prepares evidence**
- **AI Agent decides meaning**
- **Python enforces structure and persistence**

---

## Pipeline Overview

Current end-to-end flow:

```text
Email Reader
→ URL Normalizer
→ URL Filter
→ Platform Splitter
→ Instagram Scraper
→ OCR Extractor
→ Text Group Builder
→ Text Manipulator Prep
→ Text Manipulator Agent (handoff packager)
→ Semantic Extraction Agent (AI reasoning stage)
→ MongoDB Loader
→ Storage / DB / Indexing
