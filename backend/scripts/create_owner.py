"""
One-time owner/admin user creation.

Creates or updates exactly ONE owner user. Use this instead of seeding a demo
user in SQL. Run it once after the database schema has been applied.

Normal (interactive) usage — run from the backend/ directory:

    python -m scripts.create_owner

You will be prompted for the email (unless OWNER_EMAIL is set) and for the
password twice, with hidden input. The raw password is never printed, logged,
or stored — only its bcrypt hash is written to the database.

Automation / testing only:

    OWNER_EMAIL=owner@example.com OWNER_PASSWORD=... python -m scripts.create_owner

Setting OWNER_PASSWORD skips the interactive prompt. Avoid this in normal use;
it exists so CI/tests can run non-interactively.

Environment variables:
    OWNER_EMAIL          Owner email (prompted if missing).
    OWNER_DISPLAY_NAME   Display name (default: "Owner").
    OWNER_PASSWORD       OPTIONAL automation-only password (avoid in normal use).
    DATABASE_URL         Standard app DB connection string (required).
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys

from app.services.auth_service import _get_conn, hash_password

MIN_PASSWORD_LEN = 8


def _resolve_email() -> str:
    """Use OWNER_EMAIL if set, otherwise prompt."""
    email = os.getenv("OWNER_EMAIL", "").strip()
    if not email:
        email = input("Owner email: ").strip()
    if not email or "@" not in email:
        sys.exit("A valid owner email is required.")
    return email


def _resolve_password() -> str:
    """Prompt for the password twice, or use OWNER_PASSWORD for automation."""
    env_pw = os.getenv("OWNER_PASSWORD")
    if env_pw:  # automation/testing path only
        if len(env_pw) < MIN_PASSWORD_LEN:
            sys.exit(f"OWNER_PASSWORD must be at least {MIN_PASSWORD_LEN} characters.")
        return env_pw

    pw = getpass.getpass("Owner password: ")
    confirm = getpass.getpass("Confirm password: ")
    if pw != confirm:
        sys.exit("Passwords do not match.")
    if len(pw) < MIN_PASSWORD_LEN:
        sys.exit(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    return pw


async def _upsert_owner(email: str, display_name: str, password_hash: str) -> None:
    """Insert the owner, or update password/display_name if the email exists."""
    conn = await _get_conn()
    try:
        await conn.execute(
            "INSERT INTO app_user (email, password_hash, display_name) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (email) DO UPDATE SET "
            "    password_hash = EXCLUDED.password_hash, "
            "    display_name  = EXCLUDED.display_name",
            email,
            password_hash,
            display_name,
        )
    finally:
        await conn.close()


def main() -> None:
    email = _resolve_email()
    display_name = os.getenv("OWNER_DISPLAY_NAME", "Owner").strip() or "Owner"
    password = _resolve_password()

    password_hash = hash_password(password)
    asyncio.run(_upsert_owner(email, display_name, password_hash))

    print(f"Owner user created/updated for {email}")


if __name__ == "__main__":
    main()
