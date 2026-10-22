"""Local browser sessions and CSRF protection shared by Hub APIs."""

import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request

from baseline_hub.db import now, utc
from baseline_hub.models import LoginSession, User

COOKIE = "hub_session"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def db_session(request: Request):
    with request.app.state.factory() as db:
        yield db


def settings(request: Request):
    return request.app.state.settings


def user_out(user):
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "contributor": user.contributor,
    }


def current_session(request: Request, db=Depends(db_session)):
    row = db.get(LoginSession, digest(request.cookies.get(COOKIE, "")))
    if row is None or utc(row.expires_at) <= now():
        raise HTTPException(401, "Sign in required")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), row.csrf):
            raise HTTPException(403, "CSRF token required")
    return row


def current_user(request: Request, db=Depends(db_session)):
    row = current_session(request, db)
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "Account no longer exists")
    return user


def issue_session(db, user, response, config, *, upstream_token=None):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=digest(token),
            user_id=user.id,
            csrf=csrf,
            expires_at=now() + timedelta(days=7),
        )
    )
    if upstream_token is not None:
        from cryptography.fernet import Fernet

        from baseline_hub.models import PlatformCredential

        db.flush()
        db.add(
            PlatformCredential(
                session_hash=digest(token),
                connection_url=config.upstream_url.rstrip("/"),
                encrypted_token=Fernet(config.credential_key.encode())
                .encrypt(upstream_token.encode())
                .decode(),
            )
        )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path=config.api_cookie_path,
        max_age=7 * 86400,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_out(user), "csrf": csrf}


def check_origin(request, config):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    origin = request.headers.get("Origin")
    parsed = urlsplit(config.public_url)
    expected = f"{parsed.scheme}://{parsed.netloc}"
    return origin is None or origin == expected
