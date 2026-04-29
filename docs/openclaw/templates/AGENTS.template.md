# AGENTS.template.md — Prompt Extractor Agent Ecosystem

## Purpose

Defines the agent ecosystem and boundaries between deterministic pipeline stages and AI reasoning.

---

## Core Architecture Rule

- Python prepares evidence
- AI Agent decides meaning
- Python enforces structure and persistence

---

## Pipeline Overview

Email Reader
→ URL Normalizer
→ URL Filter
→ Platform Splitter
→ Scraper
→ OCR Extractor
→ Text Group Builder
→ Text Manipulator Prep
→ Text Manipulator Agent
→ Semantic Extraction Agent
→ MongoDB Loader

---

## Design Principles

- Modular pipeline stages
- Clear handoff between deterministic and AI stages
- Traceable transformations
- No hidden logic inside AI

---

## Agent Responsibilities

### Text Manipulator Agent
- Clean OCR noise
- Reconstruct text
- Extract prompt candidates

### Semantic Extraction Agent
- Interpret meaning
- Classify content
- Structure outputs

---

## Boundaries

- Python handles data movement and structure
- AI handles meaning only
- No overlap in responsibilities
