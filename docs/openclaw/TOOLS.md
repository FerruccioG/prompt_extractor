# TOOLS.md — Text Manipulator AI Agent Tooling Contract

## Purpose

This document defines the tools, data interfaces, and operational boundaries for the Text Manipulator AI Agent.

The agent operates within a controlled environment and must strictly respect these constraints.

---

## Core Principle

Tools are **for data access and persistence**, not for creativity.

The agent must:

- read input
- process semantically
- write structured output

It must **not expand beyond its defined toolset**.

---

## Available Data Sources

### 1. Prepared OCR Input

**Primary Input File:**

```text
data/instagram/text_manipulator_input.jsonl
