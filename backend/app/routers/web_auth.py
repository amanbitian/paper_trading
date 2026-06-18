"""Server-rendered authentication pages (login / register / logout).

These set and clear the persistent httpOnly session cookie so the browser stays
logged in across restarts until the user explicitly logs out.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.limiter import limiter
from app.models.auth import AuthSession, UserCredential
from app.models.user import User
from app.schemas.auth import UserCreate
from app.security import (
    clear_session_cookie,
    create_access_token,
    create_token_jti,
    hash_token_identifier,
    get_password_hash,
    session_expires_at,
    set_session_cookie,
    token_from_request,
    verify_password,
    revoke_token_session,
)
from app.services.portfolio_service import create_default_portfolios_for_user
from app.web_utils import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/web", tags=["web-auth"])

DEFAULT_REDIRECT = "/web/explore"


def _safe_next(next_url: str | None) -> str:
    """Only allow same-site relative redirects to avoid open-redirect abuse."""
    if not next_url:
        return DEFAULT_REDIRECT
    if not next_url.startswith("/") or next_url.startswith("//"):
        return DEFAULT_REDIRECT
    return next_url


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else None


def _issue_session(request: Request, db: Session, user: User) -> str:
    """Create a persistent AuthSession and return the signed JWT for the cookie."""
    expires_at = session_expires_at()
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
    return create_access_token(user.id, token_jti, expires_at)


def _redirect_with_session(request: Request, db: Session, user: User, target: str) -> RedirectResponse:
    token = _issue_session(request, db, user)
    response = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, token)
    return response


# --- Login -------------------------------------------------------------------

@router.get("/login", include_in_schema=False)
def login_page(request: Request, next: str | None = None):
    if getattr(request.state, "auth_user_id", None) is not None:
        return RedirectResponse(url=_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "pages/login.html",
        {"request": request, "title": "Log in", "next": next or "", "error": None},
    )


@router.post("/login", include_in_schema=False)
@limiter.limit("10/minute")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    normalized = email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized))
    credential = (
        db.scalar(select(UserCredential).where(UserCredential.user_id == user.id)) if user else None
    )
    if credential is None or not verify_password(password, credential.password_hash):
        return templates.TemplateResponse(
            "pages/login.html",
            {
                "request": request,
                "title": "Log in",
                "next": next,
                "error": "Invalid email or password.",
                "email": email,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return _redirect_with_session(request, db, user, _safe_next(next))


# --- Register ----------------------------------------------------------------

@router.get("/register", include_in_schema=False)
def register_page(request: Request):
    if getattr(request.state, "auth_user_id", None) is not None:
        return RedirectResponse(url=DEFAULT_REDIRECT, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "pages/register.html",
        {"request": request, "title": "Create account", "error": None, "form": {}},
    )


@router.post("/register", include_in_schema=False)
@limiter.limit("5/minute")
def register_submit(
    request: Request,
    name: str = Form(...),
    user_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    starting_cash: str = Form("1000000"),
    db: Session = Depends(get_db),
):
    form_values = {"name": name, "user_name": user_name, "email": email, "starting_cash": starting_cash}

    def _fail(message: str, code: int = status.HTTP_400_BAD_REQUEST):
        return templates.TemplateResponse(
            "pages/register.html",
            {"request": request, "title": "Create account", "error": message, "form": form_values},
            status_code=code,
        )

    try:
        payload = UserCreate(
            name=name,
            user_name=user_name,
            email=email,
            password=password,
            starting_cash=starting_cash or "1000000",
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        return _fail(str(first.get("msg", "Please check the details and try again.")))

    if db.scalar(select(User).where(User.email == payload.email.lower())):
        return _fail("That email is already registered. Please log in instead.")
    if db.scalar(select(User).where(User.user_name == payload.user_name)):
        return _fail("That username is already taken.")

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
        db.add(UserCredential(user_id=user.id, password_hash=get_password_hash(payload.password)))
        create_default_portfolios_for_user(db, user.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        return _fail("That email or username is already registered.")
    db.refresh(user)
    return _redirect_with_session(request, db, user, DEFAULT_REDIRECT)


# --- Logout ------------------------------------------------------------------

@router.api_route("/logout", methods=["GET", "POST"], include_in_schema=False)
def logout(request: Request, db: Session = Depends(get_db)):
    token = token_from_request(request)
    if token:
        try:
            revoke_token_session(db, token)
        except Exception:
            logger.exception("Logout revoke failed (clearing cookie anyway)")
    response = RedirectResponse(url="/web/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response
