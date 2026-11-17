# Implementation Note — School Operations Agent Platform

## Assumptions
- Single-node SQLite with `PRAGMA foreign_keys = ON` is sufficient for a small-team school operations slice; horizontal scaling would swap SQLite for PostgreSQL and in-memory SSE/rate-limiting for Redis.
- Chat channels (Telegram, WhatsApp, and Web Simulator) normalize into a single `CanonicalMessageEnvelope` so business logic is channel-agnostic.
- `LLM_PROVIDER=deterministic` is the default so the platform, tests, and evals run deterministically without external API keys, with optional OpenAI JSON-mode classification and automatic fallback.

## Domain & Access Model
- **Hierarchy**: `schools` -> `classes` -> `teacher_classes` (M:N), `student_enrollments` (student -> class), and `student_guardians` (M:N). `assignments` target a class, group, or individual student, creating per-student `student_assignments` rows. `documents` track `owner_type` (`school`, `class`, `teacher`, `assignment`, `student`) and `owner_id`.
- **Auth & Scoped Onboarding**: Passwords use PBKDF2-SHA256 with per-user salts; sessions use HMAC-SHA256 hashed tokens with expiry and revocation. Admins/teachers issue short-lived, role- and resource-scoped invite codes (`INV-XXXXXXXX`) tied to `school_id` and `class_id` or `linked_student_id`, blocking cross-school or cross-class mistakes at invite creation and acceptance.
- **Server-Side Boundaries**: Every API route enforces `school_id`, role, and resource ownership (`require_teacher_class_scope`, student-own-work checks, guardian-linked-child checks). Denied attempts return HTTP 403 and commit an `access_denied` audit event immediately.

## Parsing Strategy & Intent Layer
- **Document Parser**: Extracts text from PDF (`pypdf`), DOCX (`python-docx`), CSV, and plain text for `assignment_brief`, `class_roster`, `school_policy`, and `student_submission`. Missing or ambiguous fields move the document to `needs_clarification` with explicit follow-up questions; roster CSVs flag duplicate students, unknown classes, and missing contacts in `roster_conflicts` for human resolution. High-impact actions require explicit teacher/admin approval (`pending_review` -> `approved`).
- **Prompt-Injection Defense**: `detect_and_sanitize_injection` scans documents and chat inputs for instruction-override patterns, quarantines suspicious lines, lowers confidence, logs `prompt_injection_detected`, and blocks auto-approval.
- **Intent Layer**: Classifies 16+ school operations intents (`create_assignment`, `progress_update`, `blocked_help_request`, `submission`, `resubmission`, `parent_digest_request`, etc.) plus `unknown_unsafe_out_of_scope` fallback, recording end-to-end `channel -> intent -> action -> notification` spans in `trace_spans`.

## State Model & Idempotency
- **Explicit State Machines** (`app/state_machine.py`):
  - Assignments: `draft -> active -> cancelled -> archived`
  - Student Assignments: `assigned -> in_progress | blocked -> submitted -> revision_requested | completed`
  - Documents: `uploaded -> needs_clarification | pending_review -> approved | rejected`
- **Idempotency**: Repeated file uploads deduplicate by `(school_id, content_sha256, doc_type)`. Webhook retries deduplicate by `msg:<channel>:<message_id>`. Reminders deduplicate by `rem:<student_assignment_id>:<kind>:<YYYY-MM-DD>`.

## Failures Handled
- **Quiet Hours & Escalation**: The reminder engine respects `quiet_hours_start`/`quiet_hours_end` (deferring with `deferred_quiet_hours`), skips `submitted`/`completed` students, nudges silent students, and escalates `blocked` students to opted-in guardians.
- **Model/Webhook/DB Rollback Safety**: OpenAI failures fall back to deterministic classification so workflows never stall. Denied/failed audit events commit independently of HTTP exception rollbacks so security telemetry is never lost.
- **Upload & Abuse Controls**: Extension allowlists, 5 MB size caps, filename sanitization, CORS restrictions, security headers, and per-IP rate limiting protect endpoints.

## What I Would Improve Next
1. Migrate SQLite + in-memory SSE/rate-limiter to PostgreSQL + Redis Pub/Sub for multi-instance deployment.
2. Add OCR (Tesseract/Vision) and Whisper voice-note transcription for photo/audio submissions.
3. Add background worker queue (Celery/ARQ) with exponential backoff for outbound Telegram/WhatsApp delivery.
4. Add document chunking/vector retrieval for natural-language Q&A over uploaded school policies.
