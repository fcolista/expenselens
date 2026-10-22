import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
from fastapi import Depends, HTTPException, Request, status
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserSession

password_hash = PasswordHash.recommended()
SESSION_COOKIE = "expense_session"


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_session(db: Session, user: User, request: Request | None = None,
                   method: str = "password") -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.add(UserSession(
        id=hash_value(token), user_id=user.id, expires_at=now + timedelta(days=14), csrf_token=csrf,
        public_id=secrets.token_urlsafe(12), created_at=now, last_seen_at=now, method=method,
        user_agent=(request.headers.get("user-agent", "")[:300] if request else ""),
    ))
    db.commit()
    return token, csrf


def current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Accesso richiesto")
    session = db.get(UserSession, hash_value(token))
    expires_at = session.expires_at if session else None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not session or expires_at <= datetime.now(timezone.utc):
        if session:
            db.delete(session)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessione scaduta")
    seen = session.last_seen_at
    if seen is not None and seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    if seen is None or datetime.now(timezone.utc) - seen > timedelta(minutes=5):
        session.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    return session


def current_user(session: UserSession = Depends(current_session), db: Session = Depends(get_db)) -> User:
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Utente non disponibile")
    return user


def household_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Permessi di amministratore richiesti")
    return user


def csrf_check(request: Request, session: UserSession = Depends(current_session)) -> UserSession:
    submitted = request.headers.get("X-CSRF-Token", "")
    if not secrets.compare_digest(submitted, session.csrf_token):
        raise HTTPException(status_code=403, detail="Token CSRF non valido")
    return session


def verify_totp(user: User, code: str | None) -> bool:
    if not user.mfa_enabled:
        return True
    return bool(code and user.mfa_secret and pyotp.TOTP(user.mfa_secret).verify(code, valid_window=1))
