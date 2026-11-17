# ADR 001: Domain & Scoped Access Model

## Context
Schools have hierarchical relationships: Schools contain Classes, Teachers can teach multiple Classes (M:N), Students belong to a Class, and Guardians can link to multiple Students (M:N). We need strong multi-tenant isolation without overbuilding enterprise SSO.

## Decision
- Enforce `school_id` isolation on every domain table and API handler.
- Use PBKDF2-SHA256 password hashing + HMAC-SHA256 hashed session tokens stored in SQLite (`sessions`) with expiration and revocation.
- Use short-lived, role- and resource-scoped invite codes (`INV-XXXXXXXX`) tied to `school_id` and `class_id` or `linked_student_id` to prevent cross-school or cross-class onboarding mistakes.
- Require explicit `/link INV-XXXXXXXX` or authenticated web linking before chat identities (`telegram`, `whatsapp`) can perform any school action.

## Consequences
- Every denied cross-school or wrong-role access attempt is rejected server-side (`403`) and immediately committed to `audit_events`.
- A junior engineer adding a new endpoint only needs to call `require_user`, `require_school_scope`, or `require_teacher_class_scope`.
