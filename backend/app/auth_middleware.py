"""Authentication gate + persistent-session middleware.

Runs once per request and is the single place the whole app is gated:

1. Resolves the session token (cookie or ``Authorization: Bearer``), validates it
   against a live :class:`AuthSession`, and stashes ``auth_user_id`` /
   ``auth_user_display`` on ``request.state`` for routes and templates.
2. Slides the session forward (sliding renewal) so an active user stays logged in
   until they explicitly log out, reissuing the cookie when renewed.
3. For unauthenticated requests to protected paths:
   - full HTML page loads  -> 303 redirect to ``/web/login?next=<path>``
   - HTMX partial requests -> 401 with ``HX-Redirect`` so htmx swaps to login
   - JSON/API requests     -> 401 JSON
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.database import SessionLocal
from app.security import (
    lookup_session,
    renew_session_if_needed,
    safe_decode_token,
    set_session_cookie,
    token_from_request,
)

logger = logging.getLogger(__name__)

LOGIN_PATH = "/web/login"

# Paths reachable without a session. Anything else requires login.
_PUBLIC_EXACT = {"/", "/health", "/favicon.ico", "/web/login", "/web/register", "/docs", "/redoc", "/openapi.json"}
_PUBLIC_PREFIXES = ("/static/", "/auth/")  # /auth/* endpoints manage their own auth


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def _wants_html(request: Request) -> bool:
    return "text/html" in (request.headers.get("accept") or "")


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") is not None


def _is_full_page(request: Request, path: str) -> bool:
    return (
        request.method == "GET"
        and _wants_html(request)
        and not _is_htmx(request)
        and not path.startswith("/web/partials/")
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.auth_user_id = None
        request.state.auth_user_display = None

        # Never gate CORS preflight or static assets.
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if path.startswith("/static/"):
            return await call_next(request)

        reissue_token: str | None = None
        token = token_from_request(request)
        if token:
            try:
                with SessionLocal() as db:
                    payload = safe_decode_token(token)
                    if payload is not None:
                        user, session = lookup_session(db, payload)
                        if user is not None and session is not None:
                            request.state.auth_user_id = user.id
                            request.state.auth_user_display = {
                                "name": user.name,
                                "user_name": user.user_name,
                                "email": user.email,
                            }
                            reissue_token = renew_session_if_needed(db, session, payload)
            except Exception:  # never let auth resolution 500 the request
                logger.exception("Auth middleware session resolution failed")

        authenticated = request.state.auth_user_id is not None

        if not authenticated and not _is_public(path):
            if _is_full_page(request, path):
                nxt = quote(request.url.path + (f"?{request.url.query}" if request.url.query else ""), safe="")
                return RedirectResponse(url=f"{LOGIN_PATH}?next={nxt}", status_code=303)
            if _is_htmx(request):
                resp = Response(status_code=401)
                resp.headers["HX-Redirect"] = LOGIN_PATH
                return resp
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        response = await call_next(request)
        if reissue_token:
            set_session_cookie(response, reissue_token)
        return response
