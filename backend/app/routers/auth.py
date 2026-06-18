import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.config import settings
from app.limiter import limiter
from app.models.auth import AuthSession, PasswordResetToken, UserCredential
from app.models.user import User
from app.schemas.auth import ForgotPasswordRequest, ResetPasswordRequest, Token, UserCreate, UserLogin, UserRead
from app.services.email_service import send_reset_email
from app.security import (
    access_token_expires_at,
    create_access_token,
    create_token_jti,
    get_current_user,
    get_password_hash,
    hash_token_identifier,
    revoke_token_session,
    verify_password,
)
from app.services.portfolio_service import create_default_portfolios_for_user
from app.utils.observability import timed


router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else None


def _log_password_check_debug(
    *,
    email: str,
    user_exists: bool,
    credential_exists: bool,
    entered_password: str,
    password_verified: bool,
    credential: UserCredential | None,
) -> None:
    if not settings.auth_debug_log_password_checks:
        return
    stored_hash = credential.password_hash if credential else ""
    logger.warning(
        "auth_debug_password_check email=%s user_exists=%s credential_exists=%s "
        "entered_password_chars=%s entered_password_bytes=%s stored_hash_scheme=%s "
        "stored_hash_length=%s password_verified=%s",
        email,
        user_exists,
        credential_exists,
        len(entered_password),
        len(entered_password.encode("utf-8")),
        "bcrypt" if stored_hash.startswith("$2") else "unknown",
        len(stored_hash),
        password_verified,
    )


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
@timed("auth.register")
def register(request: Request, payload: UserCreate, db: Session = Depends(get_db)) -> User:
    existing_email = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered. Please log in.")
    existing_user_name = db.scalar(select(User).where(User.user_name == payload.user_name))
    if existing_user_name:
        raise HTTPException(status_code=400, detail="Username already taken")
    user = User(
        name=payload.name,
        user_name=payload.user_name,
        email=payload.email.lower(),
        starting_cash=payload.starting_cash,
        current_cash=payload.starting_cash,
        risk_profile=payload.risk_profile,
    )
    try:
        db.add(user)
        db.flush()
        db.add(
            UserCredential(
                user_id=user.id,
                password_hash=get_password_hash(payload.password),
            )
        )
        create_default_portfolios_for_user(db, user.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Email or username already registered",
        ) from exc
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
@timed("auth.login")
def login(request: Request, payload: UserLogin, db: Session = Depends(get_db)) -> Token:
    email = payload.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    credential = (
        db.scalar(select(UserCredential).where(UserCredential.user_id == user.id))
        if user
        else None
    )
    password_verified = bool(
        credential and verify_password(payload.password, credential.password_hash)
    )
    _log_password_check_debug(
        email=email,
        user_exists=user is not None,
        credential_exists=credential is not None,
        entered_password=payload.password,
        password_verified=password_verified,
        credential=credential,
    )
    if credential is None or not password_verified:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    expires_at = access_token_expires_at()
    token_jti = create_token_jti()
    db.add(
        AuthSession(
            user_id=user.id,
            token_jti_hash=hash_token_identifier(token_jti),
            user_agent=request.headers.get("user-agent"),
            ip_address=_client_ip(request),
            expires_at=expires_at,
        )
    )
    db.commit()
    return Token(
        access_token=create_access_token(user.id, token_jti, expires_at),
        expires_at=expires_at,
    )


@router.get("/me", response_model=UserRead)
@timed("auth.me")
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/logout")
@timed("auth.logout")
def logout(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict[str, bool]:
    return {"revoked": revoke_token_session(db, token)}


@router.post("/forgot-password")
@limiter.limit("3/minute")
@timed("auth.forgot_password")
def forgot_password(
    request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)
) -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token_identifier(raw_token)
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.password_reset_token_expire_minutes)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )
        db.commit()
        send_reset_email(user.email, raw_token)
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
@limiter.limit("5/minute")
@timed("auth.reset_password")
def reset_password(
    request: Request, payload: ResetPasswordRequest, db: Session = Depends(get_db)
) -> dict[str, str]:
    token_hash = hash_token_identifier(payload.token)
    row = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > datetime.now(UTC),
        )
    )
    if row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    credential = db.scalar(select(UserCredential).where(UserCredential.user_id == row.user_id))
    if credential is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    credential.password_hash = get_password_hash(payload.new_password)
    credential.password_updated_at = datetime.now(UTC)
    row.used_at = datetime.now(UTC)
    sessions = list(db.scalars(select(AuthSession).where(AuthSession.user_id == row.user_id)))
    for session in sessions:
        session.revoked_at = datetime.now(UTC)
    db.commit()
    return {"message": "Password reset successful. Please log in again."}
