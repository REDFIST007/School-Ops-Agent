# ADR 002: Deterministic + Pluggable LLM Parsing & Intent Layer

## Context
Relying solely on an external LLM creates flakiness, cost, and prompt-injection risks, while pure rigid commands fail on messy school briefs and natural chat messages.

## Decision
- Separate **model reasoning** (extracting structured fields / classifying intent into Pydantic schemas) from **deterministic business rules** (RBAC checks, state transitions, quiet hours, database writes).
- Default to `LLM_PROVIDER=deterministic` so the entire platform, tests, and evals run reliably offline with zero API keys, while supporting an optional `openai` provider that validates JSON against `IntentResult` and falls back to deterministic on any error.
- Run a prompt-injection sanitizer (`detect_and_sanitize_injection`) before parsing or intent routing, quarantining suspicious lines and never allowing document text to auto-approve high-impact actions.

## Consequences
- Model failures never leave assignments or documents stuck silently.
- Teachers/admins always review extracted fields and answer clarification questions before an assignment or policy is committed.
