# ADR 003: Explicit State Machine & Idempotency

## Context
Webhook retries (Telegram/WhatsApp), double form submits, repeated file uploads, and reminder scheduler restarts can corrupt assignment or notification state.

## Decision
- Enforce explicit transition tables in `app/state_machine.py` for `assignments`, `student_assignments`, and `documents` (`approval_state`). Invalid transitions raise `InvalidStateTransitionError` (`400`).
- Deduplicate file uploads by `(school_id, content_sha256, doc_type)`, webhooks by `msg:<channel>:<message_id>`, and reminders by `rem:<student_assignment_id>:<kind>:<YYYY-MM-DD>`.
- Commit `denied` and `failed` audit events immediately so HTTP error rollbacks never erase security telemetry.

## Consequences
- Retrying any webhook, upload, or reminder job is safe and side-effect-free.
- Every state change is traceable via `correlation_id` in `audit_events` and `trace_spans`.
