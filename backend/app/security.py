from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import bcrypt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.auth import AuthSession
from app.models.user import User


MAX_BCRYPT_PASSWORD_BYTES = 72
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def _password_bytes(password: str) -> bytes:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > MAX_BCRYPT_PASSWORD_BYTES:
        raise ValueError("Password must be 72 bytes or fewer.")
    return password_bytes


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(plain_password), password_hash.encode("utf-8"))
    except ValueError:
        return False


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


# --- Token lifetimes ---------------------------------------------------------

def access_token_expires_at() -> datetime:
    """Short-lived expiry for header/API access tokens."""
    return datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)


def session_expires_at() -> datetime:
    """Long-lived expiry for persistent ("remember me") browser sessions."""
    return datetime.now(UTC) + timedelta(days=settings.session_max_age_days)


def create_token_jti() -> str:
    return uuid4().hex


def hash_token_identifier(token_identifier: str) -> str:
    return sha256(token_identifier.encode("utf-8")).hexdigest()


def create_access_token(
    subject: str | int,
    jti: str,
    expires_at: datetime,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    issued_at = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "jti": jti,
        "iat": issued_at,
        "exp": expires_at,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token_payload(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def safe_decode_token(token: str | None) -> dict[str, Any] | None:
    """Decode and validate a JWT, returning the payload or ``None`` on any error."""
    if not token:
        return None
    try:
        payload = decode_token_payload(token)
    except (JWTError, ValueError):
        return None
    if payload.get("sub") is None or payload.get("jti") is None:
        return None
    return payload


# --- Session cookie helpers --------------------------------------------------

def session_cookie_max_age_seconds() -> int:
    return int(settings.session_max_age_days) * 24 * 3600


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the persistent session cookie to a response (httpOnly, sliding)."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=session_cookie_max_age_seconds(),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path="/")


def token_from_request(request: Request, header_token: str | None = None) -> str | None:
    """Resolve the bearer token from the Authorization header first, then the
    session cookie. Header precedence keeps programmatic API clients working."""
    if header_token:
        return header_token
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return request.cookies.get(settings.session_cookie_name)


# --- Session resolution + sliding renewal ------------------------------------

def lookup_session(
    db: Session, payload: dict[str, Any]
) -> tuple[User | None, AuthSession | None]:
    """Validate a decoded token payload against a live, non-revoked AuthSession."""
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None, None
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.token_jti_hash == hash_token_identifier(str(payload.get("jti"))),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    if session is None:
        return None, None
    user = db.get(User, user_id)
    if user is None:
        return None, None
    return user, session


def renew_session_if_needed(
    db: Session, session: AuthSession, payload: dict[str, Any]
) -> str | None:
    """Slide the session forward if it has been used past the renew threshold.

    Returns a freshly minted JWT (to reissue in the cookie) when renewed, else
    ``None``. This is what makes an active user stay "logged in until logout":
    every visit extends the absolute expiry back out to the full window.
    """
    now = datetime.now(UTC)
    full = timedelta(days=settings.session_max_age_days)
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    elapsed = full - (expires - now)
    if elapsed <= timedelta(days=settings.session_renew_after_days):
        return None
    new_expires = now + full
    session.expires_at = new_expires
    db.commit()
    return create_access_token(payload["sub"], str(payload["jti"]), new_expires)


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Return the authenticated user.

    The auth middleware resolves the session once per request and stores the
    user id on ``request.state``; this dependency loads it in the request's own
    DB session (a cheap PK lookup). A direct fallback keeps header-only API
    callers working even if middleware did not populate state.
    """
    user_id = getattr(request.state, "auth_user_id", None)
    if user_id is not None:
        user = db.get(User, user_id)
        if user is not None:
            return user

    payload = safe_decode_token(token_from_request(request, token))
    if payload is not None:
        user, _session = lookup_session(db, payload)
        if user is not None:
            return user
    raise _credentials_error()


def get_optional_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Like :func:`get_current_user` but returns ``None`` instead of raising."""
    try:
        return get_current_user(request, token, db)
    except HTTPException:
        return None


def revoke_token_session(db: Session, token: str) -> bool:
    payload = safe_decode_token(token)
    if payload is None:
        return False
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return False
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.token_jti_hash == hash_token_identifier(str(payload.get("jti"))),
            AuthSession.revoked_at.is_(None),
        )
    )
    if session is None:
        return False
    session.revoked_at = datetime.now(UTC)
    db.commit()
    return True
