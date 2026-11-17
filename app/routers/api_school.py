import json
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, status

from app.audit import record_audit_event, utc_now_iso
from app.database import get_conn
from app.models import CreateClassRequest, PolicyUpdateRequest
from app.routers.deps import require_user
from app.security import generate_id

router = APIRouter(prefix="/api/schools", tags=["schools"])


@router.post("/classes", status_code=status.HTTP_201_CREATED)
def api_create_class(
    payload: CreateClassRequest, user: Dict[str, Any] = Depends(require_user)
) -> Dict[str, Any]:
    if user["role"] != "admin":
        with get_conn() as conn:
            record_audit_event(
                conn,
                school_id=user["school_id"],
                actor_user_id=user["user_id"],
                actor_role=user["role"],
                event_type="access_denied",
                resource_type="class",
                outcome="denied",
                details={"reason": "only_admin_can_create_classes"},
            )
        raise HTTPException(status_code=403, detail="Only school admins can create classes.")

    cls_id = generate_id("cls")
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO classes (id, school_id, name, grade, created_at) VALUES (?, ?, ?, ?, ?)",
                (cls_id, user["school_id"], payload.name.strip(), payload.grade.strip(), utc_now_iso()),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not create class: {exc}")
        record_audit_event(
            conn,
            school_id=user["school_id"],
            actor_user_id=user["user_id"],
            actor_role="admin",
            event_type="class_created",
            resource_type="class",
            resource_id=cls_id,
            outcome="success",
            details={"name": payload.name, "grade": payload.grade},
        )
    return {"id": cls_id, "name": payload.name.strip(), "grade": payload.grade.strip()}


@router.get("/classes")
def api_list_classes(user: Dict[str, Any] = Depends(require_user)) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if user["role"] == "teacher":
            rows = conn.execute(
                """
                SELECT c.* FROM classes c
                JOIN teacher_classes tc ON tc.class_id = c.id
                WHERE tc.teacher_id = ? AND c.school_id = ?
                """,
                (user["user_id"], user["school_id"]),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM classes WHERE school_id = ?", (user["school_id"],)).fetchall()
        return [dict(r) for r in rows]


@router.post("/assign-teacher")
def api_assign_teacher(
    teacher_id: str, class_id: str, user: Dict[str, Any] = Depends(require_user)
) -> Dict[str, str]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only school admins can assign teachers.")
    with get_conn() as conn:
        t = conn.execute(
            "SELECT id FROM users WHERE id = ? AND school_id = ? AND role = 'teacher'",
            (teacher_id, user["school_id"]),
        ).fetchone()
        c = conn.execute(
            "SELECT id FROM classes WHERE id = ? AND school_id = ?", (class_id, user["school_id"])
        ).fetchone()
        if not t or not c:
            raise HTTPException(status_code=404, detail="Teacher or class not found in your school.")
        conn.execute("INSERT OR IGNORE INTO teacher_classes (teacher_id, class_id) VALUES (?, ?)", (teacher_id, class_id))
        record_audit_event(
            conn,
            school_id=user["school_id"],
            actor_user_id=user["user_id"],
            actor_role="admin",
            event_type="teacher_assigned_to_class",
            resource_type="class",
            resource_id=class_id,
            outcome="success",
            details={"teacher_id": teacher_id},
        )
    return {"status": "assigned", "teacher_id": teacher_id, "class_id": class_id}


@router.put("/policy")
def api_update_policy(
    payload: PolicyUpdateRequest, user: Dict[str, Any] = Depends(require_user)
) -> Dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only school admins can update school policy.")
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE schools
            SET quiet_hours_start = ?, quiet_hours_end = ?, escalation_hours = ?,
                allowed_channels = ?, require_teacher_approval = ?
            WHERE id = ?
            """,
            (
                payload.quiet_hours_start,
                payload.quiet_hours_end,
                payload.escalation_hours,
                json.dumps(payload.allowed_channels),
                1 if payload.require_teacher_approval else 0,
                user["school_id"],
            ),
        )
        record_audit_event(
            conn,
            school_id=user["school_id"],
            actor_user_id=user["user_id"],
            actor_role="admin",
            event_type="school_policy_updated",
            resource_type="school",
            resource_id=user["school_id"],
            outcome="success",
            details=payload.dict(),
        )
    return {"status": "updated", **payload.dict()}
