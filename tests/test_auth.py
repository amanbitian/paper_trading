"""Tests for cookie-based authentication: token extraction, JWT round-trip,
sliding-session renewal, and the middleware's public-path gating.

These import the real app modules, which require Python 3.11+ (the project's
.venv), like the rest of the suite. None of them need a live database — the
DB-dependent helpers are exercised with light stubs.
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "backend"))

from app.config import settings  # noqa: E402
from app import security  # noqa: E402
from app.auth_middleware import _is_public  # noqa: E402


class _StubRequest:
    """Minimal stand-in for a Starlette Request (headers + cookies only)."""

    def __init__(self, headers: dict | None = None, cookies: dict | None = None):
        self.headers = headers or {}
        self.cookies = cookies or {}


class _StubDB:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class _StubSession:
    def __init__(self, expires_at):
        self.expires_at = expires_at


class TokenExtractionTests(unittest.TestCase):
    def test_header_takes_precedence(self):
        req = _StubRequest(
            headers={"authorization": "Bearer headertoken"},
            cookies={settings.session_cookie_name: "cookietoken"},
        )
        self.assertEqual(security.token_from_request(req), "headertoken")

    def test_falls_back_to_cookie(self):
        req = _StubRequest(cookies={settings.session_cookie_name: "cookietoken"})
        self.assertEqual(security.token_from_request(req), "cookietoken")

    def test_explicit_header_token_wins(self):
        req = _StubRequest(cookies={settings.session_cookie_name: "cookietoken"})
        self.assertEqual(security.token_from_request(req, "explicit"), "explicit")

    def test_none_when_absent(self):
        self.assertIsNone(security.token_from_request(_StubRequest()))


class JwtRoundTripTests(unittest.TestCase):
    def test_create_then_safe_decode(self):
        expires = datetime.now(UTC) + timedelta(days=1)
        token = security.create_access_token(123, "jti-abc", expires)
        payload = security.safe_decode_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "123")
        self.assertEqual(payload["jti"], "jti-abc")

    def test_safe_decode_rejects_garbage(self):
        self.assertIsNone(security.safe_decode_token("not-a-jwt"))
        self.assertIsNone(security.safe_decode_token(None))
        self.assertIsNone(security.safe_decode_token(""))

    def test_safe_decode_rejects_expired(self):
        expired = datetime.now(UTC) - timedelta(minutes=1)
        token = security.create_access_token(1, "j", expired)
        self.assertIsNone(security.safe_decode_token(token))


class SlidingRenewalTests(unittest.TestCase):
    def setUp(self):
        self._orig_max = settings.session_max_age_days
        self._orig_renew = settings.session_renew_after_days
        settings.session_max_age_days = 365
        settings.session_renew_after_days = 1

    def tearDown(self):
        settings.session_max_age_days = self._orig_max
        settings.session_renew_after_days = self._orig_renew

    def test_fresh_session_not_renewed(self):
        db = _StubDB()
        session = _StubSession(datetime.now(UTC) + timedelta(days=365))
        result = security.renew_session_if_needed(db, session, {"sub": "1", "jti": "j"})
        self.assertIsNone(result)
        self.assertEqual(db.commits, 0)

    def test_aged_session_is_renewed(self):
        db = _StubDB()
        session = _StubSession(datetime.now(UTC) + timedelta(days=360))  # used ~5 days ago
        result = security.renew_session_if_needed(db, session, {"sub": "1", "jti": "j"})
        self.assertIsNotNone(result)            # a fresh JWT is returned for reissue
        self.assertEqual(db.commits, 1)
        self.assertGreater(session.expires_at, datetime.now(UTC) + timedelta(days=364))

    def test_naive_expiry_handled(self):
        db = _StubDB()
        naive = (datetime.now(UTC) + timedelta(days=300)).replace(tzinfo=None)
        session = _StubSession(naive)
        result = security.renew_session_if_needed(db, session, {"sub": "1", "jti": "j"})
        self.assertIsNotNone(result)


class GatePathTests(unittest.TestCase):
    def test_public_paths(self):
        for path in ["/", "/health", "/web/login", "/web/register", "/auth/login", "/static/css/app.css"]:
            self.assertTrue(_is_public(path), path)

    def test_protected_paths(self):
        for path in ["/web/strategy_lab", "/web/partials/portfolio/list", "/portfolios", "/web/explore"]:
            self.assertFalse(_is_public(path), path)


if __name__ == "__main__":
    unittest.main()
