from __future__ import annotations

import argparse
from datetime import UTC, datetime
from getpass import getpass
import os
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models.auth import UserCredential  # noqa: E402
from app.models.user import User  # noqa: E402
from app.security import get_password_hash  # noqa: E402


def _read_password(env_var: str | None) -> str:
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value
    password = getpass("New password: ")
    confirm_password = getpass("Confirm password: ")
    if password != confirm_password:
        raise SystemExit("Passwords do not match.")
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset a local user's password safely.")
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--password-env",
        default=None,
        help="Read the new password from this environment variable instead of prompting.",
    )
    args = parser.parse_args()

    password = _read_password(args.password_env)
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    if len(password.encode("utf-8")) > 72:
        raise SystemExit("Password must be 72 bytes or fewer.")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            raise SystemExit(f"No user found for email {args.email!r}.")
        credential = db.scalar(select(UserCredential).where(UserCredential.user_id == user.id))
        if credential is None:
            credential = UserCredential(user_id=user.id, password_hash=get_password_hash(password))
            db.add(credential)
        else:
            credential.password_hash = get_password_hash(password)
            credential.password_updated_at = datetime.now(UTC)
        db.commit()
        print(f"Password reset complete for {user.email} (@{user.user_name}).")


if __name__ == "__main__":
    main()
