# TOOLS.template.md — Tooling Contract

## Purpose

Defines how the agent interacts with data within the Prompt Extractor system.

---

## Core Principle

Tools are for:
- reading input
- processing data
- writing output

They are NOT for creativity or invention.

---

## Input Source

<DATA_INPUT_JSONL_PATH>

Example:
data/instagram/text_manipulator_input.jsonl

---

## Output Target

<DATA_OUTPUT_JSONL_PATH>

Example:
data/processed/text_manipulator_output.jsonl

---

## Execution Model

- Deterministic processing per record
- Stateless execution
- No hidden dependencies

---

## Constraints

- No external internet access
- No hidden data sources
- No side effects outside defined pipeline
- Must respect pipeline stage boundaries

---

## Integration Notes

- Upstream stages prepare data
- Agent performs semantic processing
- Downstream stages handle storage (e.g., MongoDB)

---

## Extension Guidelines

When adding new tools:

- Keep single responsibility
- Ensure idempotent behavior
- Maintain JSONL contracts
