import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, Optional, Tuple

from app.config import settings

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".csv", ".txt", ".png", ".jpg", ".jpeg"}


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: Optional[str]) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    parts = stored_hash.split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    _, salt, expected_hex = parts
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return hmac.compare_digest(dk.hex(), expected_hex)


def hash_token(raw_token: str) -> str:
    return hmac.new(
        settings.secret_key.encode("utf-8"), raw_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def generate_invite_code() -> str:
    return f"INV-{secrets.token_hex(4).upper()}"


def generate_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def sanitize_filename(filename: str) -> str:
    base = Path(filename).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return cleaned or "upload.txt"


def validate_upload(filename: str, content: bytes) -> Tuple[str, str]:
    safe_name = sanitize_filename(filename)
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(f"Unsupported file extension '{ext}'. Allowed: {sorted(ALLOWED_UPLOAD_EXTENSIONS)}")
    if len(content) > settings.max_upload_bytes:
        raise ValueError(f"File exceeds max upload size of {settings.max_upload_bytes} bytes")
    sha256 = hashlib.sha256(content).hexdigest()
    return safe_name, sha256


class InMemoryRateLimiter:
    def __init__(self, max_per_minute: int = 120) -> None:
        self.max_per_minute = max_per_minute
        self.buckets: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, client_key: str) -> bool:
        now = time.time()
        window_start = now - 60.0
        q = self.buckets[client_key]
        while q and q[0] < window_start:
            q.popleft()
        if len(q) >= self.max_per_minute:
            return False
        q.append(now)
        return True


rate_limiter = InMemoryRateLimiter(settings.rate_limit_per_minute)
