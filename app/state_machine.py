from typing import Dict, Set


class InvalidStateTransitionError(ValueError):
    pass


ASSIGNMENT_TRANSITIONS: Dict[str, Set[str]] = {
    "draft": {"active", "cancelled"},
    "active": {"cancelled", "archived"},
    "cancelled": {"archived"},
    "archived": set(),
}

STUDENT_ASSIGNMENT_TRANSITIONS: Dict[str, Set[str]] = {
    "assigned": {"in_progress", "blocked", "submitted"},
    "in_progress": {"in_progress", "blocked", "submitted"},
    "blocked": {"in_progress", "submitted"},
    "submitted": {"revision_requested", "completed"},
    "revision_requested": {"in_progress", "blocked", "submitted"},
    "completed": set(),
}

DOCUMENT_APPROVAL_TRANSITIONS: Dict[str, Set[str]] = {
    "uploaded": {"needs_clarification", "pending_review", "rejected"},
    "needs_clarification": {"pending_review", "approved", "rejected"},
    "pending_review": {"approved", "rejected", "needs_clarification"},
    "approved": set(),
    "rejected": set(),
}


def validate_transition(table: Dict[str, Set[str]], current: str, target: str, entity_name: str) -> None:
    if current == target and target in table:
        return
    allowed = table.get(current, set())
    if target not in allowed:
        raise InvalidStateTransitionError(
            f"Invalid {entity_name} transition: '{current}' -> '{target}'. Allowed: {sorted(allowed)}"
        )
