# Schemas — Data Contracts

## Overview

The `schemas/` directory defines the **structure and expectations** of data produced and consumed across the pipeline.

These schemas act as **contracts between pipeline stages**, ensuring consistency and reliability.

---

## 🧠 Purpose

Schemas provide:

- Structural validation of JSONL data
- Clear expectations for each pipeline stage
- Stability when evolving internal logic
- Documentation of data transformations

---

## 📦 Data Format Standard

All pipeline artifacts follow:

```text
JSON Lines (.jsonl)
