from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import bcrypt
from fastapi import Depends, HTTPException, status
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


def access_token_expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)


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


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_debug_user(db: Session) -> User:
    debug_email = settings.debug_auth_user_email.lower()
    user = db.scalar(select(User).where(User.email == debug_email))
    if user is None:
        user = db.scalar(select(User).where(User.user_name == settings.debug_auth_user_name))
    if user is None:
        user = User(
            name=settings.debug_auth_name,
            user_name=settings.debug_auth_user_name,
            email=debug_email,
            starting_cash=1000000,
            current_cash=1000000,
            risk_profile="moderate",
        )
        db.add(user)
        db.flush()
    else:
        user.name = settings.debug_auth_name
        user.user_name = settings.debug_auth_user_name
        user.email = debug_email
    from app.services.portfolio_service import create_default_portfolios_for_user

    create_default_portfolios_for_user(db, user.id)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    if settings.debug_auth_bypass:
        return get_debug_user(db)

    credentials_error = _credentials_error()
    if token is None:
        raise credentials_error
    try:
        payload = decode_token_payload(token)
        user_id = payload.get("sub")
        token_jti = payload.get("jti")
        if user_id is None or token_jti is None:
            raise credentials_error
    except (JWTError, ValueError) as exc:
        raise credentials_error from exc

    try:
        parsed_user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise credentials_error from exc

    session = db.scalar(
        select(AuthSession).where(
            AuthSession.user_id == parsed_user_id,
            AuthSession.token_jti_hash == hash_token_identifier(str(token_jti)),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    if session is None:
        raise credentials_error

    user = db.get(User, parsed_user_id)
    if user is None:
        raise credentials_error
    return user


def revoke_token_session(db: Session, token: str) -> bool:
    try:
        payload = decode_token_payload(token)
        user_id = int(payload.get("sub"))
        token_jti = str(payload.get("jti"))
    except (JWTError, TypeError, ValueError):
        return False

    session = db.scalar(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.token_jti_hash == hash_token_identifier(token_jti),
            AuthSession.revoked_at.is_(None),
        )
    )
    if session is None:
        return False
    session.revoked_at = datetime.now(UTC)
    db.commit()
    return True
