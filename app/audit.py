import json
import logging
import sqlite3
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.security import generate_id

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

logger = logging.getLogger("school_ops")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

REDACT_KEYS = {"password", "password_hash", "token", "secret", "email", "phone", "guardian_contact"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_correlation_id() -> str:
    cid = correlation_id_var.get()
    if not cid:
        cid = f"corr_{uuid.uuid4().hex[:12]}"
        correlation_id_var.set(cid)
    return cid


def _sanitize_details(details: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not details:
        return {}
    clean: Dict[str, Any] = {}
    for k, v in details.items():
        if k.lower() in REDACT_KEYS:
            clean[k] = "[REDACTED]"
        elif isinstance(v, dict):
            clean[k] = _sanitize_details(v)
        else:
            clean[k] = v
    return clean


def record_audit_event(
    conn: sqlite3.Connection,
    *,
    school_id: Optional[str],
    actor_user_id: Optional[str],
    actor_role: Optional[str],
    event_type: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    outcome: str = "success",
    details: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> str:
    cid = correlation_id or get_correlation_id()
    event_id = generate_id("evt")
    safe_details = _sanitize_details(details)
    now = utc_now_iso()

    conn.execute(
        """
        INSERT INTO audit_events (
            id, school_id, actor_user_id, actor_role, event_type,
            resource_type, resource_id, outcome, correlation_id, details_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            school_id,
            actor_user_id,
            actor_role,
            event_type,
            resource_type,
            resource_id,
            outcome,
            cid,
            json.dumps(safe_details),
            now,
        ),
    )
    if outcome in {"denied", "failed"}:
        conn.commit()

    logger.info(
        json.dumps(
            {
                "ts": now,
                "correlation_id": cid,
                "event_id": event_id,
                "school_id": school_id,
                "actor_role": actor_role,
                "event_type": event_type,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "outcome": outcome,
            }
        )
    )
    return event_id


def record_trace_span(
    conn: sqlite3.Connection,
    *,
    correlation_id: str,
    school_id: Optional[str],
    channel: str,
    intent: str,
    action: str,
    notification: str,
    status: str,
    duration_ms: int = 0,
) -> str:
    span_id = generate_id("trc")
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO trace_spans (
            id, correlation_id, school_id, channel, intent, action, notification, status, duration_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (span_id, correlation_id, school_id, channel, intent, action, notification, status, duration_ms, now),
    )
    return span_id
