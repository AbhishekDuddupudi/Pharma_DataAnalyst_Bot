"""
Cookie configuration constants.

Centralised so every part of the app uses identical settings.
When moving to production, flip SECURE to True and set DOMAIN.
"""

from __future__ import annotations

from app.core.config import settings

# Cookie name used for the session token
SESSION_COOKIE = "session_id"

# How long a session lasts (seconds) – 7 days
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

# Cookie flags – tuned for local dev; override via APP_ENV for prod
_is_prod = settings.APP_ENV == "production"

COOKIE_SETTINGS: dict = {
    "key": SESSION_COOKIE,
    "httponly": True,
    "samesite": "lax",
    "secure": _is_prod,          # True in production (HTTPS only)
    "max_age": SESSION_MAX_AGE_SECONDS,
    "path": "/",
    # "domain": ".yourdomain.com",  # uncomment for prod multi-subdomain
}

DELETE_COOKIE_SETTINGS: dict = {
    "key": SESSION_COOKIE,
    "httponly": True,
    "samesite": "lax",
    "secure": _is_prod,
    "path": "/",
}
