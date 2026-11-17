from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import get_conn
from app.models import (
    CreateAssignmentRequest,
    ProgressUpdateRequest,
    SubmissionRequest,
    TeacherFeedbackRequest,
)
from app.routers.deps import require_user
from app.services.assignment_service import (
    create_assignment,
    give_teacher_feedback,
    submit_work,
    update_student_progress,
)
from app.services.auth_service import PermissionDeniedError
from app.state_machine import InvalidStateTransitionError

router = APIRouter(prefix="/api/assignments", tags=["assignments"])


@router.post("", status_code=status.HTTP_201_CREATED)
def api_create_assignment(
    payload: CreateAssignmentRequest, user: Dict[str, Any] = Depends(require_user)
) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            return create_assignment(
                conn,
                actor=user,
                title=payload.title,
                subject=payload.subject,
                instructions=payload.instructions,
                due_date=payload.due_date,
                target_type=payload.target_type,
                target_class_id=payload.target_class_id,
                target_student_ids=payload.target_student_ids,
                idempotency_key=payload.idempotency_key,
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{student_assignment_id}/progress")
def api_update_progress(
    student_assignment_id: str,
    payload: ProgressUpdateRequest,
    user: Dict[str, Any] = Depends(require_user),
) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            return update_student_progress(
                conn,
                actor=user,
                student_assignment_id=student_assignment_id,
                status=payload.status,
                note=payload.note,
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except (ValueError, InvalidStateTransitionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{student_assignment_id}/submit", status_code=status.HTTP_201_CREATED)
def api_submit_work(
    student_assignment_id: str,
    payload: SubmissionRequest,
    user: Dict[str, Any] = Depends(require_user),
) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            return submit_work(
                conn,
                actor=user,
                student_assignment_id=student_assignment_id,
                submission_type=payload.submission_type,
                content_text=payload.content_text,
                idempotency_key=payload.idempotency_key,
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except (ValueError, InvalidStateTransitionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{student_assignment_id}/feedback", status_code=status.HTTP_201_CREATED)
def api_give_feedback(
    student_assignment_id: str,
    payload: TeacherFeedbackRequest,
    user: Dict[str, Any] = Depends(require_user),
) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            return give_teacher_feedback(
                conn,
                actor=user,
                student_assignment_id=student_assignment_id,
                decision=payload.decision,
                comment=payload.comment,
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except (ValueError, InvalidStateTransitionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
