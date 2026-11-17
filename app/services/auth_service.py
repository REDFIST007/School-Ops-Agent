import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.audit import record_audit_event, utc_now_iso
from app.config import settings
from app.security import (
    generate_id,
    generate_invite_code,
    hash_password,
    hash_token,
    verify_password,
)


class AuthError(ValueError):
    pass


class PermissionDeniedError(PermissionError):
    pass


def _future_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def register_school(
    conn: sqlite3.Connection,
    *,
    school_name: str,
    admin_name: str,
    admin_email: str,
    password: str,
    tz: str = "Asia/Kolkata",
) -> Dict[str, Any]:
    existing = conn.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (admin_email.strip(),)).fetchone()
    if existing:
        raise AuthError("An account with this email already exists.")

    school_id = generate_id("sch")
    admin_id = generate_id("usr")
    now = utc_now_iso()

    conn.execute(
        """
        INSERT INTO schools (id, name, timezone, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (school_id, school_name.strip(), tz, now),
    )
    conn.execute(
        """
        INSERT INTO users (id, school_id, email, full_name, role, password_hash, created_at)
        VALUES (?, ?, ?, ?, 'admin', ?, ?)
        """,
        (admin_id, school_id, admin_email.strip().lower(), admin_name.strip(), hash_password(password), now),
    )

    record_audit_event(
        conn,
        school_id=school_id,
        actor_user_id=admin_id,
        actor_role="admin",
        event_type="school_registered",
        resource_type="school",
        resource_id=school_id,
        outcome="success",
        details={"school_name": school_name.strip()},
    )
    raw_token = create_session(conn, user_id=admin_id, school_id=school_id)
    return {
        "school_id": school_id,
        "school_name": school_name.strip(),
        "user_id": admin_id,
        "role": "admin",
        "session_token": raw_token,
    }


def create_session(conn: sqlite3.Connection, *, user_id: str, school_id: str) -> str:
    raw_token = secrets.token_urlsafe(32)
    session_id = generate_id("ses")
    now = utc_now_iso()
    expires = _future_iso(settings.session_ttl_hours)
    conn.execute(
        """
        INSERT INTO sessions (id, user_id, school_id, token_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, user_id, school_id, hash_token(raw_token), expires, now),
    )
    return raw_token


def login_user(conn: sqlite3.Connection, *, email: str, password: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT id, school_id, email, full_name, role, password_hash, is_active FROM users WHERE lower(email) = lower(?)",
        (email.strip(),),
    ).fetchone()
    if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
        record_audit_event(
            conn,
            school_id=row["school_id"] if row else None,
            actor_user_id=row["id"] if row else None,
            actor_role=row["role"] if row else None,
            event_type="login_failed",
            resource_type="session",
            outcome="denied",
            details={"reason": "invalid_credentials"},
        )
        raise AuthError("Invalid email or password.")

    token = create_session(conn, user_id=row["id"], school_id=row["school_id"])
    record_audit_event(
        conn,
        school_id=row["school_id"],
        actor_user_id=row["id"],
        actor_role=row["role"],
        event_type="user_logged_in",
        resource_type="session",
        outcome="success",
    )
    return {
        "user_id": row["id"],
        "school_id": row["school_id"],
        "role": row["role"],
        "full_name": row["full_name"],
        "session_token": token,
    }


def get_user_by_session(conn: sqlite3.Connection, raw_token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw_token:
        return None
    row = conn.execute(
        """
        SELECT s.id AS session_id, s.expires_at, s.revoked_at,
               u.id AS user_id, u.school_id, u.email, u.full_name, u.role, u.is_active, u.guardian_opt_in,
               sc.name AS school_name
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        JOIN schools sc ON sc.id = u.school_id
        WHERE s.token_hash = ?
        """,
        (hash_token(raw_token),),
    ).fetchone()
    if not row or row["revoked_at"] is not None or not row["is_active"]:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return None
    return dict(row)


def logout_session(conn: sqlite3.Connection, raw_token: Optional[str]) -> None:
    if not raw_token:
        return
    conn.execute(
        "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
        (utc_now_iso(), hash_token(raw_token)),
    )


def create_invite(
    conn: sqlite3.Connection,
    *,
    actor: Dict[str, Any],
    role: str,
    invitee_name: str,
    invitee_email: Optional[str] = None,
    class_id: Optional[str] = None,
    linked_student_id: Optional[str] = None,
) -> Dict[str, Any]:
    if actor["role"] not in {"admin", "teacher"}:
        raise PermissionDeniedError("Only admins or teachers can issue invites.")
    if actor["role"] == "teacher" and role == "teacher":
        raise PermissionDeniedError("Only school admins can invite teachers.")

    school_id = actor["school_id"]

    # Cross-school / cross-class validation
    if class_id:
        cls = conn.execute("SELECT id, school_id FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not cls or cls["school_id"] != school_id:
            record_audit_event(
                conn,
                school_id=school_id,
                actor_user_id=actor["user_id"],
                actor_role=actor["role"],
                event_type="access_denied",
                resource_type="class",
                resource_id=class_id,
                outcome="denied",
                details={"reason": "cross_school_invite_blocked"},
            )
            raise PermissionDeniedError("Class does not belong to your school.")
        if actor["role"] == "teacher":
            assigned = conn.execute(
                "SELECT 1 FROM teacher_classes WHERE teacher_id = ? AND class_id = ?",
                (actor["user_id"], class_id),
            ).fetchone()
            if not assigned:
                raise PermissionDeniedError("Teachers can only invite to their assigned classes.")

    if role == "student" and not class_id:
        raise AuthError("Student invites must specify a valid class_id.")

    if role == "guardian":
        if not linked_student_id:
            raise AuthError("Guardian invites must specify linked_student_id.")
        stu = conn.execute(
            "SELECT id, school_id, role FROM users WHERE id = ?", (linked_student_id,)
        ).fetchone()
        if not stu or stu["school_id"] != school_id or stu["role"] != "student":
            record_audit_event(
                conn,
                school_id=school_id,
                actor_user_id=actor["user_id"],
                actor_role=actor["role"],
                event_type="access_denied",
                resource_type="student",
                resource_id=linked_student_id,
                outcome="denied",
                details={"reason": "cross_school_guardian_invite_blocked"},
            )
            raise PermissionDeniedError("Linked student does not belong to your school.")

    code = generate_invite_code()
    expires_at = _future_iso(settings.invite_ttl_hours)
    conn.execute(
        """
        INSERT INTO invites (
            code, school_id, role, class_id, linked_student_id,
            invitee_name, invitee_email, created_by, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code,
            school_id,
            role,
            class_id,
            linked_student_id,
            invitee_name.strip(),
            invitee_email.strip().lower() if invitee_email else None,
            actor["user_id"],
            expires_at,
        ),
    )

    record_audit_event(
        conn,
        school_id=school_id,
        actor_user_id=actor["user_id"],
        actor_role=actor["role"],
        event_type="invite_created",
        resource_type="invite",
        resource_id=code,
        outcome="success",
        details={"target_role": role, "class_id": class_id, "linked_student_id": linked_student_id},
    )
    return {
        "code": code,
        "role": role,
        "school_id": school_id,
        "class_id": class_id,
        "linked_student_id": linked_student_id,
        "expires_at": expires_at,
    }


def accept_invite(
    conn: sqlite3.Connection,
    *,
    code: str,
    email: str,
    password: str,
    full_name: Optional[str] = None,
) -> Dict[str, Any]:
    inv = conn.execute("SELECT * FROM invites WHERE code = ?", (code.strip().upper(),)).fetchone()
    if not inv:
        raise AuthError("Invite code not found.")
    if inv["used_at"] is not None:
        raise AuthError("Invite code has already been used.")
    if datetime.fromisoformat(inv["expires_at"]) < datetime.now(timezone.utc):
        raise AuthError("Invite code has expired.")
    if inv["invitee_email"] and inv["invitee_email"].lower() != email.strip().lower():
        raise PermissionDeniedError("Email does not match the scoped invite.")

    existing = conn.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (email.strip(),)).fetchone()
    if existing:
        raise AuthError("An account with this email already exists.")

    user_id = generate_id("usr")
    now = utc_now_iso()
    name_to_use = (full_name or inv["invitee_name"]).strip()

    conn.execute(
        """
        INSERT INTO users (id, school_id, email, full_name, role, password_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, inv["school_id"], email.strip().lower(), name_to_use, inv["role"], hash_password(password), now),
    )

    if inv["role"] == "teacher" and inv["class_id"]:
        conn.execute(
            "INSERT OR IGNORE INTO teacher_classes (teacher_id, class_id) VALUES (?, ?)",
            (user_id, inv["class_id"]),
        )
    elif inv["role"] == "student" and inv["class_id"]:
        conn.execute(
            "INSERT INTO student_enrollments (student_id, school_id, class_id) VALUES (?, ?, ?)",
            (user_id, inv["school_id"], inv["class_id"]),
        )
    elif inv["role"] == "guardian" and inv["linked_student_id"]:
        conn.execute(
            "INSERT OR IGNORE INTO student_guardians (student_id, guardian_id) VALUES (?, ?)",
            (inv["linked_student_id"], user_id),
        )

    conn.execute(
        "UPDATE invites SET used_at = ?, used_by = ? WHERE code = ?",
        (now, user_id, inv["code"]),
    )

    token = create_session(conn, user_id=user_id, school_id=inv["school_id"])
    record_audit_event(
        conn,
        school_id=inv["school_id"],
        actor_user_id=user_id,
        actor_role=inv["role"],
        event_type="invite_accepted",
        resource_type="invite",
        resource_id=inv["code"],
        outcome="success",
        details={"role": inv["role"], "class_id": inv["class_id"]},
    )
    return {
        "user_id": user_id,
        "school_id": inv["school_id"],
        "role": inv["role"],
        "full_name": name_to_use,
        "session_token": token,
    }


def link_chat_identity(
    conn: sqlite3.Connection,
    *,
    channel: str,
    external_user_id: str,
    user: Optional[Dict[str, Any]] = None,
    invite_code: Optional[str] = None,
) -> Dict[str, Any]:
    target_user = user
    if not target_user and invite_code:
        inv = conn.execute("SELECT * FROM invites WHERE code = ?", (invite_code.strip().upper(),)).fetchone()
        if not inv:
            raise AuthError("Invalid invite code for chat linking.")
        if inv["used_by"]:
            row = conn.execute("SELECT id AS user_id, school_id, role, full_name FROM users WHERE id = ?", (inv["used_by"],)).fetchone()
            target_user = dict(row) if row else None
        else:
            if datetime.fromisoformat(inv["expires_at"]) < datetime.now(timezone.utc):
                raise AuthError("Invite code has expired.")
            # Auto-provision scoped chat-only account from invite
            uid = generate_id("usr")
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO users (id, school_id, email, full_name, role, created_at) VALUES (?, ?, NULL, ?, ?, ?)",
                (uid, inv["school_id"], inv["invitee_name"], inv["role"], now),
            )
            if inv["role"] == "teacher" and inv["class_id"]:
                conn.execute("INSERT OR IGNORE INTO teacher_classes (teacher_id, class_id) VALUES (?, ?)", (uid, inv["class_id"]))
            elif inv["role"] == "student" and inv["class_id"]:
                conn.execute("INSERT INTO student_enrollments (student_id, school_id, class_id) VALUES (?, ?, ?)", (uid, inv["school_id"], inv["class_id"]))
            elif inv["role"] == "guardian" and inv["linked_student_id"]:
                conn.execute("INSERT OR IGNORE INTO student_guardians (student_id, guardian_id) VALUES (?, ?)", (inv["linked_student_id"], uid))
            conn.execute("UPDATE invites SET used_at = ?, used_by = ? WHERE code = ?", (now, uid, inv["code"]))
            target_user = {"user_id": uid, "school_id": inv["school_id"], "role": inv["role"], "full_name": inv["invitee_name"]}

    if not target_user:
        raise AuthError("Must be logged in or provide a valid invite code to link chat identity.")

    existing = conn.execute(
        "SELECT user_id, school_id FROM chat_identities WHERE channel = ? AND external_user_id = ?",
        (channel, external_user_id),
    ).fetchone()
    if existing and existing["user_id"] != target_user["user_id"]:
        raise PermissionDeniedError("This chat identity is already linked to another account.")

    if not existing:
        conn.execute(
            """
            INSERT INTO chat_identities (id, user_id, school_id, channel, external_user_id, linked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (generate_id("cid"), target_user["user_id"], target_user["school_id"], channel, external_user_id, utc_now_iso()),
        )

    record_audit_event(
        conn,
        school_id=target_user["school_id"],
        actor_user_id=target_user["user_id"],
        actor_role=target_user["role"],
        event_type="chat_identity_linked",
        resource_type="chat_identity",
        resource_id=f"{channel}:{external_user_id}",
        outcome="success",
    )
    return {
        "user_id": target_user["user_id"],
        "school_id": target_user["school_id"],
        "role": target_user["role"],
        "channel": channel,
        "external_user_id": external_user_id,
    }


def resolve_chat_user(conn: sqlite3.Connection, channel: str, external_user_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT u.id AS user_id, u.school_id, u.full_name, u.role, u.is_active, u.guardian_opt_in
        FROM chat_identities c
        JOIN users u ON u.id = c.user_id
        WHERE c.channel = ? AND c.external_user_id = ? AND u.is_active = 1
        """,
        (channel, external_user_id),
    ).fetchone()
    return dict(row) if row else None


def require_school_scope(conn: sqlite3.Connection, actor: Dict[str, Any], target_school_id: str, resource_type: str, resource_id: str) -> None:
    if actor["school_id"] != target_school_id:
        record_audit_event(
            conn,
            school_id=actor["school_id"],
            actor_user_id=actor["user_id"],
            actor_role=actor["role"],
            event_type="access_denied",
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="denied",
            details={"reason": "cross_school_access_attempt", "target_school_id": target_school_id},
        )
        raise PermissionDeniedError("Access denied: resource belongs to a different school.")


def require_teacher_class_scope(conn: sqlite3.Connection, actor: Dict[str, Any], class_id: str) -> None:
    if actor["role"] == "admin":
        cls = conn.execute("SELECT school_id FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not cls:
            raise AuthError("Class not found.")
        require_school_scope(conn, actor, cls["school_id"], "class", class_id)
        return
    if actor["role"] != "teacher":
        record_audit_event(
            conn,
            school_id=actor["school_id"],
            actor_user_id=actor["user_id"],
            actor_role=actor["role"],
            event_type="access_denied",
            resource_type="class",
            resource_id=class_id,
            outcome="denied",
            details={"reason": "role_not_authorized_for_class"},
        )
        raise PermissionDeniedError("Access denied: teacher or admin role required.")
    assigned = conn.execute(
        """
        SELECT c.school_id FROM teacher_classes tc
        JOIN classes c ON c.id = tc.class_id
        WHERE tc.teacher_id = ? AND tc.class_id = ?
        """,
        (actor["user_id"], class_id),
    ).fetchone()
    if not assigned or assigned["school_id"] != actor["school_id"]:
        record_audit_event(
            conn,
            school_id=actor["school_id"],
            actor_user_id=actor["user_id"],
            actor_role=actor["role"],
            event_type="access_denied",
            resource_type="class",
            resource_id=class_id,
            outcome="denied",
            details={"reason": "teacher_not_assigned_to_class"},
        )
        raise PermissionDeniedError("Access denied: you are not assigned to this class.")
