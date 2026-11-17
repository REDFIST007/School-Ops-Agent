import json
import sqlite3
import time
from typing import Any, Dict

from app.audit import get_correlation_id, record_audit_event, record_trace_span, utc_now_iso
from app.models import CanonicalMessageEnvelope
from app.services.assignment_service import submit_work, update_student_progress
from app.services.auth_service import link_chat_identity, resolve_chat_user
from app.services.intent_layer import classify_intent


def normalize_telegram_payload(payload: Dict[str, Any]) -> CanonicalMessageEnvelope:
    msg = payload.get("message") or payload.get("edited_message") or {}
    chat = msg.get("chat") or {}
    return CanonicalMessageEnvelope(
        channel="telegram",
        external_user_id=str(chat.get("id", "")),
        message_id=str(payload.get("update_id") or msg.get("message_id") or ""),
        text=str(msg.get("text") or msg.get("caption") or ""),
    )


def normalize_whatsapp_payload(payload: Dict[str, Any]) -> CanonicalMessageEnvelope:
    try:
        entry = payload["entry"][0]["changes"][0]["value"]
        msg = entry["messages"][0]
        return CanonicalMessageEnvelope(
            channel="whatsapp",
            external_user_id=str(msg.get("from", "")),
            message_id=str(msg.get("id", "")),
            text=str(msg.get("text", {}).get("body", "")),
        )
    except (KeyError, IndexError, TypeError):
        return CanonicalMessageEnvelope(
            channel="whatsapp",
            external_user_id=str(payload.get("from", "")),
            message_id=str(payload.get("message_id", "")),
            text=str(payload.get("text", "")),
        )


def process_incoming_message(conn: sqlite3.Connection, envelope: CanonicalMessageEnvelope) -> Dict[str, Any]:
    start_ms = int(time.time() * 1000)
    cid = get_correlation_id()

    # Webhook idempotency
    idem_key = f"msg:{envelope.channel}:{envelope.message_id}"
    if envelope.message_id:
        cached = conn.execute(
            "SELECT response_json FROM idempotency_keys WHERE key = ? AND scope = 'webhook'",
            (idem_key,),
        ).fetchone()
        if cached:
            resp = json.loads(cached["response_json"])
            resp["deduplicated"] = True
            return resp

    intent_res = classify_intent(envelope.text)
    user = resolve_chat_user(conn, envelope.channel, envelope.external_user_id)

    # Allow /link INV-XXXXXXXX even before user is linked
    if intent_res.intent == "link_chat":
        code = intent_res.entities.get("invite_code", "")
        try:
            linked = link_chat_identity(
                conn,
                channel=envelope.channel,
                external_user_id=envelope.external_user_id,
                user=user,
                invite_code=code,
            )
            reply = f"Linked {envelope.channel} identity to your {linked['role']} account."
            out = {"reply": reply, "intent": "link_chat", "status": "ok", "correlation_id": cid}
            record_trace_span(
                conn,
                correlation_id=cid,
                school_id=linked["school_id"],
                channel=envelope.channel,
                intent="link_chat",
                action="link_chat_identity",
                notification=reply,
                status="success",
                duration_ms=int(time.time() * 1000) - start_ms,
            )
        except Exception as exc:
            reply = f"Could not link chat identity: {exc}"
            out = {"reply": reply, "intent": "link_chat", "status": "error", "correlation_id": cid}
        _store_webhook_idem(conn, idem_key, envelope.message_id, out)
        return out

    # Reject unlinked users before sensitive actions
    if not user:
        reply = "Your chat identity is not linked. Please send `/link INV-XXXXXXXX` with your school invite code first."
        record_audit_event(
            conn,
            school_id=None,
            actor_user_id=None,
            actor_role="anonymous",
            event_type="access_denied",
            resource_type="chat_channel",
            resource_id=f"{envelope.channel}:{envelope.external_user_id}",
            outcome="denied",
            correlation_id=cid,
            details={"reason": "unlinked_chat_identity"},
        )
        out = {"reply": reply, "intent": intent_res.intent, "status": "unlinked", "correlation_id": cid}
        _store_webhook_idem(conn, idem_key, envelope.message_id, out)
        return out

    school_id = user["school_id"]
    role = user["role"]

    if intent_res.is_unsafe_or_unknown:
        reply = "I could not safely process that request. Please use supported school commands (e.g., progress update, blocked/help, submit work, or parent digest)."
        record_audit_event(
            conn,
            school_id=school_id,
            actor_user_id=user["user_id"],
            actor_role=role,
            event_type="intent_rejected",
            resource_type="chat_message",
            outcome="denied",
            correlation_id=cid,
            details={"intent": intent_res.intent, "reason": intent_res.reason},
        )
        record_trace_span(
            conn,
            correlation_id=cid,
            school_id=school_id,
            channel=envelope.channel,
            intent=intent_res.intent,
            action="rejected_unsafe_or_unknown",
            notification=reply,
            status="denied",
            duration_ms=int(time.time() * 1000) - start_ms,
        )
        out = {"reply": reply, "intent": intent_res.intent, "status": "rejected", "correlation_id": cid}
        _store_webhook_idem(conn, idem_key, envelope.message_id, out)
        return out

    # Route by intent + role
    reply = ""
    action_name = "none"
    status_label = "success"

    if intent_res.intent in {"progress_update", "blocked_help_request"}:
        if role != "student":
            reply = "Only students can report assignment progress or blockers."
            status_label = "denied"
            action_name = "role_mismatch_denied"
            record_audit_event(
                conn,
                school_id=school_id,
                actor_user_id=user["user_id"],
                actor_role=role,
                event_type="access_denied",
                resource_type="student_assignment",
                outcome="denied",
                correlation_id=cid,
                details={"intent": intent_res.intent, "reason": "non_student_progress_attempt"},
            )
        else:
            sa = conn.execute(
                """
                SELECT sa.id, a.title FROM student_assignments sa
                JOIN assignments a ON a.id = sa.assignment_id
                WHERE sa.student_id = ? AND sa.status != 'completed'
                ORDER BY sa.updated_at DESC LIMIT 1
                """,
                (user["user_id"],),
            ).fetchone()
            if not sa:
                reply = "You have no active assignments to update."
            else:
                target_status = "blocked" if intent_res.intent == "blocked_help_request" else "in_progress"
                update_student_progress(
                    conn,
                    actor=user,
                    student_assignment_id=sa["id"],
                    status=target_status,
                    note=envelope.text,
                )
                action_name = f"update_status_{target_status}"
                reply = f"Updated '{sa['title']}' to {target_status}. Your teacher has been notified."

    elif intent_res.intent in {"submission", "resubmission"}:
        if role != "student":
            reply = "Only students can submit assignment work."
            status_label = "denied"
            action_name = "role_mismatch_denied"
        else:
            sa = conn.execute(
                """
                SELECT sa.id, a.title FROM student_assignments sa
                JOIN assignments a ON a.id = sa.assignment_id
                WHERE sa.student_id = ? AND sa.status != 'completed'
                ORDER BY sa.updated_at DESC LIMIT 1
                """,
                (user["user_id"],),
            ).fetchone()
            if not sa:
                reply = "No open assignment found for submission."
            else:
                res = submit_work(
                    conn,
                    actor=user,
                    student_assignment_id=sa["id"],
                    submission_type="text",
                    content_text=envelope.text,
                    idempotency_key=f"chat_sub:{envelope.channel}:{envelope.message_id}" if envelope.message_id else None,
                )
                action_name = "submit_work"
                reply = f"Submission v{res['version']} recorded for '{sa['title']}'."

    elif intent_res.intent in {"parent_opt_in", "parent_digest_request", "escalation_acknowledgement"}:
        if role != "guardian":
            reply = "Parent/guardian role is required for this action."
            status_label = "denied"
            action_name = "role_mismatch_denied"
        elif intent_res.intent == "parent_opt_in":
            conn.execute("UPDATE users SET guardian_opt_in = 1 WHERE id = ?", (user["user_id"],))
            action_name = "guardian_opt_in"
            reply = "You are opted in to receive child progress digests and escalation alerts."
        elif intent_res.intent == "escalation_acknowledgement":
            action_name = "escalation_acknowledged"
            record_audit_event(
                conn,
                school_id=school_id,
                actor_user_id=user["user_id"],
                actor_role=role,
                event_type="escalation_acknowledged",
                resource_type="guardian",
                resource_id=user["user_id"],
                outcome="success",
                correlation_id=cid,
            )
            reply = "Thank you, escalation acknowledgement recorded."
        else:
            children = conn.execute(
                """
                SELECT u.full_name, a.title, sa.status
                FROM student_guardians sg
                JOIN users u ON u.id = sg.student_id
                LEFT JOIN student_assignments sa ON sa.student_id = u.id
                LEFT JOIN assignments a ON a.id = sa.assignment_id
                WHERE sg.guardian_id = ?
                """,
                (user["user_id"],),
            ).fetchall()
            action_name = "parent_digest"
            if not children:
                reply = "No linked student assignments found."
            else:
                lines = [f"- {c['full_name']}: {c['title'] or 'No assignments'} ({c['status'] or 'N/A'})" for c in children]
                reply = "High-level child progress:\n" + "\n".join(lines)
    else:
        action_name = "web_portal_guidance"
        reply = f"Intent '{intent_res.intent}' recognized. Please complete high-impact review/approval actions in the Web Portal."

    record_trace_span(
        conn,
        correlation_id=cid,
        school_id=school_id,
        channel=envelope.channel,
        intent=intent_res.intent,
        action=action_name,
        notification=reply[:200],
        status=status_label,
        duration_ms=int(time.time() * 1000) - start_ms,
    )
    out = {"reply": reply, "intent": intent_res.intent, "status": status_label, "correlation_id": cid}
    _store_webhook_idem(conn, idem_key, envelope.message_id, out)
    return out


def _store_webhook_idem(conn: sqlite3.Connection, key: str, message_id: str, out: Dict[str, Any]) -> None:
    if not message_id:
        return
    conn.execute(
        "INSERT OR IGNORE INTO idempotency_keys (key, scope, response_json, created_at) VALUES (?, 'webhook', ?, ?)",
        (key, json.dumps(out), utc_now_iso()),
    )
