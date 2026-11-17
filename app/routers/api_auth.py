from typing import Any, Dict, Optional
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status

from app.database import get_conn
from app.models import (
    AcceptInviteRequest,
    CreateInviteRequest,
    LinkChatRequest,
    LoginRequest,
    SchoolRegisterRequest,
)
from app.routers.deps import extract_token, require_user
from app.services.auth_service import (
    AuthError,
    PermissionDeniedError,
    accept_invite,
    create_invite,
    link_chat_identity,
    login_user,
    logout_session,
    register_school,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def api_register_school(payload: SchoolRegisterRequest, response: Response) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            res = register_school(
                conn,
                school_name=payload.school_name,
                admin_name=payload.admin_name,
                admin_email=payload.admin_email,
                password=payload.password,
                tz=payload.timezone,
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    response.set_cookie("session_token", res["session_token"], httponly=True, samesite="lax")
    return res


@router.post("/login")
def api_login(payload: LoginRequest, response: Response) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            res = login_user(conn, email=payload.email, password=payload.password)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
    response.set_cookie("session_token", res["session_token"], httponly=True, samesite="lax")
    return res


@router.post("/logout")
def api_logout(
    response: Response,
    session_token: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, str]:
    tok = extract_token(session_token, authorization)
    with get_conn() as conn:
        logout_session(conn, tok)
    response.delete_cookie("session_token")
    return {"status": "logged_out"}


@router.get("/me")
def api_me(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    return {
        "user_id": user["user_id"],
        "school_id": user["school_id"],
        "school_name": user["school_name"],
        "full_name": user["full_name"],
        "role": user["role"],
    }


@router.post("/invites", status_code=status.HTTP_201_CREATED)
def api_create_invite(
    payload: CreateInviteRequest, user: Dict[str, Any] = Depends(require_user)
) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            return create_invite(
                conn,
                actor=user,
                role=payload.role,
                invitee_name=payload.invitee_name,
                invitee_email=payload.invitee_email,
                class_id=payload.class_id,
                linked_student_id=payload.linked_student_id,
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accept-invite", status_code=status.HTTP_201_CREATED)
def api_accept_invite(payload: AcceptInviteRequest, response: Response) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            res = accept_invite(
                conn,
                code=payload.code,
                email=payload.email,
                password=payload.password,
                full_name=payload.full_name,
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    response.set_cookie("session_token", res["session_token"], httponly=True, samesite="lax")
    return res


@router.post("/link-chat")
def api_link_chat(
    payload: LinkChatRequest, user: Dict[str, Any] = Depends(require_user)
) -> Dict[str, Any]:
    with get_conn() as conn:
        try:
            return link_chat_identity(
                conn,
                channel=payload.channel,
                external_user_id=payload.external_user_id,
                user=user,
                invite_code=payload.invite_code,
            )
        except PermissionDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
