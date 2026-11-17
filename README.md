# School Operations Agent Platform

A production-shaped **School Operations Agent Platform** built with **FastAPI**, **SQLite**, **Jinja2 + Server-Sent Events (SSE)**, and a **Canonical Multi-Channel Envelope** supporting **Telegram**, **WhatsApp**, and a built-in **Web Chat Simulator**.

## Architecture Overview

```
+-----------------------+      +-----------------------+      +-----------------------+
|   Web App (Jinja2)    |      |   Telegram Webhook    |      |   WhatsApp Webhook    |
| Admin/Teacher/Stu/Par |      | /api/webhooks/telegram|      | /api/webhooks/whatsapp|
+-----------+-----------+      +-----------+-----------+      +-----------+-----------+
            |                              |                              |
            |                              +--------------+---------------+
            |                                             v
            |                              +------------------------------+
            |                              |  Canonical Message Envelope  |
            |                              |  + Idempotency Guard (SHA)   |
            |                              +--------------+---------------+
            |                                             v
            v                                     +---------------+
+-----------------------+                         |  Intent Layer |
|  FastAPI Auth & RBAC  |<------------------------|  (16+ Intents |
|  School/Class/Student |                         |  + Unsafe)    |
+-----------+-----------+                         +-------+-------+
            |                                             |
            v                                             v
+-----------------------+      +------------------------------------------+
|   Document Parser     |      |           Domain Services                |
| PDF/DOCX/CSV/Text     |----->| Assignments | Reminders | Audit & Traces |
| + Injection Guard     |      | State Machine | Quiet Hours | SSE Broker |
+-----------------------+      +------------------------------------------+
                                                  |
                                                  v
                                        +-------------------+
                                        | SQLite (FK ON)    |
                                        +-------------------+
```

## Quick Start (Local)

```bash
python3 -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000/register` to register a school, or run the full test & eval suite:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 evals/eval_parsing_and_intent.py
```

## Domain & Access Model

- **School Admin / Coordinator**: Scoped strictly to their school. Creates grades/classes, issues invites, uploads rosters/policies, views audit & traces.
- **Teacher**: Scoped to assigned classes (`teacher_classes`). Uploads/approves assignment briefs, creates assignments, reviews progress/submissions, gives feedback/revisions/completion.
- **Student**: Scoped to own `student_assignments`. Reports progress/blockers, submits/resubmits work, views own feedback.
- **Parent / Guardian**: Scoped to linked children (`student_guardians`). Opts in, views high-level progress, receives escalation alerts.

## Document Parsing Strategy

- Supports `assignment_brief` (PDF, DOCX, text), `class_roster` (CSV/text with duplicate & ambiguous class detection), `school_policy` (quiet hours, escalation hours, channels), and `student_submission`.
- **Prompt Injection Defense**: Scans all incoming text and documents for instruction-override patterns, quarantines suspicious lines, logs `prompt_injection_detected`, and blocks auto-approval.
- **Human-in-the-Loop**: Missing/ambiguous fields transition documents to `needs_clarification` with explicit follow-up questions before `pending_review` -> `approved`.

## Architecture Decision Records (ADRs)

- [ADR 001: Domain & Scoped Access Model](docs/adr/001-domain-and-access-model.md)
- [ADR 002: Deterministic + Pluggable LLM Parsing](docs/adr/002-deterministic-plus-llm-parsing.md)
- [ADR 003: Explicit State Machine & Idempotency](docs/adr/003-state-machine-and-idempotency.md)

## Known Limitations & Trade-Offs

- **Single-node SQLite & In-Memory SSE/Rate-Limiter**: Chosen for zero-dependency local & small-team operability; horizontal scaling would swap SQLite for PostgreSQL and SSE/rate-limiter for Redis Pub/Sub.
- **Offline Image OCR**: Image uploads are validated, hashed, and stored safely, with OCR text extraction stubbed when no external OCR/LLM provider is configured.
- **Deterministic Default Provider**: `LLM_PROVIDER=deterministic` runs 100% offline with zero API keys; `LLM_PROVIDER=openai` can be enabled in `.env` with automatic fallback to deterministic on failure.
