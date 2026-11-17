from typing import Any, Dict, Optional
from fastapi import Cookie, Header, HTTPException, status

from app.database import get_conn
from app.services.auth_service import get_user_by_session


def extract_token(
    session_token: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return session_token


def get_optional_user(
    session_token: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Optional[Dict[str, Any]]:
    tok = extract_token(session_token, authorization)
    if not tok:
        return None
    with get_conn() as conn:
        return get_user_by_session(conn, tok)


def require_user(
    session_token: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    user = get_optional_user(session_token, authorization)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return user
