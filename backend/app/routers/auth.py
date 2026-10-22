import base64
import io
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Category, Household, HouseholdInvite, TelegramLinkToken, User, UserSession
from app.limits import limiter
from app.schemas import AuthOutput, LoginInput, RegisterInput, UserOutput
from app.security import (
    SESSION_COOKIE, create_session, current_session, current_user, csrf_check, hash_value,
    household_admin,
    password_hash, verify_totp,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class MfaCodeInput(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=settings.secure_cookies,
                        samesite="lax", max_age=14 * 24 * 60 * 60, path="/")


@router.post("/register", status_code=201, response_model=AuthOutput)
@limiter.limit("5/hour")
def register(request: Request, body: RegisterInput, response: Response,
             db: Session = Depends(get_db)) -> AuthOutput:
    if not settings.allow_registration and not body.invite_token:
        raise HTTPException(403, "Registrazione disabilitata")
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "Email già registrata")
    if body.invite_token:
        invite = db.scalar(select(HouseholdInvite).where(
            HouseholdInvite.token_hash == hash_value(body.invite_token),
            HouseholdInvite.consumed.is_(False),
        ).with_for_update())
        invite_expiry = invite.expires_at if invite else None
        if invite_expiry and invite_expiry.tzinfo is None:
            invite_expiry = invite_expiry.replace(tzinfo=timezone.utc)
        if not invite or invite_expiry <= datetime.now(timezone.utc):
            raise HTTPException(400, "Invito non valido o scaduto")
        household_id = invite.household_id
        invite.consumed = True
    else:
        if not body.household_name:
            raise HTTPException(422, "Il nome famiglia è obbligatorio senza invito")
        household = Household(name=body.household_name.strip())
        db.add(household)
        db.flush()
        household_id = household.id
    user = User(email=body.email.lower(), password_hash=password_hash.hash(body.password),
                display_name=body.display_name.strip(), household_id=household_id,
                is_admin=not body.invite_token)
    db.add(user)
    db.flush()
    if not body.invite_token:
        for name, color in [("Casa", "#0f766e"), ("Alimentari", "#16a34a"), ("Trasporti", "#2563eb"),
                            ("Salute", "#db2777"), ("Utenze", "#7c3aed"), ("Altro", "#64748b")]:
            db.add(Category(household_id=household_id, name=name, color=color))
    db.commit()
    token, csrf = create_session(db, user, request)
    set_cookie(response, token)
    return AuthOutput(user=UserOutput.model_validate(user), csrf_token=csrf)


@router.post("/login", response_model=AuthOutput)
@limiter.limit("10/minute")
def login(request: Request, body: LoginInput, response: Response,
          db: Session = Depends(get_db)) -> AuthOutput:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not password_hash.verify(body.password, user.password_hash):
        raise HTTPException(401, "Email, password o codice di verifica non validi")
    if user.mfa_enabled and not verify_totp(user, body.totp_code):
        supplied_hash = hash_value(body.totp_code or "")
        codes = (user.recovery_codes or "").splitlines()
        if supplied_hash not in codes:
            raise HTTPException(401, "Email, password o codice di verifica non validi")
        user.recovery_codes = "\n".join(code for code in codes if code != supplied_hash)
        db.commit()
    token, csrf = create_session(db, user, request)
    set_cookie(response, token)
    return AuthOutput(user=UserOutput.model_validate(user), csrf_token=csrf)


@router.post("/logout", dependencies=[Depends(csrf_check)])
def logout(response: Response, session: UserSession = Depends(current_session),
           db: Session = Depends(get_db)) -> dict[str, bool]:
    db.delete(session)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOutput)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.get("/session")
def refresh_csrf(session: UserSession = Depends(current_session)) -> dict[str, str]:
    return {"csrf_token": session.csrf_token}


@router.post("/mfa/setup", dependencies=[Depends(csrf_check)])
def mfa_setup(user: User = Depends(current_user),
              db: Session = Depends(get_db)) -> dict[str, str]:
    if user.mfa_enabled:
        raise HTTPException(409, "MFA già attiva")
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="ExpenseLens")
    image = qrcode.make(uri)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    user.mfa_secret = secret
    user.mfa_enabled = False
    db.commit()
    return {"secret": secret, "qr_data_url": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()}


@router.post("/mfa/enable", dependencies=[Depends(csrf_check)])
def mfa_enable(body: MfaCodeInput, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict[str, list[str]]:
    code = body.code
    if not user.mfa_secret or not pyotp.TOTP(user.mfa_secret).verify(code, valid_window=1):
        raise HTTPException(400, "Codice MFA non valido")
    codes = [secrets.token_hex(5) for _ in range(8)]
    user.mfa_enabled = True
    user.recovery_codes = "\n".join(hash_value(c) for c in codes)
    db.commit()
    return {"recovery_codes": codes}


@router.post("/mfa/disable", dependencies=[Depends(csrf_check)])
def mfa_disable(body: MfaCodeInput, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict[str, bool]:
    if not user.mfa_enabled or not user.mfa_secret or not pyotp.TOTP(user.mfa_secret).verify(body.code, valid_window=1):
        raise HTTPException(400, "Codice MFA non valido")
    user.mfa_enabled = False
    user.mfa_secret = None
    user.recovery_codes = None
    db.commit()
    return {"ok": True}


@router.post("/telegram/link-token", dependencies=[Depends(csrf_check)])
def telegram_link_token(user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict[str, str | int]:
    token = secrets.token_urlsafe(24)
    db.add(TelegramLinkToken(token_hash=hash_value(token), user_id=user.id,
                             expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    db.commit()
    return {"token": token, "expires_in_seconds": 600}


@router.post("/household/invite", dependencies=[Depends(csrf_check)])
def create_household_invite(user: User = Depends(household_admin),
                            db: Session = Depends(get_db)) -> dict[str, str | int]:
    token = secrets.token_urlsafe(24)
    db.add(HouseholdInvite(token_hash=hash_value(token), household_id=user.household_id,
                           expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
    db.commit()
    return {"token": token, "expires_in_seconds": 604800}
