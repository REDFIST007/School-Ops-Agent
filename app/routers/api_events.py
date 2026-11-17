from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.database import get_conn
from app.routers.deps import require_user
from app.sse import broker

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events/stream")
async def sse_stream(user: Dict[str, Any] = Depends(require_user)):
    return StreamingResponse(broker.subscribe(user["school_id"]), media_type="text/event-stream")


@router.get("/audit")
def api_list_audit(user: Dict[str, Any] = Depends(require_user)) -> List[Dict[str, Any]]:
    if user["role"] not in {"admin", "teacher"}:
        raise HTTPException(status_code=403, detail="Only admins or teachers can view audit timeline.")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE school_id = ? ORDER BY created_at DESC LIMIT 150",
            (user["school_id"],),
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/traces")
def api_list_traces(user: Dict[str, Any] = Depends(require_user)) -> List[Dict[str, Any]]:
    if user["role"] not in {"admin", "teacher"}:
        raise HTTPException(status_code=403, detail="Only admins or teachers can view trace spans.")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trace_spans WHERE school_id = ? ORDER BY created_at DESC LIMIT 100",
            (user["school_id"],),
        ).fetchall()
        return [dict(r) for r in rows]
