from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException

from app.database import get_conn
from app.routers.deps import require_user
from app.services.reminder_engine import run_reminders_for_school

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


@router.post("/run")
def api_run_reminders(
    override_hhmm: Optional[str] = None,
    ignore_quiet_hours: bool = False,
    user: Dict[str, Any] = Depends(require_user),
) -> Dict[str, Any]:
    if user["role"] not in {"admin", "teacher"}:
        raise HTTPException(status_code=403, detail="Only admins or teachers can trigger reminders.")
    with get_conn() as conn:
        return run_reminders_for_school(
            conn,
            school_id=user["school_id"],
            actor=user,
            override_hhmm=override_hhmm,
            ignore_quiet_hours=ignore_quiet_hours,
        )


@router.get("")
def api_list_reminders(user: Dict[str, Any] = Depends(require_user)) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if user["role"] in {"admin", "teacher"}:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE school_id = ? ORDER BY created_at DESC LIMIT 100",
                (user["school_id"],),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE school_id = ? AND recipient_user_id = ? ORDER BY created_at DESC LIMIT 50",
                (user["school_id"], user["user_id"]),
            ).fetchall()
        return [dict(r) for r in rows]
