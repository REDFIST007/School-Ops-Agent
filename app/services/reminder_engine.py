import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.audit import get_correlation_id, record_audit_event, utc_now_iso
from app.config import settings
from app.security import generate_id
from app.sse import broker


def is_in_quiet_hours(now_hhmm: str, start_hhmm: str, end_hhmm: str) -> bool:
    if start_hhmm == end_hhmm:
        return False
    if start_hhmm < end_hhmm:
        return start_hhmm <= now_hhmm < end_hhmm
    # Wraps midnight (e.g. 21:00 to 07:00)
    return now_hhmm >= start_hhmm or now_hhmm < end_hhmm


def run_reminders_for_school(
    conn: sqlite3.Connection,
    *,
    school_id: str,
    actor: Optional[Dict[str, Any]] = None,
    override_hhmm: Optional[str] = None,
    ignore_quiet_hours: bool = False,
) -> Dict[str, Any]:
    school = conn.execute("SELECT * FROM schools WHERE id = ?", (school_id,)).fetchone()
    if not school:
        raise ValueError("School not found.")

    cid = get_correlation_id()
    now_dt = datetime.now(timezone.utc)
    now_hhmm = override_hhmm or now_dt.strftime("%H:%M")
    date_bucket = now_dt.strftime("%Y-%m-%d")

    quiet_active = (
        settings.feature_quiet_hours_enforced
        and not ignore_quiet_hours
        and is_in_quiet_hours(now_hhmm, school["quiet_hours_start"], school["quiet_hours_end"])
    )

    rows = conn.execute(
        """
        SELECT sa.*, a.title AS assignment_title, a.due_date, u.full_name AS student_name
        FROM student_assignments sa
        JOIN assignments a ON a.id = sa.assignment_id
        JOIN users u ON u.id = sa.student_id
        WHERE sa.school_id = ? AND a.state = 'active'
        """,
        (school_id,),
    ).fetchall()

    results: List[Dict[str, Any]] = []
    counts = {"sent": 0, "deferred": 0, "skipped": 0, "deduplicated": 0}

    for r in rows:
        sa_id = r["id"]
        status = r["status"]

        if status in {"submitted", "completed"}:
            kind = "skipped_submitted"
            rem_status = "skipped"
            msg = f"Skipped reminder for {r['student_name']} on '{r['assignment_title']}' (already {status})."
            recipient_id = r["student_id"]
            recipient_role = "student"
        elif quiet_active:
            kind = "deferred_quiet_hours"
            rem_status = "deferred"
            msg = f"Deferred reminder during quiet hours ({school['quiet_hours_start']}-{school['quiet_hours_end']})."
            recipient_id = r["student_id"]
            recipient_role = "student"
        elif status == "blocked":
            kind = "help_blocked"
            rem_status = "sent"
            msg = f"Hi {r['student_name']}, your teacher was notified that you're blocked on '{r['assignment_title']}'. Reply with your specific question."
            recipient_id = r["student_id"]
            recipient_role = "student"
        else:
            kind = "nudge_silent"
            rem_status = "sent"
            msg = f"Reminder for {r['student_name']}: '{r['assignment_title']}' is due {r['due_date'] or 'soon'}. Reply with progress or 'blocked' if you need help."
            recipient_id = r["student_id"]
            recipient_role = "student"

        idem_key = f"rem:{sa_id}:{kind}:{date_bucket}"
        existing = conn.execute("SELECT id FROM reminders WHERE idempotency_key = ?", (idem_key,)).fetchone()
        if existing:
            counts["deduplicated"] += 1
            continue

        rem_id = generate_id("rem")
        now_iso = utc_now_iso()
        conn.execute(
            """
            INSERT INTO reminders (
                id, school_id, student_assignment_id, recipient_user_id, recipient_role,
                channel, reminder_kind, message, status, idempotency_key, correlation_id, created_at
            ) VALUES (?, ?, ?, ?, ?, 'web', ?, ?, ?, ?, ?, ?)
            """,
            (rem_id, school_id, sa_id, recipient_id, recipient_role, kind, msg, rem_status, idem_key, cid, now_iso),
        )
        counts[rem_status] += 1
        results.append({"id": rem_id, "student": r["student_name"], "kind": kind, "status": rem_status, "message": msg})

        if rem_status == "sent":
            conn.execute(
                "UPDATE student_assignments SET last_reminder_at = ?, reminder_count = reminder_count + 1 WHERE id = ?",
                (now_iso, sa_id),
            )

        # Escalate blocked students to opted-in guardians if enabled and not quiet hours
        if status == "blocked" and not quiet_active and settings.feature_auto_escalate_blocked and not r["escalated_to_guardian"]:
            guardians = conn.execute(
                """
                SELECT u.id, u.full_name FROM student_guardians sg
                JOIN users u ON u.id = sg.guardian_id
                WHERE sg.student_id = ? AND u.guardian_opt_in = 1 AND u.is_active = 1
                """,
                (r["student_id"],),
            ).fetchall()
            for g in guardians:
                g_key = f"rem:{sa_id}:guardian_escalation:{g['id']}:{date_bucket}"
                if conn.execute("SELECT id FROM reminders WHERE idempotency_key = ?", (g_key,)).fetchone():
                    continue
                g_rem_id = generate_id("rem")
                g_msg = f"Guardian Alert: {r['student_name']} reported being blocked on '{r['assignment_title']}'."
                conn.execute(
                    """
                    INSERT INTO reminders (
                        id, school_id, student_assignment_id, recipient_user_id, recipient_role,
                        channel, reminder_kind, message, status, idempotency_key, correlation_id, created_at
                    ) VALUES (?, ?, ?, ?, 'guardian', 'web', 'guardian_escalation', ?, 'sent', ?, ?, ?)
                    """,
                    (g_rem_id, school_id, sa_id, g["id"], g_msg, g_key, cid, now_iso),
                )
                counts["sent"] += 1
                results.append({"id": g_rem_id, "student": r["student_name"], "kind": "guardian_escalation", "status": "sent", "message": g_msg})
            if guardians:
                conn.execute("UPDATE student_assignments SET escalated_to_guardian = 1 WHERE id = ?", (sa_id,))

    record_audit_event(
        conn,
        school_id=school_id,
        actor_user_id=actor["user_id"] if actor else None,
        actor_role=actor["role"] if actor else "system",
        event_type="reminders_executed",
        resource_type="school",
        resource_id=school_id,
        outcome="success",
        correlation_id=cid,
        details={"quiet_hours_active": quiet_active, "counts": counts},
    )
    broker.publish_sync(school_id, "reminders_executed", {"quiet_hours_active": quiet_active, "counts": counts})
    return {"quiet_hours_active": quiet_active, "counts": counts, "reminders": results}
