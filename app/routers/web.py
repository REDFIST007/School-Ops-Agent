import json
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.database import get_conn
from app.routers.deps import get_optional_user

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter(tags=["web"])


@router.get("/", response_class=HTMLResponse)
def home_page(request: Request, user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = user["role"]
    school_id = user["school_id"]

    with get_conn() as conn:
        school = dict(conn.execute("SELECT * FROM schools WHERE id = ?", (school_id,)).fetchone())
        if role == "admin":
            classes = [dict(r) for r in conn.execute("SELECT * FROM classes WHERE school_id = ?", (school_id,)).fetchall()]
            users = [dict(r) for r in conn.execute("SELECT id, full_name, role, is_active FROM users WHERE school_id = ?", (school_id,)).fetchall()]
            invites = [dict(r) for r in conn.execute("SELECT * FROM invites WHERE school_id = ? ORDER BY expires_at DESC", (school_id,)).fetchall()]
            docs = [dict(r) for r in conn.execute("SELECT id, doc_type, filename, approval_state, confidence, prompt_injection_flagged, created_at FROM documents WHERE school_id = ? ORDER BY created_at DESC", (school_id,)).fetchall()]
            return templates.TemplateResponse(
                "dashboard_admin.html",
                {"request": request, "user": user, "school": school, "classes": classes, "users": users, "invites": invites, "docs": docs},
            )

        if role == "teacher":
            classes = [
                dict(r)
                for r in conn.execute(
                    "SELECT c.* FROM classes c JOIN teacher_classes tc ON tc.class_id = c.id WHERE tc.teacher_id = ?",
                    (user["user_id"],),
                ).fetchall()
            ]
            student_work = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT sa.*, a.title AS assignment_title, a.due_date, u.full_name AS student_name
                    FROM student_assignments sa
                    JOIN assignments a ON a.id = sa.assignment_id
                    JOIN users u ON u.id = sa.student_id
                    WHERE sa.school_id = ? AND a.teacher_id = ?
                    ORDER BY sa.updated_at DESC
                    """,
                    (school_id, user["user_id"]),
                ).fetchall()
            ]
            docs = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, doc_type, filename, approval_state, confidence, prompt_injection_flagged FROM documents WHERE school_id = ? ORDER BY created_at DESC",
                    (school_id,),
                ).fetchall()
            ]
            return templates.TemplateResponse(
                "dashboard_teacher.html",
                {"request": request, "user": user, "school": school, "classes": classes, "student_work": student_work, "docs": docs},
            )

        if role == "student":
            my_work = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT sa.*, a.title AS assignment_title, a.subject, a.instructions, a.due_date
                    FROM student_assignments sa
                    JOIN assignments a ON a.id = sa.assignment_id
                    WHERE sa.student_id = ?
                    ORDER BY sa.updated_at DESC
                    """,
                    (user["user_id"],),
                ).fetchall()
            ]
            feedbacks = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT f.*, sa.assignment_id FROM feedback f
                    JOIN student_assignments sa ON sa.id = f.student_assignment_id
                    WHERE sa.student_id = ? ORDER BY f.created_at DESC
                    """,
                    (user["user_id"],),
                ).fetchall()
            ]
            return templates.TemplateResponse(
                "dashboard_student.html",
                {"request": request, "user": user, "school": school, "my_work": my_work, "feedbacks": feedbacks},
            )

        # guardian
        children = [
            dict(r)
            for r in conn.execute(
                """
                SELECT u.id AS student_id, u.full_name AS student_name, a.title AS assignment_title, sa.status, a.due_date
                FROM student_guardians sg
                JOIN users u ON u.id = sg.student_id
                LEFT JOIN student_assignments sa ON sa.student_id = u.id
                LEFT JOIN assignments a ON a.id = sa.assignment_id
                WHERE sg.guardian_id = ?
                """,
                (user["user_id"],),
            ).fetchall()
        ]
        reminders = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reminders WHERE recipient_user_id = ? ORDER BY created_at DESC LIMIT 20",
                (user["user_id"],),
            ).fetchall()
        ]
        return templates.TemplateResponse(
            "dashboard_guardian.html",
            {"request": request, "user": user, "school": school, "children": children, "reminders": reminders},
        )


@router.get("/documents/{doc_id}", response_class=HTMLResponse)
def document_review_page(doc_id: str, request: Request, user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    with get_conn() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ? AND school_id = ?", (doc_id, user["school_id"])).fetchone()
        if not doc:
            return HTMLResponse("Document not found or access denied.", status_code=404)
        conflicts = [dict(r) for r in conn.execute("SELECT * FROM roster_conflicts WHERE document_id = ?", (doc_id,)).fetchall()]
        classes = [dict(r) for r in conn.execute("SELECT * FROM classes WHERE school_id = ?", (user["school_id"],)).fetchall()]
        return templates.TemplateResponse(
            "document_review.html",
            {
                "request": request,
                "user": user,
                "doc": dict(doc),
                "parsed": json.loads(doc["parsed_json"]),
                "ambiguities": json.loads(doc["ambiguity_notes"]),
                "missing": json.loads(doc["missing_fields"]),
                "conflicts": conflicts,
                "classes": classes,
            },
        )


@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user["role"] not in {"admin", "teacher"}:
        return HTMLResponse("Access denied: Admin or Teacher role required.", status_code=403)
    with get_conn() as conn:
        events = [dict(r) for r in conn.execute("SELECT * FROM audit_events WHERE school_id = ? ORDER BY created_at DESC LIMIT 150", (user["school_id"],)).fetchall()]
        traces = [dict(r) for r in conn.execute("SELECT * FROM trace_spans WHERE school_id = ? ORDER BY created_at DESC LIMIT 100", (user["school_id"],)).fetchall()]
    return templates.TemplateResponse("audit_timeline.html", {"request": request, "user": user, "events": events, "traces": traces})


@router.get("/simulator", response_class=HTMLResponse)
def simulator_page(request: Request, user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    return templates.TemplateResponse("chat_simulator.html", {"request": request, "user": user})
