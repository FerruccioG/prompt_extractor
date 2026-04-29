# OpenClaw Pipeline Integration

## Purpose

This project separates the OpenClaw agent layer from the deterministic Python pipeline.

OpenClaw defines:
- agent identity
- extraction discipline
- tool boundaries
- semantic reasoning expectations

Python executes:
- ingestion
- normalization
- scraping
- OCR
- text preparation
- semantic extraction
- MongoDB loading

---

## Execution Contract

OpenClaw should call only approved pipeline entrypoints.

Primary entrypoint:

python3 pipelines/run_pipeline_instagram.py

---

## Agent-to-Pipeline Boundary

OpenClaw may request:

- run full Instagram pipeline
- run OCR preparation
- run semantic extraction
- prepare MongoDB-ready records

OpenClaw must not:

- directly edit raw data
- bypass pipeline stages
- invent missing OCR content
- write directly to MongoDB without validation

---

## Source of Truth

Runtime OpenClaw files live outside this repo:

~/.openclaw/workspace/

Repository-safe templates live here:

docs/openclaw/templates/

---

## Core Rule

Python prepares evidence.  
AI decides meaning.  
Python enforces structure and persistence.
