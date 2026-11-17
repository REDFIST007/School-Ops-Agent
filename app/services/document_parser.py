import csv
import io
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from docx import Document as DocxDocument
from pypdf import PdfReader

from app.audit import record_audit_event, utc_now_iso
from app.config import settings
from app.models import ParsedDocumentOutput
from app.security import generate_id, validate_upload
from app.sse import broker
from app.state_machine import DOCUMENT_APPROVAL_TRANSITIONS, validate_transition

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"disregard\s+(all\s+)?rules",
    r"system\s*:\s*",
    r"you\s+are\s+now\s+in\s+developer\s+mode",
    r"auto[-\s]?approve\s+all",
    r"bypass\s+(auth|security|approval|policy)",
    r"reveal\s+(all\s+)?(secrets|tokens|passwords|api[_\s]?keys)",
    r"drop\s+table",
    r"<script[^>]*>",
]


def detect_and_sanitize_injection(raw_text: str) -> Tuple[str, bool, List[str]]:
    reasons: List[str] = []
    clean_lines: List[str] = []
    for line in raw_text.splitlines():
        matched = False
        for pat in INJECTION_PATTERNS:
            if re.search(pat, line, flags=re.IGNORECASE):
                reasons.append(f"Matched suspicious pattern: {pat}")
                matched = True
                break
        if matched and settings.feature_strict_prompt_guard:
            clean_lines.append("[QUARANTINED_SUSPICIOUS_LINE]")
        else:
            clean_lines.append(line)
    return "\n".join(clean_lines), len(reasons) > 0, reasons


def extract_text_from_bytes(filename: str, content: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()
    if ext == ".docx":
        doc = DocxDocument(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs).strip()
    if ext in {".csv", ".txt"}:
        return content.decode("utf-8", errors="replace").strip()
    if ext in {".png", ".jpg", ".jpeg"}:
        return f"[IMAGE_UPLOAD:{filename}; size={len(content)} bytes; OCR_TEXT_UNAVAILABLE_IN_OFFLINE_MODE]"
    return content.decode("utf-8", errors="replace").strip()


def _parse_assignment_brief(text: str) -> ParsedDocumentOutput:
    fields: Dict[str, Any] = {
        "title": None,
        "subject": None,
        "instructions": None,
        "due_date": None,
        "target_type": "class",
        "target_ref": None,
        "constraints": [],
    }
    ambiguities: List[str] = []
    missing: List[str] = []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and "[QUARANTINED" not in ln]
    for ln in lines:
        m_title = re.match(r"^(?:title|assignment)\s*:\s*(.+)$", ln, re.I)
        if m_title and not fields["title"]:
            fields["title"] = m_title.group(1).strip()
            continue
        m_subj = re.match(r"^(?:subject|course)\s*:\s*(.+)$", ln, re.I)
        if m_subj and not fields["subject"]:
            fields["subject"] = m_subj.group(1).strip()
            continue
        m_due = re.match(r"^(?:due|due\s*date|deadline)\s*:\s*(.+)$", ln, re.I)
        if m_due and not fields["due_date"]:
            raw_due = m_due.group(1).strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}", raw_due):
                fields["due_date"] = raw_due
            else:
                fields["due_date"] = raw_due
                ambiguities.append(f"Due date '{raw_due}' is relative/unstandardized; please confirm exact YYYY-MM-DD.")
            continue
        m_target = re.match(r"^(?:target|class|grade|students?)\s*:\s*(.+)$", ln, re.I)
        if m_target and not fields["target_ref"]:
            fields["target_ref"] = m_target.group(1).strip()
            continue
        m_inst = re.match(r"^(?:instructions?|details?)\s*:\s*(.+)$", ln, re.I)
        if m_inst:
            fields["instructions"] = m_inst.group(1).strip()
            continue

    if not fields["title"] and lines:
        fields["title"] = lines[0][:120]
        ambiguities.append("Title inferred from first line; please confirm.")
    if not fields["instructions"]:
        body_lines = [ln for ln in lines if not re.match(r"^(title|assignment|subject|due|target|class)\s*:", ln, re.I)]
        if body_lines:
            fields["instructions"] = " ".join(body_lines)[:2000]
    if not fields["subject"]:
        missing.append("subject")
        ambiguities.append("Subject is missing. Which subject does this assignment belong to?")
    if not fields["due_date"]:
        missing.append("due_date")
        ambiguities.append("Due date is missing. Please specify a due date (YYYY-MM-DD).")
    if not fields["target_ref"]:
        missing.append("target_ref")
        ambiguities.append("Target class/students not specified. Which class or student should receive this?")

    if "photo" in text.lower() or "diagram" in text.lower():
        fields["constraints"].append("photo_or_diagram_accepted")
    if "no late" in text.lower():
        fields["constraints"].append("strict_deadline")

    confidence = max(0.35, min(0.96, 0.95 - 0.15 * len(missing) - 0.08 * len(ambiguities)))
    return ParsedDocumentOutput(
        doc_type="assignment_brief",
        structured_fields=fields,
        confidence=round(confidence, 2),
        ambiguity_notes=ambiguities,
        missing_fields=missing,
    )


def _parse_class_roster(text: str) -> ParsedDocumentOutput:
    rows: List[Dict[str, str]] = []
    ambiguities: List[str] = []
    missing: List[str] = []

    clean_text = "\n".join(ln for ln in text.splitlines() if "[QUARANTINED" not in ln)
    reader = csv.DictReader(io.StringIO(clean_text))
    if not reader.fieldnames:
        # Fallback: parse comma-separated lines without header
        for idx, ln in enumerate(clean_text.splitlines(), start=1):
            parts = [p.strip() for p in ln.split(",")]
            if len(parts) >= 2:
                rows.append(
                    {
                        "row_index": str(idx),
                        "student_name": parts[0],
                        "class_name": parts[1],
                        "guardian_contact": parts[2] if len(parts) > 2 else "",
                        "notes": parts[3] if len(parts) > 3 else "",
                    }
                )
    else:
        for idx, raw_row in enumerate(reader, start=1):
            norm = {k.strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
            rows.append(
                {
                    "row_index": str(idx),
                    "student_name": norm.get("student_name") or norm.get("name") or norm.get("student") or "",
                    "class_name": norm.get("class_name") or norm.get("class") or norm.get("grade") or "",
                    "guardian_contact": norm.get("guardian_contact") or norm.get("parent_contact") or norm.get("parent") or "",
                    "notes": norm.get("notes") or "",
                }
            )

    if not rows:
        missing.append("roster_rows")
        ambiguities.append("No valid roster rows detected in CSV.")

    return ParsedDocumentOutput(
        doc_type="class_roster",
        structured_fields={"rows": rows, "row_count": len(rows)},
        confidence=0.92 if rows else 0.3,
        ambiguity_notes=ambiguities,
        missing_fields=missing,
    )


def _parse_school_policy(text: str) -> ParsedDocumentOutput:
    fields: Dict[str, Any] = {
        "quiet_hours_start": "21:00",
        "quiet_hours_end": "07:00",
        "escalation_hours": 24,
        "allowed_channels": ["telegram", "whatsapp", "web"],
        "require_teacher_approval": True,
    }
    ambiguities: List[str] = []

    m_quiet = re.search(r"quiet\s*hours?\s*[:=]?\s*(\d{1,2}:\d{2})\s*(?:-|to)\s*(\d{1,2}:\d{2})", text, re.I)
    if m_quiet:
        fields["quiet_hours_start"] = m_quiet.group(1).zfill(5)
        fields["quiet_hours_end"] = m_quiet.group(2).zfill(5)
    else:
        ambiguities.append("Quiet hours not explicitly matched; defaulted to 21:00-07:00.")

    m_esc = re.search(r"escalat\w*\s*(?:after)?\s*(\d+)\s*h", text, re.I)
    if m_esc:
        fields["escalation_hours"] = int(m_esc.group(1))

    channels = []
    for ch in ("telegram", "whatsapp", "web"):
        if ch in text.lower():
            channels.append(ch)
    if channels:
        fields["allowed_channels"] = channels

    if "auto-approve" in text.lower() or "no teacher approval" in text.lower():
        fields["require_teacher_approval"] = False

    return ParsedDocumentOutput(
        doc_type="school_policy",
        structured_fields=fields,
        confidence=0.9 if not ambiguities else 0.75,
        ambiguity_notes=ambiguities,
        missing_fields=[],
    )


def _parse_student_submission(text: str) -> ParsedDocumentOutput:
    return ParsedDocumentOutput(
        doc_type="student_submission",
        structured_fields={"summary": text[:500], "word_count": len(text.split())},
        confidence=0.9,
        ambiguity_notes=[],
        missing_fields=[],
    )


def parse_document_text(doc_type: str, raw_text: str) -> ParsedDocumentOutput:
    sanitized_text, flagged, reasons = detect_and_sanitize_injection(raw_text)
    if doc_type == "assignment_brief":
        parsed = _parse_assignment_brief(sanitized_text)
    elif doc_type == "class_roster":
        parsed = _parse_class_roster(sanitized_text)
    elif doc_type == "school_policy":
        parsed = _parse_school_policy(sanitized_text)
    elif doc_type == "student_submission":
        parsed = _parse_student_submission(sanitized_text)
    else:
        raise ValueError(f"Unsupported doc_type: {doc_type}")

    parsed.prompt_injection_flagged = flagged
    parsed.injection_reasons = reasons
    if flagged:
        parsed.ambiguity_notes.append("Prompt injection attempt detected and quarantined; manual review required.")
        parsed.confidence = min(parsed.confidence, 0.4)
    return parsed


def ingest_document(
    conn: sqlite3.Connection,
    *,
    actor: Dict[str, Any],
    doc_type: str,
    owner_type: str,
    owner_id: str,
    filename: str,
    content_bytes: bytes,
) -> Dict[str, Any]:
    school_id = actor["school_id"]
    safe_name, sha256 = validate_upload(filename, content_bytes)

    # Idempotency check on repeated file upload
    existing = conn.execute(
        "SELECT * FROM documents WHERE school_id = ? AND content_sha256 = ? AND doc_type = ?",
        (school_id, sha256, doc_type),
    ).fetchone()
    if existing:
        return {
            "document": dict(existing),
            "deduplicated": True,
            "conflicts": [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM roster_conflicts WHERE document_id = ?", (existing["id"],)
                ).fetchall()
            ],
        }

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    doc_id = generate_id("doc")
    storage_path = settings.upload_dir / f"{doc_id}_{safe_name}"
    storage_path.write_bytes(content_bytes)

    raw_text = extract_text_from_bytes(safe_name, content_bytes)
    parsed = parse_document_text(doc_type, raw_text)

    if parsed.prompt_injection_flagged:
        record_audit_event(
            conn,
            school_id=school_id,
            actor_user_id=actor["user_id"],
            actor_role=actor["role"],
            event_type="prompt_injection_detected",
            resource_type="document",
            resource_id=doc_id,
            outcome="denied",
            details={"reasons": parsed.injection_reasons, "filename": safe_name},
        )

    approval_state = "needs_clarification" if (parsed.missing_fields or parsed.ambiguity_notes) else "pending_review"
    now = utc_now_iso()

    conn.execute(
        """
        INSERT INTO documents (
            id, school_id, uploaded_by, owner_type, owner_id, doc_type, filename,
            content_sha256, storage_path, raw_text, parsed_json, confidence,
            ambiguity_notes, missing_fields, prompt_injection_flagged, approval_state, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            school_id,
            actor["user_id"],
            owner_type,
            owner_id,
            doc_type,
            safe_name,
            sha256,
            str(storage_path),
            raw_text,
            json.dumps(parsed.structured_fields),
            parsed.confidence,
            json.dumps(parsed.ambiguity_notes),
            json.dumps(parsed.missing_fields),
            1 if parsed.prompt_injection_flagged else 0,
            approval_state,
            now,
        ),
    )

    conflicts: List[Dict[str, Any]] = []
    if doc_type == "class_roster":
        conflicts = _populate_roster_conflicts(conn, school_id=school_id, doc_id=doc_id, rows=parsed.structured_fields.get("rows", []))

    record_audit_event(
        conn,
        school_id=school_id,
        actor_user_id=actor["user_id"],
        actor_role=actor["role"],
        event_type="document_parsed",
        resource_type="document",
        resource_id=doc_id,
        outcome="success",
        details={
            "doc_type": doc_type,
            "approval_state": approval_state,
            "confidence": parsed.confidence,
            "conflict_count": len(conflicts),
        },
    )

    broker.publish_sync(
        school_id,
        "document_parsed",
        {"document_id": doc_id, "doc_type": doc_type, "approval_state": approval_state},
    )
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return {"document": dict(row), "deduplicated": False, "conflicts": conflicts}


def _populate_roster_conflicts(
    conn: sqlite3.Connection, *, school_id: str, doc_id: str, rows: List[Dict[str, str]]
) -> List[Dict[str, Any]]:
    seen_names: Dict[str, int] = {}
    classes_in_school = {
        r["name"].lower(): r["id"]
        for r in conn.execute("SELECT id, name FROM classes WHERE school_id = ?", (school_id,)).fetchall()
    }
    existing_students = {
        r["full_name"].lower(): r["id"]
        for r in conn.execute(
            "SELECT id, full_name FROM users WHERE school_id = ? AND role = 'student'", (school_id,)
        ).fetchall()
    }

    conflicts: List[Dict[str, Any]] = []
    for row in rows:
        idx = int(row.get("row_index", 0))
        sname = row.get("student_name", "").strip()
        cname = row.get("class_name", "").strip()
        gcontact = row.get("guardian_contact", "").strip()

        issue_type: Optional[str] = None
        details = ""
        key = f"{sname.lower()}::{cname.lower()}"
        if key in seen_names or sname.lower() in existing_students:
            issue_type = "duplicate_student"
            details = f"Student '{sname}' appears multiple times or already exists in school."
        elif cname.lower() not in classes_in_school:
            issue_type = "ambiguous_class"
            details = f"Class '{cname}' not found in school classes ({list(classes_in_school.keys())})."
        elif not gcontact:
            issue_type = "missing_contact"
            details = f"Guardian contact is missing for '{sname}'."
        seen_names[key] = idx

        if issue_type:
            cid = generate_id("rcf")
            conn.execute(
                """
                INSERT INTO roster_conflicts (
                    id, document_id, school_id, row_index, student_name, class_name,
                    guardian_contact, issue_type, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (cid, doc_id, school_id, idx, sname, cname, gcontact, issue_type, details),
            )
            conflicts.append(
                {
                    "id": cid,
                    "row_index": idx,
                    "student_name": sname,
                    "class_name": cname,
                    "issue_type": issue_type,
                    "details": details,
                    "resolution_state": "open",
                }
            )
    return conflicts


def clarify_document(
    conn: sqlite3.Connection, *, actor: Dict[str, Any], doc_id: str, answers: Dict[str, str]
) -> Dict[str, Any]:
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc or doc["school_id"] != actor["school_id"]:
        raise ValueError("Document not found in your school.")
    validate_transition(DOCUMENT_APPROVAL_TRANSITIONS, doc["approval_state"], "pending_review", "document")

    parsed_fields = json.loads(doc["parsed_json"])
    for k, v in answers.items():
        if v.strip():
            parsed_fields[k] = v.strip()

    missing = [m for m in json.loads(doc["missing_fields"]) if not parsed_fields.get(m)]
    new_state = "needs_clarification" if missing else "pending_review"

    conn.execute(
        """
        UPDATE documents
        SET parsed_json = ?, missing_fields = ?, clarification_answers = ?, approval_state = ?
        WHERE id = ?
        """,
        (json.dumps(parsed_fields), json.dumps(missing), json.dumps(answers), new_state, doc_id),
    )
    record_audit_event(
        conn,
        school_id=actor["school_id"],
        actor_user_id=actor["user_id"],
        actor_role=actor["role"],
        event_type="document_clarified",
        resource_type="document",
        resource_id=doc_id,
        outcome="success",
        details={"new_state": new_state, "answered_keys": list(answers.keys())},
    )
    return dict(conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())


def approve_document(conn: sqlite3.Connection, *, actor: Dict[str, Any], doc_id: str) -> Dict[str, Any]:
    if actor["role"] not in {"admin", "teacher"}:
        raise PermissionError("Only admins or teachers can approve documents.")
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc or doc["school_id"] != actor["school_id"]:
        raise ValueError("Document not found in your school.")
    validate_transition(DOCUMENT_APPROVAL_TRANSITIONS, doc["approval_state"], "approved", "document")

    now = utc_now_iso()
    conn.execute(
        "UPDATE documents SET approval_state = 'approved', approved_by = ?, approved_at = ? WHERE id = ?",
        (actor["user_id"], now, doc_id),
    )
    record_audit_event(
        conn,
        school_id=actor["school_id"],
        actor_user_id=actor["user_id"],
        actor_role=actor["role"],
        event_type="document_approved",
        resource_type="document",
        resource_id=doc_id,
        outcome="success",
        details={"doc_type": doc["doc_type"]},
    )
    broker.publish_sync(actor["school_id"], "document_approved", {"document_id": doc_id, "doc_type": doc["doc_type"]})
    return dict(conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())
