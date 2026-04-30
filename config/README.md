# ⚙️ Config Layer — Environment & Runtime Control (10X)

## 📌 Purpose

The `config/` directory defines how the Prompt Extractor system is **configured, parameterized, and executed across environments**.

It centralizes:

- Environment variables
- Runtime switches
- Secrets handling strategy (via `.env`)
- Non-code configuration

> If `pipelines/` orchestrate execution,  
> `config/` controls **how and under what conditions** that execution happens.

---

## 🧭 Role in Architecture

```text
.env → config/ → pipelines/ → tools/ → data/
```

Configuration is **read at runtime** and influences:

- Data sources (email query)
- Execution mode (CLEAN vs INCREMENTAL)
- Credentials (email, APIs)
- Behavior toggles

---

## 📂 Structure

```text
config/
├── README.md
├── defaults.env.example
├── settings.md
```

*(files may evolve as the system matures)*

---

## 🔐 Environment Variables

Core variables used by the pipeline:

```text
EMAIL_ADDRESS
EMAIL_APP_PASSWORD
GMAIL_QUERY
PIPELINE_MODE
```

Optional / future:

```text
MONGODB_URI
MONGODB_DB
MONGODB_COLLECTION
OPENAI_API_KEY
LOG_LEVEL
```

---

## 📄 .env File (Repository Root)

The system expects a `.env` file at repo root:

```bash
prompt-extractor/.env
```

Example:

```env
EMAIL_ADDRESS=your_email@gmail.com
EMAIL_APP_PASSWORD=your_app_password
GMAIL_QUERY=in:inbox subject:prompt
PIPELINE_MODE=CLEAN
```

### Rules

- Do NOT commit `.env` to Git
- Use `.gitignore`
- Share via `defaults.env.example`

---

## 🔁 Configuration Modes

### CLEAN

- Rebuild pipeline from scratch
- Resets Instagram working dirs
- Deterministic runs

### INCREMENTAL

- Process only new data
- Preserve artifacts
- Faster iteration

Switch via:

```bash
export PIPELINE_MODE=CLEAN
```

---

## 🧠 Configuration Principles

- Externalize all environment-specific values
- Keep code environment-agnostic
- Fail fast if required config is missing
- Prefer explicit over implicit defaults

---

## ⚠️ Critical Rules

Do NOT:

- Hardcode credentials in code
- Commit secrets to repository
- Depend on local-only paths
- Change variable names without updating pipeline

Always:

- Validate env vars at startup
- Document new variables
- Provide example defaults

---

## 🧪 Validation Strategy

Pipeline performs preflight checks:

- Required variables present
- Values non-empty
- Basic format validation

If missing → pipeline exits early

---

## 🔐 Secrets Management (Best Practice)

Current:

- `.env` file

Future (recommended):

- Vault / Secret Manager
- Environment injection (CI/CD)
- Docker secrets

---

## 🚀 Future Enhancements

- Central config loader module
- Typed config (pydantic settings)
- Environment profiles (dev/staging/prod)
- CLI overrides
- Config versioning

---

## 🏁 Summary

The `config/` layer enables:

- Reproducible runs
- Secure credential handling
- Flexible environment control

Core rule:

> **No config discipline = no production readiness.**
