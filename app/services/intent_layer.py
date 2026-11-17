import json
import re
from typing import Any, Dict

from app.config import settings
from app.models import IntentResult
from app.services.document_parser import detect_and_sanitize_injection


SUPPORTED_INTENTS = {
    "create_assignment",
    "update_assignment",
    "cancel_assignment",
    "progress_update",
    "blocked_help_request",
    "submission",
    "resubmission",
    "teacher_feedback",
    "revision_request",
    "completion_decision",
    "parent_opt_in",
    "parent_digest_request",
    "escalation_acknowledgement",
    "admin_config_change",
    "roster_import",
    "policy_upload",
    "link_chat",
    "unknown_unsafe_out_of_scope",
}


def classify_intent_deterministic(text: str) -> IntentResult:
    sanitized, flagged, reasons = detect_and_sanitize_injection(text)
    if flagged:
        return IntentResult(
            intent="unknown_unsafe_out_of_scope",
            confidence=0.95,
            is_unsafe_or_unknown=True,
            reason="; ".join(reasons),
        )

    low = sanitized.strip().lower()
    if not low:
        return IntentResult(intent="unknown_unsafe_out_of_scope", confidence=0.9, is_unsafe_or_unknown=True, reason="Empty message")

    # Chat linking command
    m_link = re.search(r"(?:/link|link)\s+(INV-[A-Z0-9]+)", sanitized, re.I)
    if m_link:
        return IntentResult(intent="link_chat", confidence=0.99, entities={"invite_code": m_link.group(1).upper()})

    # Parent / guardian intents
    if re.search(r"\b(opt[\s-]?in|subscribe|receive\s+updates)\b", low):
        return IntentResult(intent="parent_opt_in", confidence=0.92)
    if re.search(r"\b(digest|summary|how\s+is\s+my\s+child|progress\s+report)\b", low):
        return IntentResult(intent="parent_digest_request", confidence=0.9)
    if re.search(r"\b(acknowledge|acknowledged|got\s+it|seen\s+escalation)\b", low):
        return IntentResult(intent="escalation_acknowledgement", confidence=0.88)

    # Student blocked / help
    if re.search(r"\b(blocked|stuck|help|can'?t\s+solve|need\s+help|confused)\b", low):
        return IntentResult(intent="blocked_help_request", confidence=0.92, entities={"note": sanitized})

    # Student submission / resubmission
    if re.search(r"\b(resubmit|resubmission|updated\s+work|fixed\s+submission)\b", low):
        return IntentResult(intent="resubmission", confidence=0.91, entities={"content": sanitized})
    if re.search(r"\b(submit|submitted|here\s+is\s+my\s+work|attached\s+homework|completed\s+homework)\b", low):
        return IntentResult(intent="submission", confidence=0.9, entities={"content": sanitized})

    # Student progress
    if re.search(r"\b(progress|working\s+on|started|halfway|done\s+\d+%|making\s+progress)\b", low):
        return IntentResult(intent="progress_update", confidence=0.88, entities={"note": sanitized})

    # Teacher feedback / revision / completion
    if re.search(r"\b(request\s+revision|revise|needs\s+revision)\b", low):
        return IntentResult(intent="revision_request", confidence=0.9, entities={"comment": sanitized})
    if re.search(r"\b(mark\s+complete|approve\s+submission|assignment\s+complete|completed)\b", low):
        return IntentResult(intent="completion_decision", confidence=0.87, entities={"comment": sanitized})
    if re.search(r"\b(feedback|good\s+job|well\s+done)\b", low):
        return IntentResult(intent="teacher_feedback", confidence=0.85, entities={"comment": sanitized})

    # Assignment lifecycle
    if re.search(r"\b(cancel\s+assignment|delete\s+assignment)\b", low):
        return IntentResult(intent="cancel_assignment", confidence=0.9)
    if re.search(r"\b(update\s+assignment|extend\s+due\s+date|change\s+deadline)\b", low):
        return IntentResult(intent="update_assignment", confidence=0.88)
    if re.search(r"\b(create\s+assignment|new\s+assignment|assign\s+homework)\b", low):
        return IntentResult(intent="create_assignment", confidence=0.88, entities={"raw": sanitized})

    # Admin
    if re.search(r"\b(import\s+roster|upload\s+roster)\b", low):
        return IntentResult(intent="roster_import", confidence=0.9)
    if re.search(r"\b(upload\s+policy|quiet\s+hours|escalation\s+rule)\b", low):
        return IntentResult(intent="policy_upload", confidence=0.88)
    if re.search(r"\b(config|setting|timezone)\b", low):
        return IntentResult(intent="admin_config_change", confidence=0.82)

    return IntentResult(
        intent="unknown_unsafe_out_of_scope",
        confidence=0.75,
        is_unsafe_or_unknown=True,
        reason="Message did not match a supported school operations intent.",
    )


def classify_intent(text: str) -> IntentResult:
    # Deterministic provider is default and fallback; optional OpenAI adapter can be enabled via LLM_PROVIDER=openai
    if settings.llm_provider == "openai" and settings.openai_api_key:
        try:
            import httpx

            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.openai_model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Classify the school operations message into one of: "
                                + ", ".join(sorted(SUPPORTED_INTENTS))
                                + ". Return JSON with keys: intent, confidence, entities, is_unsafe_or_unknown, reason."
                            ),
                        },
                        {"role": "user", "content": text[:1500]},
                    ],
                    "temperature": 0.0,
                },
                timeout=8.0,
            )
            if resp.status_code == 200:
                raw = json.loads(resp.json()["choices"][0]["message"]["content"])
                if raw.get("intent") in SUPPORTED_INTENTS:
                    return IntentResult(**raw)
        except Exception:
            # Safe fallback to deterministic classifier so failed model calls never leave state stuck
            pass
    return classify_intent_deterministic(text)
