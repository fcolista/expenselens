import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.budgets import BASE_CURRENCY, budget_status, converted_total, household_rates
from app.config import settings
from app.database import get_db
from app.limits import limiter
from app.models import ExchangeRate, Expense, PasskeyCredential, TelegramUser, User, UserSession
from app.routers.auth import set_cookie
from app.routers.expenses import base_query
from app.schemas import AuthOutput, UserOutput
from app.security import (
    create_session, csrf_check, current_session, current_user, household_admin, password_hash,
    verify_totp,
)

router = APIRouter(prefix="/api", tags=["account"])


# ---------- password ----------

class PasswordChange(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    totp_code: str | None = Field(default=None, max_length=32, pattern=r"^(?:\d{6}|[0-9a-f]{10})$")


@router.post("/auth/password", dependencies=[Depends(csrf_check)])
@limiter.limit("5/minute")
def change_password(request: Request, body: PasswordChange, user: User = Depends(current_user),
                    current: UserSession = Depends(current_session),
                    db: Session = Depends(get_db)) -> dict[str, int]:
    if not password_hash.verify(body.current_password, user.password_hash):
        raise HTTPException(400, "Password attuale non corretta")
    if user.mfa_enabled and not verify_totp(user, body.totp_code):
        raise HTTPException(400, "Codice di verifica non valido")
    if body.new_password == body.current_password:
        raise HTTPException(422, "La nuova password deve essere diversa da quella attuale")
    user.password_hash = password_hash.hash(body.new_password)
    others = db.scalars(select(UserSession).where(UserSession.user_id == user.id,
                                                  UserSession.id != current.id)).all()
    for item in others:
        db.delete(item)
    db.commit()
    return {"revoked": len(others)}


# ---------- active sessions ----------

def ensure_public_ids(db: Session, sessions: list[UserSession]) -> None:
    changed = False
    for item in sessions:
        if not item.public_id:
            item.public_id = secrets.token_urlsafe(12)
            changed = True
    if changed:
        db.commit()


@router.get("/auth/sessions")
def list_sessions(user: User = Depends(current_user), current: UserSession = Depends(current_session),
                  db: Session = Depends(get_db)) -> list[dict]:
    now = datetime.now(timezone.utc)
    sessions = [s for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id)
                                      .order_by(UserSession.created_at.desc())).all()
                if (s.expires_at if s.expires_at.tzinfo else s.expires_at.replace(tzinfo=timezone.utc)) > now]
    ensure_public_ids(db, sessions)
    return [{"id": s.public_id, "current": s.id == current.id, "created_at": s.created_at,
             "last_seen_at": s.last_seen_at, "user_agent": s.user_agent, "method": s.method}
            for s in sessions]


@router.delete("/auth/sessions/{public_id}", status_code=204, dependencies=[Depends(csrf_check)])
def revoke_session(public_id: str, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> None:
    item = db.scalar(select(UserSession).where(UserSession.public_id == public_id,
                                               UserSession.user_id == user.id))
    if not item:
        raise HTTPException(404, "Sessione non trovata")
    db.delete(item)
    db.commit()


@router.post("/auth/sessions/revoke-others", dependencies=[Depends(csrf_check)])
def revoke_other_sessions(user: User = Depends(current_user),
                          current: UserSession = Depends(current_session),
                          db: Session = Depends(get_db)) -> dict[str, int]:
    others = db.scalars(select(UserSession).where(UserSession.user_id == user.id,
                                                  UserSession.id != current.id)).all()
    for item in others:
        db.delete(item)
    db.commit()
    return {"revoked": len(others)}


# ---------- passkeys (WebAuthn) ----------

CHALLENGE_TTL = 300


def rp_id() -> str:
    return settings.webauthn_rp_id or (urlparse(settings.frontend_origin).hostname or "localhost")


def expected_origin() -> str:
    return settings.frontend_origin.rstrip("/")


def sign_challenge(challenge: bytes, purpose: str, user_id: int = 0) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({
        "c": base64.urlsafe_b64encode(challenge).decode(), "p": purpose, "u": user_id,
        "e": int(time.time()) + CHALLENGE_TTL}).encode()).decode()
    mac = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def open_challenge(token: str, purpose: str, user_id: int = 0) -> bytes:
    try:
        payload, mac = token.rsplit(".", 1)
        expected = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        data = json.loads(base64.urlsafe_b64decode(payload))
        if (not hmac.compare_digest(mac, expected) or data["p"] != purpose or data["u"] != user_id
                or data["e"] < time.time()):
            raise ValueError
        return base64.urlsafe_b64decode(data["c"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Richiesta passkey non valida o scaduta") from None


class PasskeyFinish(BaseModel):
    challenge_token: str = Field(max_length=2000)
    credential: dict
    name: str = Field(default="Passkey", max_length=80)


class PasskeyLoginFinish(BaseModel):
    challenge_token: str = Field(max_length=2000)
    credential: dict


@router.get("/auth/passkeys")
def list_passkeys(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id)
                      .order_by(PasskeyCredential.created_at)).all()
    return [{"id": r.id, "name": r.name, "created_at": r.created_at, "last_used_at": r.last_used_at}
            for r in rows]


@router.post("/auth/passkeys/register/options", dependencies=[Depends(csrf_check)])
def passkey_register_options(user: User = Depends(current_user),
                             db: Session = Depends(get_db)) -> dict:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                          ResidentKeyRequirement, UserVerificationRequirement)

    existing = db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id)).all()
    options = generate_registration_options(
        rp_id=rp_id(), rp_name=settings.webauthn_rp_name, user_id=str(user.id).encode(),
        user_name=user.email, user_display_name=user.display_name,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
                             for c in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED))
    return {"options": json.loads(options_to_json(options)),
            "challenge_token": sign_challenge(options.challenge, "reg", user.id)}


@router.post("/auth/passkeys/register/verify", status_code=201, dependencies=[Depends(csrf_check)])
def passkey_register_verify(body: PasskeyFinish, user: User = Depends(current_user),
                            db: Session = Depends(get_db)) -> dict:
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url
    from webauthn.helpers.exceptions import InvalidRegistrationResponse

    challenge = open_challenge(body.challenge_token, "reg", user.id)
    try:
        verified = verify_registration_response(
            credential=body.credential, expected_challenge=challenge, expected_rp_id=rp_id(),
            expected_origin=expected_origin(), require_user_verification=True)
    except (InvalidRegistrationResponse, ValueError, KeyError):
        raise HTTPException(400, "Registrazione passkey non valida") from None
    credential_id = bytes_to_base64url(verified.credential_id)
    if db.scalar(select(PasskeyCredential.id).where(PasskeyCredential.credential_id == credential_id)):
        raise HTTPException(409, "Passkey già registrata")
    item = PasskeyCredential(
        user_id=user.id, credential_id=credential_id, name=body.name.strip() or "Passkey",
        public_key=bytes_to_base64url(verified.credential_public_key), sign_count=verified.sign_count)
    db.add(item)
    db.commit()
    return {"id": item.id, "name": item.name}


@router.delete("/auth/passkeys/{passkey_id}", status_code=204, dependencies=[Depends(csrf_check)])
def delete_passkey(passkey_id: int, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> None:
    item = db.scalar(select(PasskeyCredential).where(PasskeyCredential.id == passkey_id,
                                                     PasskeyCredential.user_id == user.id))
    if not item:
        raise HTTPException(404, "Passkey non trovata")
    db.delete(item)
    db.commit()


@router.post("/auth/passkeys/login/options")
@limiter.limit("20/minute")
def passkey_login_options(request: Request) -> dict:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement

    options = generate_authentication_options(
        rp_id=rp_id(), user_verification=UserVerificationRequirement.REQUIRED)
    return {"options": json.loads(options_to_json(options)),
            "challenge_token": sign_challenge(options.challenge, "auth")}


@router.post("/auth/passkeys/login/verify", response_model=AuthOutput)
@limiter.limit("10/minute")
def passkey_login_verify(request: Request, body: PasskeyLoginFinish, response: Response,
                         db: Session = Depends(get_db)) -> AuthOutput:
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
    from webauthn.helpers.exceptions import InvalidAuthenticationResponse

    challenge = open_challenge(body.challenge_token, "auth")
    credential = db.scalar(select(PasskeyCredential).where(
        PasskeyCredential.credential_id == str(body.credential.get("id", ""))))
    if not credential:
        raise HTTPException(401, "Passkey non riconosciuta")
    try:
        verified = verify_authentication_response(
            credential=body.credential, expected_challenge=challenge, expected_rp_id=rp_id(),
            expected_origin=expected_origin(),
            credential_public_key=base64url_to_bytes(credential.public_key),
            credential_current_sign_count=credential.sign_count, require_user_verification=True)
    except (InvalidAuthenticationResponse, ValueError, KeyError):
        raise HTTPException(401, "Passkey non valida") from None
    credential.sign_count = verified.new_sign_count
    credential.last_used_at = datetime.now(timezone.utc)
    user = db.get(User, credential.user_id)
    db.commit()
    token, csrf = create_session(db, user, request, method="passkey")
    set_cookie(response, token)
    return AuthOutput(user=UserOutput.model_validate(user), csrf_token=csrf)


# ---------- budgets ----------

@router.get("/budgets")
def budgets(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"currency": BASE_CURRENCY, "items": budget_status(db, user.household_id, user.id, date.today())}


# ---------- exchange rates ----------

class RateInput(BaseModel):
    rate: Decimal = Field(gt=0, max_digits=18, decimal_places=6)


def _currency(code: str) -> str:
    code = code.upper()
    if len(code) != 3 or not code.isalpha() or code == BASE_CURRENCY:
        raise HTTPException(422, "Valuta non valida")
    return code


@router.get("/rates")
def rates(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(ExchangeRate).where(ExchangeRate.household_id == user.household_id)
                      .order_by(ExchangeRate.currency)).all()
    used = db.scalars(select(distinct(Expense.currency)).where(
        Expense.household_id == user.household_id)).all()
    return {"base": BASE_CURRENCY, "auto": settings.rates_provider == "frankfurter",
            "rates": [{"currency": r.currency, "rate": r.rate, "updated_at": r.updated_at} for r in rows],
            "used_currencies": sorted(c for c in used if c != BASE_CURRENCY)}


@router.put("/rates/{currency}", dependencies=[Depends(csrf_check)])
def set_rate(currency: str, body: RateInput, user: User = Depends(household_admin),
             db: Session = Depends(get_db)) -> dict:
    code = _currency(currency)
    item = db.get(ExchangeRate, (user.household_id, code))
    if item:
        item.rate, item.updated_at = body.rate, datetime.now(timezone.utc)
    else:
        db.add(ExchangeRate(household_id=user.household_id, currency=code, rate=body.rate))
    db.commit()
    return {"currency": code, "rate": body.rate}


@router.delete("/rates/{currency}", status_code=204, dependencies=[Depends(csrf_check)])
def delete_rate(currency: str, user: User = Depends(household_admin),
                db: Session = Depends(get_db)) -> None:
    item = db.get(ExchangeRate, (user.household_id, _currency(currency)))
    if item:
        db.delete(item)
        db.commit()


@router.post("/rates/refresh", dependencies=[Depends(csrf_check)])
def refresh_rates(user: User = Depends(household_admin), db: Session = Depends(get_db)) -> dict:
    if settings.rates_provider != "frankfurter":
        raise HTTPException(404, "Aggiornamento automatico dei cambi non abilitato")
    symbols = set(db.scalars(select(distinct(Expense.currency)).where(
        Expense.household_id == user.household_id)).all())
    symbols |= set(db.scalars(select(ExchangeRate.currency).where(
        ExchangeRate.household_id == user.household_id)).all())
    symbols.discard(BASE_CURRENCY)
    if not symbols:
        return {"updated": 0}
    try:
        reply = httpx.get("https://api.frankfurter.dev/v1/latest",
                          params={"base": BASE_CURRENCY, "symbols": ",".join(sorted(symbols))},
                          timeout=10)
        reply.raise_for_status()
        fetched = reply.json()["rates"]
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(502, "Servizio dei cambi non raggiungibile") from None
    updated = 0
    for code, per_eur in fetched.items():
        if not per_eur:
            continue
        rate = (Decimal(1) / Decimal(str(per_eur))).quantize(Decimal("0.000001"))
        item = db.get(ExchangeRate, (user.household_id, code))
        if item:
            item.rate, item.updated_at = rate, datetime.now(timezone.utc)
        else:
            db.add(ExchangeRate(household_id=user.household_id, currency=code, rate=rate))
        updated += 1
    db.commit()
    return {"updated": updated}


@router.get("/rates/total")
def converted_period_total(start: date, end: date, user: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict:
    if start > end:
        raise HTTPException(422, "La data iniziale deve precedere la data finale")
    subquery = base_query(user).where(Expense.spent_on >= start, Expense.spent_on <= end).subquery()
    rows = db.execute(select(subquery.c.currency, func.sum(subquery.c.amount))
                      .group_by(subquery.c.currency)).all()
    total, missing = converted_total([(c, a) for c, a in rows], household_rates(db, user.household_id))
    return {"currency": BASE_CURRENCY, "total": total, "missing_rates": missing,
            "by_currency": [{"currency": c, "total": a} for c, a in rows]}


# ---------- Telegram preferences ----------

class TelegramPrefs(BaseModel):
    language: str | None = Field(default=None, pattern="^(it|en)$")
    notify_period: str | None = Field(default=None, pattern="^(off|weekly|monthly)$")
    notify_budget: bool | None = None


def _prefs(link: TelegramUser | None) -> dict:
    if not link:
        return {"linked": False}
    return {"linked": True, "language": link.language, "notify_period": link.notify_period,
            "notify_budget": link.notify_budget}


@router.get("/telegram/preferences")
def get_telegram_prefs(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return _prefs(db.scalar(select(TelegramUser).where(TelegramUser.user_id == user.id)))


@router.put("/telegram/preferences", dependencies=[Depends(csrf_check)])
def set_telegram_prefs(body: TelegramPrefs, user: User = Depends(current_user),
                       db: Session = Depends(get_db)) -> dict:
    link = db.scalar(select(TelegramUser).where(TelegramUser.user_id == user.id))
    if not link:
        raise HTTPException(404, "Telegram non collegato")
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(link, key, value)
    db.commit()
    return _prefs(link)
