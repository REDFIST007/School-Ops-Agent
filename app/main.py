import uuid
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.audit import correlation_id_var
from app.config import settings
from app.database import init_db
from app.routers import (
    api_assignments,
    api_auth,
    api_documents,
    api_events,
    api_reminders,
    api_school,
    api_webhooks,
    web,
)
from app.security import rate_limiter

app = FastAPI(title=settings.app_name, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def correlation_and_security_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(client_ip):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded."})

    cid = request.headers.get("X-Correlation-ID") or f"corr_{uuid.uuid4().hex[:12]}"
    token = correlation_id_var.set(cid)
    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response
    finally:
        correlation_id_var.reset(token)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "quiet_hours_enforced": settings.feature_quiet_hours_enforced,
        "strict_prompt_guard": settings.feature_strict_prompt_guard,
    }


static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(web.router)
app.include_router(api_auth.router)
app.include_router(api_school.router)
app.include_router(api_documents.router)
app.include_router(api_assignments.router)
app.include_router(api_reminders.router)
app.include_router(api_webhooks.router)
app.include_router(api_events.router)
