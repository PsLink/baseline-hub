"""Hub browser sessions linked to stable AutoTune user IDs."""

import hashlib
import secrets

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from baseline_hub.models import PlatformCredential, PlatformIdentity, User
from baseline_hub.sessions import (
    COOKIE,
    current_session,
    db_session,
    issue_session,
    settings,
    user_out,
)
from baseline_hub.upstream import call, cipher, client

router = APIRouter(prefix="/auth", tags=["platform auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class Registration(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)


def identity(db, user_id, config):
    return db.scalar(
        select(PlatformIdentity).where(
            PlatformIdentity.user_id == user_id,
            PlatformIdentity.connection_url == config.upstream_url.rstrip("/"),
        )
    )


def account(db, user, config, session_hash):
    linked = identity(db, user.id, config)
    credential = db.get(PlatformCredential, session_hash)
    return {
        "user": user_out(user) | {"username": linked.username if linked else user.username},
        "upstream": {
            "id": linked.upstream_user_id if linked else None,
            "username": linked.username if linked else None,
            "connected": bool(
                linked and credential and credential.connection_url == linked.connection_url
            ),
        },
    }


def authenticate(request, body, *, action="login"):
    # Fail before sending credentials if encrypted storage is not configured.
    cipher(request.app.state.settings)
    with client(request) as http:
        result = call(http, "POST", f"auth/{action}", body=body.model_dump())
        if not isinstance(result, dict) or not isinstance(result.get("access_token"), str):
            raise HTTPException(502, "AutoTune 登录响应缺少凭证")
        token = result["access_token"]
        http.headers["Authorization"] = f"Bearer {token}"
        profile = call(http, "GET", "auth/me")
    if (
        not isinstance(profile, dict)
        or type(profile.get("id")) is not int
        or not isinstance(profile.get("username"), str)
    ):
        raise HTTPException(502, "AutoTune 返回了无效账号信息")
    return token, profile


def provision(db, config, profile):
    url = config.upstream_url.rstrip("/")
    query = select(PlatformIdentity).where(
        PlatformIdentity.connection_url == url, PlatformIdentity.upstream_user_id == profile["id"]
    )
    linked = db.scalar(query)
    if linked is None:
        internal = hashlib.sha256(f"{url}:{profile['id']}".encode()).hexdigest()
        user = User(
            username="platform_" + internal,
            role="user",
            password_hash=bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt()).decode(),
            contributor=profile.get("contributor", ""),
        )
        try:
            db.add(user)
            db.flush()
            linked = PlatformIdentity(
                user_id=user.id,
                connection_url=url,
                upstream_user_id=profile["id"],
                username=profile["username"],
            )
            db.add(linked)
            db.flush()
        except IntegrityError:
            db.rollback()
            linked = db.scalar(query)
            if linked is None:
                raise HTTPException(409, "账号关联冲突，请重新登录") from None
    linked.username = profile["username"]
    return db.get(User, linked.user_id)


@router.get("/options")
def options(request: Request, config=Depends(settings)):
    result = {"signup": False, "github": config.github_enabled, "mode": "platform"}
    try:
        with client(request) as http:
            upstream = call(http, "GET", "auth/options")
        if not isinstance(upstream, dict) or type(upstream.get("signup")) is not bool:
            raise HTTPException(502, "AutoTune 返回了无效注册配置")
        result["signup"] = upstream["signup"]
    except HTTPException:
        # GitHub login must remain available when AutoTune cannot be reached.
        result["signup_error"] = "暂时无法获取注册配置，请稍后刷新重试"
    return result


@router.post("/login")
def login(
    body: Credentials,
    request: Request,
    response: Response,
    config=Depends(settings),
    db=Depends(db_session),
):
    return sign_in(request, response, config, db, body)


@router.post("/register", status_code=201)
def register(
    body: Registration,
    request: Request,
    response: Response,
    config=Depends(settings),
    db=Depends(db_session),
):
    return sign_in(request, response, config, db, body, action="register")


def sign_in(request, response, config, db, body, *, action="login"):
    token, profile = authenticate(request, body, action=action)
    user = provision(db, config, profile)
    result = issue_session(db, user, response, config, upstream_token=token)
    result["user"]["username"] = profile["username"]
    result["upstream"] = {"id": profile["id"], "username": profile["username"], "connected": True}
    db.commit()
    return result


@router.get("/me")
def me(session=Depends(current_session), config=Depends(settings), db=Depends(db_session)):
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "账号已不存在")
    return account(db, user, config, session.token_hash) | {"csrf": session.csrf}


@router.post("/upstream/connect")
def connect(
    body: Credentials,
    request: Request,
    session=Depends(current_session),
    config=Depends(settings),
    db=Depends(db_session),
):
    try:
        token, profile = authenticate(request, body)
    except HTTPException as exc:
        if exc.status_code == 401:
            raise HTTPException(403, "AutoTune 用户名或密码不正确，Hub 登录仍然有效") from None
        raise
    linked = identity(db, session.user_id, config)
    if linked is None or linked.upstream_user_id != profile["id"]:
        raise HTTPException(409, "请使用与此 Hub 身份关联的 AutoTune 账号登录")
    linked.username = profile["username"]
    row = db.get(PlatformCredential, session.token_hash)
    if row is None:
        row = PlatformCredential(session_hash=session.token_hash)
        db.add(row)
    row.connection_url = config.upstream_url.rstrip("/")
    row.encrypted_token = cipher(config).encrypt(token.encode()).decode()
    db.commit()
    return account(db, db.get(User, session.user_id), config, session.token_hash)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    session=Depends(current_session),
    db=Depends(db_session),
    config=Depends(settings),
):
    db.delete(session)
    db.commit()
    response.delete_cookie(COOKIE, path=config.api_cookie_path)
