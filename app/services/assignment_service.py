import json
import sqlite3
from typing import Any, Dict, List, Optional

from app.audit import record_audit_event, utc_now_iso
from app.security import generate_id
from app.services.auth_service import PermissionDeniedError, require_school_scope, require_teacher_class_scope
from app.sse import broker
from app.state_machine import (
    ASSIGNMENT_TRANSITIONS,
    STUDENT_ASSIGNMENT_TRANSITIONS,
    validate_transition,
)


def create_assignment(
    conn: sqlite3.Connection,
    *,
    actor: Dict[str, Any],
    title: str,
    subject: str,
    instructions: str,
    due_date: Optional[str] = None,
    target_type: str = "class",
    target_class_id: Optional[str] = None,
    target_student_ids: Optional[List[str]] = None,
    source_document_id: Optional[str] = None,
    constraints: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    school_id = actor["school_id"]
    if idempotency_key:
        cached = conn.execute(
            "SELECT response_json FROM idempotency_keys WHERE key = ? AND scope = 'create_assignment'",
            (idempotency_key,),
        ).fetchone()
        if cached:
            return json.loads(cached["response_json"])

    if actor["role"] not in {"teacher", "admin"}:
        record_audit_event(
            conn,
            school_id=school_id,
            actor_user_id=actor["user_id"],
            actor_role=actor["role"],
            event_type="access_denied",
            resource_type="assignment",
            outcome="denied",
            details={"reason": "only_teacher_or_admin_can_create_assignment"},
        )
        raise PermissionDeniedError("Only teachers or admins can create assignments.")

    student_ids: List[str] = []
    if target_type == "class":
        if not target_class_id:
            raise ValueError("target_class_id is required for class-targeted assignments.")
        require_teacher_class_scope(conn, actor, target_class_id)
        rows = conn.execute(
            "SELECT student_id FROM student_enrollments WHERE school_id = ? AND class_id = ?",
            (school_id, target_class_id),
        ).fetchall()
        student_ids = [r["student_id"] for r in rows]
    else:
        req_ids = target_student_ids or []
        if not req_ids:
            raise ValueError("target_student_ids is required for group or individual assignments.")
        for sid in req_ids:
            stu = conn.execute(
                "SELECT student_id, school_id, class_id FROM student_enrollments WHERE student_id = ?",
                (sid,),
            ).fetchone()
            if not stu or stu["school_id"] != school_id:
                raise PermissionDeniedError("Cannot assign to a student outside your school.")
            if actor["role"] == "teacher":
                require_teacher_class_scope(conn, actor, stu["class_id"])
            student_ids.append(sid)

    assignment_id = generate_id("asg")
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO assignments (
            id, school_id, teacher_id, source_document_id, title, subject,
            instructions, due_date, target_type, target_class_id, constraints_json,
            state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            assignment_id,
            school_id,
            actor["user_id"],
            source_document_id,
            title.strip(),
            subject.strip(),
            instructions.strip(),
            due_date,
            target_type,
            target_class_id,
            json.dumps(constraints or {}),
            now,
            now,
        ),
    )

    for sid in student_ids:
        sa_id = generate_id("sas")
        conn.execute(
            """
            INSERT INTO student_assignments (id, assignment_id, student_id, school_id, status, updated_at)
            VALUES (?, ?, ?, ?, 'assigned', ?)
            """,
            (sa_id, assignment_id, sid, school_id, now),
        )

    record_audit_event(
        conn,
        school_id=school_id,
        actor_user_id=actor["user_id"],
        actor_role=actor["role"],
        event_type="assignment_created",
        resource_type="assignment",
        resource_id=assignment_id,
        outcome="success",
        details={"title": title, "target_type": target_type, "student_count": len(student_ids)},
    )
    broker.publish_sync(
        school_id,
        "assignment_created",
        {"assignment_id": assignment_id, "title": title, "student_count": len(student_ids)},
    )
    result = {
        "assignment_id": assignment_id,
        "title": title,
        "subject": subject,
        "due_date": due_date,
        "target_type": target_type,
        "student_count": len(student_ids),
        "state": "active",
    }
    if idempotency_key:
        conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys (key, scope, response_json, created_at) VALUES (?, 'create_assignment', ?, ?)",
            (idempotency_key, json.dumps(result), now),
        )
    return result


def update_student_progress(
    conn: sqlite3.Connection,
    *,
    actor: Dict[str, Any],
    student_assignment_id: str,
    status: str,
    note: str = "",
) -> Dict[str, Any]:
    sa = conn.execute("SELECT * FROM student_assignments WHERE id = ?", (student_assignment_id,)).fetchone()
    if not sa:
        raise ValueError("Student assignment record not found.")
    require_school_scope(conn, actor, sa["school_id"], "student_assignment", student_assignment_id)
    if actor["role"] != "student" or sa["student_id"] != actor["user_id"]:
        record_audit_event(
            conn,
            school_id=actor["school_id"],
            actor_user_id=actor["user_id"],
            actor_role=actor["role"],
            event_type="access_denied",
            resource_type="student_assignment",
            resource_id=student_assignment_id,
            outcome="denied",
            details={"reason": "wrong_student_context"},
        )
        raise PermissionDeniedError("Students can only update their own assignment progress.")

    validate_transition(STUDENT_ASSIGNMENT_TRANSITIONS, sa["status"], status, "student_assignment")
    now = utc_now_iso()
    progress_note = note if status == "in_progress" else sa["progress_note"]
    blocker_note = note if status == "blocked" else sa["blocker_note"]

    conn.execute(
        """
        UPDATE student_assignments
        SET status = ?, progress_note = ?, blocker_note = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, progress_note, blocker_note, now, student_assignment_id),
    )

    event_type = "student_blocked" if status == "blocked" else "student_progress_updated"
    record_audit_event(
        conn,
        school_id=sa["school_id"],
        actor_user_id=actor["user_id"],
        actor_role=actor["role"],
        event_type=event_type,
        resource_type="student_assignment",
        resource_id=student_assignment_id,
        outcome="success",
        details={"from_status": sa["status"], "to_status": status, "note": note[:200]},
    )
    broker.publish_sync(
        sa["school_id"],
        event_type,
        {"student_assignment_id": student_assignment_id, "student_id": actor["user_id"], "status": status, "note": note[:120]},
    )
    return {"student_assignment_id": student_assignment_id, "status": status, "note": note}


def submit_work(
    conn: sqlite3.Connection,
    *,
    actor: Dict[str, Any],
    student_assignment_id: str,
    submission_type: str,
    content_text: str,
    document_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    if idempotency_key:
        cached = conn.execute(
            "SELECT response_json FROM idempotency_keys WHERE key = ? AND scope = 'submit_work'",
            (idempotency_key,),
        ).fetchone()
        if cached:
            return json.loads(cached["response_json"])

    sa = conn.execute("SELECT * FROM student_assignments WHERE id = ?", (student_assignment_id,)).fetchone()
    if not sa:
        raise ValueError("Student assignment record not found.")
    require_school_scope(conn, actor, sa["school_id"], "student_assignment", student_assignment_id)
    if actor["role"] != "student" or sa["student_id"] != actor["user_id"]:
        record_audit_event(
            conn,
            school_id=actor["school_id"],
            actor_user_id=actor["user_id"],
            actor_role=actor["role"],
            event_type="access_denied",
            resource_type="student_assignment",
            resource_id=student_assignment_id,
            outcome="denied",
            details={"reason": "cannot_submit_for_another_student"},
        )
        raise PermissionDeniedError("You can only submit work for your own assignments.")

    validate_transition(STUDENT_ASSIGNMENT_TRANSITIONS, sa["status"], "submitted", "student_assignment")

    prev_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM submissions WHERE student_assignment_id = ?",
        (student_assignment_id,),
    ).fetchone()["cnt"]
    version = prev_count + 1
    sub_id = generate_id("sub")
    now = utc_now_iso()

    conn.execute(
        """
        INSERT INTO submissions (
            id, student_assignment_id, assignment_id, student_id, school_id,
            version, submission_type, content_text, document_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sub_id,
            student_assignment_id,
            sa["assignment_id"],
            actor["user_id"],
            sa["school_id"],
            version,
            submission_type,
            content_text.strip(),
            document_id,
            now,
        ),
    )
    conn.execute(
        "UPDATE student_assignments SET status = 'submitted', updated_at = ? WHERE id = ?",
        (now, student_assignment_id),
    )

    event_type = "work_resubmitted" if version > 1 else "work_submitted"
    record_audit_event(
        conn,
        school_id=sa["school_id"],
        actor_user_id=actor["user_id"],
        actor_role=actor["role"],
        event_type=event_type,
        resource_type="submission",
        resource_id=sub_id,
        outcome="success",
        details={"student_assignment_id": student_assignment_id, "version": version, "submission_type": submission_type},
    )
    broker.publish_sync(
        sa["school_id"],
        event_type,
        {"student_assignment_id": student_assignment_id, "submission_id": sub_id, "version": version},
    )
    res = {"submission_id": sub_id, "student_assignment_id": student_assignment_id, "version": version, "status": "submitted"}
    if idempotency_key:
        conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys (key, scope, response_json, created_at) VALUES (?, 'submit_work', ?, ?)",
            (idempotency_key, json.dumps(res), now),
        )
    return res


def give_teacher_feedback(
    conn: sqlite3.Connection,
    *,
    actor: Dict[str, Any],
    student_assignment_id: str,
    decision: str,
    comment: str,
) -> Dict[str, Any]:
    sa = conn.execute("SELECT * FROM student_assignments WHERE id = ?", (student_assignment_id,)).fetchone()
    if not sa:
        raise ValueError("Student assignment record not found.")
    require_school_scope(conn, actor, sa["school_id"], "student_assignment", student_assignment_id)

    if actor["role"] not in {"teacher", "admin"}:
        record_audit_event(
            conn,
            school_id=actor["school_id"],
            actor_user_id=actor["user_id"],
            actor_role=actor["role"],
            event_type="access_denied",
            resource_type="student_assignment",
            resource_id=student_assignment_id,
            outcome="denied",
            details={"reason": "only_teacher_can_give_feedback"},
        )
        raise PermissionDeniedError("Only teachers or admins can give assignment feedback.")

    if actor["role"] == "teacher":
        enr = conn.execute("SELECT class_id FROM student_enrollments WHERE student_id = ?", (sa["student_id"],)).fetchone()
        if enr:
            require_teacher_class_scope(conn, actor, enr["class_id"])

    if decision in {"revision_requested", "completed"}:
        validate_transition(STUDENT_ASSIGNMENT_TRANSITIONS, sa["status"], decision, "student_assignment")
        conn.execute(
            "UPDATE student_assignments SET status = ?, updated_at = ? WHERE id = ?",
            (decision, utc_now_iso(), student_assignment_id),
        )

    latest_sub = conn.execute(
        "SELECT id FROM submissions WHERE student_assignment_id = ? ORDER BY version DESC LIMIT 1",
        (student_assignment_id,),
    ).fetchone()

    fb_id = generate_id("fbk")
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO feedback (id, student_assignment_id, submission_id, teacher_id, decision, comment, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (fb_id, student_assignment_id, latest_sub["id"] if latest_sub else None, actor["user_id"], decision, comment.strip(), now),
    )

    record_audit_event(
        conn,
        school_id=sa["school_id"],
        actor_user_id=actor["user_id"],
        actor_role=actor["role"],
        event_type=f"teacher_{decision}",
        resource_type="feedback",
        resource_id=fb_id,
        outcome="success",
        details={"student_assignment_id": student_assignment_id, "decision": decision},
    )
    broker.publish_sync(
        sa["school_id"],
        "teacher_feedback",
        {"student_assignment_id": student_assignment_id, "decision": decision},
    )
    return {"feedback_id": fb_id, "student_assignment_id": student_assignment_id, "decision": decision}
