# Tools Layer — Execution Entry Points vs Implementation

## Overview

The `tools/` directory serves two distinct purposes:

1. **Execution entrypoints (flat files in `tools/*.py`)**
2. **Implementation modules (organized in subfolders)**

This separation is intentional and critical to avoid breaking the pipeline.

---

## 🧭 How the Pipeline Works

The pipeline runner:

```bash
pipelines/run_pipeline_instagram.py
