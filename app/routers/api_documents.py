import json
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.audit import record_audit_event, utc_now_iso
from app.database import get_conn
from app.models import ClarifyDocumentRequest, ResolveRosterConflictRequest
from app.routers.deps import require_user
from app.services.assignment_service import create_assignment
from app.services.document_parser import approve_document, clarify_document, ingest_document
from app.state_machine import InvalidStateTransitionError

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def api_upload_document(
    doc_type: str = Form(...),
    owner_type: str = Form("school"),
    owner_id: Optional[str] = Form(None),
    pasted_text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    user: Dict[str, Any] = Depends(require_user),
) -> Dict[str, Any]:
    if file is not None and file.filename:
        content = await file.read()
        filename = file.filename
    elif pasted_text:
        content = pasted_text.encode("utf-8")
        filename = f"pasted_{doc_type}.txt"
    else:
        raise HTTPException(status_code=400, detail="Provide either a file upload or pasted_text.")

    resolved_owner_id = owner_id or user["school_id"]
    with get_conn() as conn:
        try:
            return ingest_document(
                conn,
                actor=user,
                doc_type=doc_type,
                owner_type=owner_type,
                owner_id=resolved_owner_id,
                filename=filename,
                content_bytes=content,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{doc_id}/clarify")
def api_clarify_document(
    doc_id: str, payload: ClarifyDocumentRequest, user: Dict[str, Any] = Depends(require_user)
) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            return clarify_document(conn, actor=user, doc_id=doc_id, answers=payload.answers)
        except (ValueError, InvalidStateTransitionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{doc_id}/approve")
def api_approve_document(
    doc_id: str,
    create_assignment_flag: bool = True,
    target_class_id: Optional[str] = None,
    user: Dict[str, Any] = Depends(require_user),
) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            doc = approve_document(conn, actor=user, doc_id=doc_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except (ValueError, InvalidStateTransitionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        created_asg = None
        parsed = json.loads(doc["parsed_json"])
        if doc["doc_type"] == "assignment_brief" and create_assignment_flag:
            cls_id = target_class_id
            if not cls_id and parsed.get("target_ref"):
                c = conn.execute(
                    "SELECT id FROM classes WHERE school_id = ? AND lower(name) = lower(?)",
                    (user["school_id"], parsed["target_ref"]),
                ).fetchone()
                if c:
                    cls_id = c["id"]
            if cls_id:
                created_asg = create_assignment(
                    conn,
                    actor=user,
                    title=parsed.get("title") or "Untitled Assignment",
                    subject=parsed.get("subject") or "General",
                    instructions=parsed.get("instructions") or "See attached brief.",
                    due_date=parsed.get("due_date"),
                    target_type="class",
                    target_class_id=cls_id,
                    source_document_id=doc_id,
                )
        elif doc["doc_type"] == "school_policy":
            conn.execute(
                """
                UPDATE schools
                SET quiet_hours_start = ?, quiet_hours_end = ?, escalation_hours = ?,
                    allowed_channels = ?, require_teacher_approval = ?
                WHERE id = ?
                """,
                (
                    parsed.get("quiet_hours_start", "21:00"),
                    parsed.get("quiet_hours_end", "07:00"),
                    int(parsed.get("escalation_hours", 24)),
                    json.dumps(parsed.get("allowed_channels", ["telegram", "whatsapp", "web"])),
                    1 if parsed.get("require_teacher_approval", True) else 0,
                    user["school_id"],
                ),
            )
        return {"document": doc, "created_assignment": created_asg}


@router.post("/conflicts/{conflict_id}/resolve")
def api_resolve_roster_conflict(
    conflict_id: str,
    payload: ResolveRosterConflictRequest,
    user: Dict[str, Any] = Depends(require_user),
) -> Dict[str, Any]:
    if user["role"] not in {"admin", "teacher"}:
        raise HTTPException(status_code=403, detail="Only admins or teachers can resolve roster conflicts.")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM roster_conflicts WHERE id = ?", (conflict_id,)).fetchone()
        if not row or row["school_id"] != user["school_id"]:
            raise HTTPException(status_code=404, detail="Conflict not found in your school.")
        now = utc_now_iso()
        conn.execute(
            "UPDATE roster_conflicts SET resolution_state = ?, resolved_by = ?, resolved_at = ? WHERE id = ?",
            (payload.resolution, user["user_id"], now, conflict_id),
        )
        record_audit_event(
            conn,
            school_id=user["school_id"],
            actor_user_id=user["user_id"],
            actor_role=user["role"],
            event_type="roster_conflict_resolved",
            resource_type="roster_conflict",
            resource_id=conflict_id,
            outcome="success",
            details={"resolution": payload.resolution, "student_name": row["student_name"]},
        )
        return {"id": conflict_id, "resolution_state": payload.resolution}
