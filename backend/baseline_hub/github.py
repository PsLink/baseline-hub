"""GitHub OAuth lives entirely in Hub. Binding requires the originating Hub session."""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from baseline_hub.db import now, utc
from baseline_hub.models import GithubCredential, GithubIdentity, LoginSession, OAuthFlow, User
from baseline_hub.sessions import (
    COOKIE,
    current_session,
    current_user,
    db_session,
    digest,
    issue_session,
    settings,
)

router = APIRouter(prefix="/auth/github", tags=["github"])
FLOW_COOKIE = "hub_github_flow"


def start(config, db, user=None, session=None, publishing=False):
    if not config.github_enabled:
        raise HTTPException(503, "GitHub OAuth is not configured")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    db.add(
        OAuthFlow(
            state_hash=digest(state),
            verifier=verifier,
            user_id=user.id if user else None,
            session_hash=session.token_hash if session else None,
            publishing=publishing,
            expires_at=now() + timedelta(minutes=10),
        )
    )
    db.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    url = "https://github.com/login/oauth/authorize?" + urlencode(
        {
            "client_id": config.github_client_id,
            "redirect_uri": config.github_callback,
            "state": state,
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
            **({"scope": "public_repo"} if publishing else {}),
        }
    )
    response = JSONResponse({"url": url}) if user else RedirectResponse(url, status_code=303)
    response.set_cookie(
        FLOW_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path=config.github_flow_path,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/login")
def login(config=Depends(settings), db=Depends(db_session)):
    return start(config, db)


@router.post("/bind")
def bind(
    user=Depends(current_user),
    session=Depends(current_session),
    config=Depends(settings),
    db=Depends(db_session),
):
    return start(config, db, user, session)


@router.get("/identity")
def identity(user=Depends(current_user), config=Depends(settings), db=Depends(db_session)):
    row = db.scalar(select(GithubIdentity).where(GithubIdentity.user_id == user.id))
    credential = db.get(GithubCredential, user.id)
    return {
        "enabled": config.github_enabled,
        "login": row.login if row else None,
        "publishing_authorized": credential is not None,
    }


@router.post("/publishing/authorize")
def authorize(
    user=Depends(current_user),
    session=Depends(current_session),
    config=Depends(settings),
    db=Depends(db_session),
):
    if not config.credential_key:
        raise HTTPException(503, "Publishing encryption key is not configured")
    Fernet(config.credential_key.encode())
    if db.scalar(select(GithubIdentity).where(GithubIdentity.user_id == user.id)) is None:
        raise HTTPException(409, "Bind your GitHub identity first")
    return start(config, db, user, session, publishing=True)


@router.delete("/publishing", status_code=204)
def disconnect(user=Depends(current_user), db=Depends(db_session)):
    db.execute(delete(GithubCredential).where(GithubCredential.user_id == user.id))
    db.commit()


@router.get("/callback")
def callback(
    request: Request,
    state: str = "",
    code: str = "",
    config=Depends(settings),
    db=Depends(db_session),
):
    cookie = request.cookies.get(FLOW_COOKIE, "")
    if not state or not secrets.compare_digest(state, cookie):
        raise HTTPException(400, "Invalid OAuth state")
    flow = db.get(OAuthFlow, digest(state))
    if flow is None or utc(flow.expires_at) <= now():
        raise HTTPException(400, "Expired OAuth state")
    # Consume atomically BEFORE exchanging the code; concurrent callbacks cannot reuse the flow.
    consumed = db.execute(delete(OAuthFlow).where(OAuthFlow.state_hash == flow.state_hash))
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "OAuth flow already consumed")
    user_id, session_hash, publishing, verifier = (
        flow.user_id,
        flow.session_hash,
        flow.publishing,
        flow.verifier,
    )
    db.commit()
    target = "/account" if user_id else "/login"
    response = RedirectResponse(config.public_url.rstrip("/") + target, status_code=303)
    response.delete_cookie(FLOW_COOKIE, path=config.github_flow_path)
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    try:
        if not code:
            raise ValueError("GitHub authorization was cancelled")
        if user_id:
            original = db.get(LoginSession, session_hash)
            if (
                not original
                or utc(original.expires_at) <= now()
                or original.user_id != user_id
                or not secrets.compare_digest(digest(request.cookies.get(COOKIE, "")), session_hash)
            ):
                raise ValueError("Sign in again before linking GitHub")
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            token_response = client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": config.github_client_id,
                    "client_secret": config.github_client_secret,
                    "redirect_uri": config.github_callback,
                    "code": code,
                    "code_verifier": verifier,
                },
            )
            token_response.raise_for_status()
            authorization = token_response.json()
            token = authorization.get("access_token")
            if not token:
                raise ValueError("GitHub authorization failed")
            profile_response = client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
        github_id = str(profile["id"])
        if not github_id.isdigit() or not isinstance(profile["login"], str):
            raise ValueError("Invalid GitHub profile")
        linked = db.get(GithubIdentity, github_id)
        if user_id:
            user = db.get(User, user_id)
            existing = db.scalar(select(GithubIdentity).where(GithubIdentity.user_id == user_id))
            if user is None:
                raise ValueError("Account no longer exists")
            if linked and linked.user_id != user_id:
                raise ValueError("GitHub account is linked to another user")
            if existing and existing.github_id != github_id:
                raise ValueError("Your account is linked to another GitHub account")
        elif linked:
            user = db.get(User, linked.user_id)
            if user is None:
                raise ValueError("Linked account no longer exists")
        else:
            raise ValueError("Sign in to your existing Hub account and bind GitHub first")
        if publishing:
            if not linked or linked.user_id != user_id:
                raise ValueError("Authorize the GitHub identity linked to your Hub account")
            scopes = authorization.get("scope", "").replace(",", " ").split()
            if not {"public_repo", "repo"} & set(scopes):
                raise ValueError("Public repository permission was not granted")
            credential = db.get(GithubCredential, user.id)
            if credential is None:
                credential = GithubCredential(user_id=user.id)
                db.add(credential)
            credential.github_id = github_id
            credential.scopes = " ".join(scopes)
            credential.encrypted_token = (
                Fernet(config.credential_key.encode()).encrypt(token.encode()).decode()
            )
        if linked:
            linked.login = profile["login"]
        else:
            db.add(GithubIdentity(github_id=github_id, user_id=user.id, login=profile["login"]))
        if not user_id:
            issue_session(db, user, response, config)
            response.headers["location"] = config.public_url.rstrip("/") + "/"
        db.commit()
    except (ValueError, KeyError, httpx.HTTPError, IntegrityError) as exc:
        db.rollback()
        message = (
            str(exc) if isinstance(exc, ValueError) else "GitHub authorization could not complete"
        )
        response.headers["location"] = (
            config.public_url.rstrip("/") + target + "?" + urlencode({"github_error": message})
        )
    return response
