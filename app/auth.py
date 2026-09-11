import hashlib
import hmac
import os
import secrets
from pathlib import Path
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .db import DATA_DIR
from .models import UserAccount, AuditLog

PBKDF2_ITERATIONS = 260_000
SECRET_PATH = DATA_DIR / ".session_secret"


def session_secret() -> str:
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    SECRET_PATH.write_text(value, encoding="utf-8")
    return value


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def current_user(request: Request, db: Session) -> UserAccount | None:
    user_id = request.session.get("user_id") if "session" in request.scope else None
    if not user_id:
        return None
    user = db.get(UserAccount, int(user_id))
    if not user or not user.active:
        return None
    return user


def require_role(request: Request, db: Session, allowed: set[str]) -> UserAccount:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Sessão não autenticada")
    if user.role not in allowed:
        raise HTTPException(status_code=403, detail="Você não possui permissão para esta ação")
    return user


def audit(db: Session, user: UserAccount | None, action: str, entity_type: str,
          entity_id=None, summary: str = "", before_json: str | None = None,
          after_json: str | None = None):
    db.add(AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=summary[:300],
        before_json=before_json,
        after_json=after_json,
    ))
