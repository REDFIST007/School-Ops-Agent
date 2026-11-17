import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import settings


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schools (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    quiet_hours_start TEXT NOT NULL DEFAULT '21:00',
    quiet_hours_end TEXT NOT NULL DEFAULT '07:00',
    escalation_hours INTEGER NOT NULL DEFAULT 24,
    allowed_channels TEXT NOT NULL DEFAULT '["telegram","whatsapp","web"]',
    require_teacher_approval INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classes (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    grade TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(school_id, name)
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    email TEXT UNIQUE,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'teacher', 'student', 'guardian')),
    password_hash TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    guardian_opt_in INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teacher_classes (
    teacher_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    PRIMARY KEY (teacher_id, class_id)
);

CREATE TABLE IF NOT EXISTS student_enrollments (
    student_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    roll_number TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS student_guardians (
    student_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    guardian_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL DEFAULT 'parent',
    PRIMARY KEY (student_id, guardian_id)
);

CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('teacher', 'student', 'guardian')),
    class_id TEXT REFERENCES classes(id) ON DELETE CASCADE,
    linked_student_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    invitee_name TEXT NOT NULL,
    invitee_email TEXT,
    created_by TEXT NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL,
    used_at TEXT,
    used_by TEXT REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS chat_identities (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('telegram', 'whatsapp', 'web')),
    external_user_id TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    UNIQUE(channel, external_user_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    uploaded_by TEXT NOT NULL REFERENCES users(id),
    owner_type TEXT NOT NULL CHECK (owner_type IN ('school', 'class', 'teacher', 'assignment', 'student')),
    owner_id TEXT NOT NULL,
    doc_type TEXT NOT NULL CHECK (doc_type IN ('assignment_brief', 'class_roster', 'school_policy', 'student_submission')),
    filename TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    storage_path TEXT,
    raw_text TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    ambiguity_notes TEXT NOT NULL DEFAULT '[]',
    missing_fields TEXT NOT NULL DEFAULT '[]',
    prompt_injection_flagged INTEGER NOT NULL DEFAULT 0,
    approval_state TEXT NOT NULL CHECK (approval_state IN ('uploaded', 'needs_clarification', 'pending_review', 'approved', 'rejected')),
    clarification_answers TEXT NOT NULL DEFAULT '{}',
    approved_by TEXT REFERENCES users(id),
    approved_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roster_conflicts (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    row_index INTEGER NOT NULL,
    student_name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    guardian_contact TEXT,
    issue_type TEXT NOT NULL CHECK (issue_type IN ('duplicate_student', 'ambiguous_class', 'missing_contact')),
    details TEXT NOT NULL,
    resolution_state TEXT NOT NULL DEFAULT 'open' CHECK (resolution_state IN ('open', 'merged', 'created_new', 'skipped')),
    resolved_by TEXT REFERENCES users(id),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    teacher_id TEXT NOT NULL REFERENCES users(id),
    source_document_id TEXT REFERENCES documents(id),
    title TEXT NOT NULL,
    subject TEXT NOT NULL,
    instructions TEXT NOT NULL,
    due_date TEXT,
    target_type TEXT NOT NULL CHECK (target_type IN ('class', 'group', 'individual')),
    target_class_id TEXT REFERENCES classes(id),
    constraints_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('draft', 'active', 'cancelled', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS student_assignments (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'in_progress', 'blocked', 'submitted', 'revision_requested', 'completed')),
    progress_note TEXT,
    blocker_note TEXT,
    last_reminder_at TEXT,
    reminder_count INTEGER NOT NULL DEFAULT 0,
    escalated_to_guardian INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(assignment_id, student_id)
);

CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    student_assignment_id TEXT NOT NULL REFERENCES student_assignments(id) ON DELETE CASCADE,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    submission_type TEXT NOT NULL CHECK (submission_type IN ('text', 'file', 'photo', 'voice_transcript')),
    content_text TEXT NOT NULL,
    document_id TEXT REFERENCES documents(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    student_assignment_id TEXT NOT NULL REFERENCES student_assignments(id) ON DELETE CASCADE,
    submission_id TEXT REFERENCES submissions(id),
    teacher_id TEXT NOT NULL REFERENCES users(id),
    decision TEXT NOT NULL CHECK (decision IN ('comment', 'revision_requested', 'completed')),
    comment TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    student_assignment_id TEXT NOT NULL REFERENCES student_assignments(id) ON DELETE CASCADE,
    recipient_user_id TEXT NOT NULL REFERENCES users(id),
    recipient_role TEXT NOT NULL,
    channel TEXT NOT NULL,
    reminder_kind TEXT NOT NULL CHECK (reminder_kind IN ('nudge_silent', 'help_blocked', 'guardian_escalation', 'skipped_submitted', 'deferred_quiet_hours')),
    message TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('sent', 'deferred', 'skipped', 'failed')),
    idempotency_key TEXT NOT NULL UNIQUE,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    school_id TEXT,
    actor_user_id TEXT,
    actor_role TEXT,
    event_type TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'denied', 'failed', 'info')),
    correlation_id TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_spans (
    id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    school_id TEXT,
    channel TEXT NOT NULL,
    intent TEXT NOT NULL,
    action TEXT NOT NULL,
    notification TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_school_time ON audit_events(school_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_corr ON audit_events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_sa_school_status ON student_assignments(school_id, status);
CREATE INDEX IF NOT EXISTS idx_docs_school_state ON documents(school_id, approval_state);
"""


def get_db_path() -> Path:
    db_path = settings.database_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def init_db() -> None:
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
