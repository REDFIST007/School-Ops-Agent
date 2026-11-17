import os
from pathlib import Path
from typing import List


def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.app_name: str = os.getenv("APP_NAME", "School Operations Agent Platform")
        self.app_env: str = os.getenv("APP_ENV", "development")
        self.secret_key: str = os.getenv(
            "SECRET_KEY", "dev-only-insecure-secret-replace-in-prod-9f8e7d6c5b4a"
        )
        self.database_path: Path = Path(os.getenv("DATABASE_PATH", "./data/school_ops.db"))
        self.upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "./data/uploads"))
        self.max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))

        self.llm_provider: str = os.getenv("LLM_PROVIDER", "deterministic").lower()
        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.prompt_version: str = os.getenv("PROMPT_VERSION", "v1.0")

        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
        self.whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
        self.whatsapp_access_token: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        self.whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

        # Feature flags
        self.feature_quiet_hours_enforced: bool = _bool_env("FEATURE_QUIET_HOURS_ENFORCED", True)
        self.feature_auto_escalate_blocked: bool = _bool_env("FEATURE_AUTO_ESCALATE_BLOCKED", True)
        self.feature_strict_prompt_guard: bool = _bool_env("FEATURE_STRICT_PROMPT_GUARD", True)

        raw_origins = os.getenv("FEATURE_CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
        self.cors_origins: List[str] = [o.strip() for o in raw_origins.split(",") if o.strip()]

        self.session_ttl_hours: int = int(os.getenv("SESSION_TTL_HOURS", "24"))
        self.invite_ttl_hours: int = int(os.getenv("INVITE_TTL_HOURS", "48"))
        self.rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))


settings = Settings()
