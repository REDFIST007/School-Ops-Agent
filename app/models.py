from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class SchoolRegisterRequest(BaseModel):
    school_name: str = Field(..., min_length=2, max_length=120)
    admin_name: str = Field(..., min_length=2, max_length=100)
    admin_email: str = Field(..., min_length=5, max_length=150)
    password: str = Field(..., min_length=6, max_length=128)
    timezone: str = "Asia/Kolkata"


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateClassRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    grade: str = Field(..., min_length=1, max_length=30)


class CreateInviteRequest(BaseModel):
    role: Literal["teacher", "student", "guardian"]
    invitee_name: str = Field(..., min_length=2, max_length=100)
    invitee_email: Optional[str] = None
    class_id: Optional[str] = None
    linked_student_id: Optional[str] = None


class AcceptInviteRequest(BaseModel):
    code: str
    email: str
    password: str = Field(..., min_length=6, max_length=128)
    full_name: Optional[str] = None


class LinkChatRequest(BaseModel):
    channel: Literal["telegram", "whatsapp", "web"]
    external_user_id: str = Field(..., min_length=1, max_length=120)
    invite_code: Optional[str] = None


class ClarifyDocumentRequest(BaseModel):
    answers: Dict[str, str]


class ResolveRosterConflictRequest(BaseModel):
    resolution: Literal["merged", "created_new", "skipped"]


class CreateAssignmentRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    subject: str = Field(..., min_length=1, max_length=80)
    instructions: str = Field(..., min_length=3, max_length=4000)
    due_date: Optional[str] = None
    target_type: Literal["class", "group", "individual"] = "class"
    target_class_id: Optional[str] = None
    target_student_ids: List[str] = Field(default_factory=list)
    idempotency_key: Optional[str] = None


class ProgressUpdateRequest(BaseModel):
    status: Literal["in_progress", "blocked"]
    note: str = Field("", max_length=1000)


class SubmissionRequest(BaseModel):
    submission_type: Literal["text", "file", "photo", "voice_transcript"] = "text"
    content_text: str = Field(..., min_length=1, max_length=8000)
    idempotency_key: Optional[str] = None


class TeacherFeedbackRequest(BaseModel):
    decision: Literal["comment", "revision_requested", "completed"]
    comment: str = Field(..., min_length=1, max_length=2000)


class PolicyUpdateRequest(BaseModel):
    quiet_hours_start: str = "21:00"
    quiet_hours_end: str = "07:00"
    escalation_hours: int = 24
    allowed_channels: List[str] = Field(default_factory=lambda: ["telegram", "whatsapp", "web"])
    require_teacher_approval: bool = True


class CanonicalMessageEnvelope(BaseModel):
    channel: Literal["telegram", "whatsapp", "web"]
    external_user_id: str
    message_id: str
    text: str
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: Optional[str] = None


class ParsedDocumentOutput(BaseModel):
    doc_type: Literal["assignment_brief", "class_roster", "school_policy", "student_submission"]
    structured_fields: Dict[str, Any]
    confidence: float = Field(..., ge=0.0, le=1.0)
    ambiguity_notes: List[str] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    prompt_injection_flagged: bool = False
    injection_reasons: List[str] = Field(default_factory=list)


class IntentResult(BaseModel):
    intent: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    entities: Dict[str, Any] = Field(default_factory=dict)
    requires_clarification: bool = False
    clarification_question: Optional[str] = None
    is_unsafe_or_unknown: bool = False
    reason: Optional[str] = None
