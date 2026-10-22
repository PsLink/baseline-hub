"""HTTP adapter to the deployed, unpatched AutoTune user API. No POST retries."""

from contextlib import contextmanager

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from baseline_hub.models import PlatformCredential


def cipher(config):
    try:
        return Fernet(config.credential_key.encode())
    except (ValueError, TypeError):
        raise HTTPException(503, "请配置有效的 HUB_CREDENTIAL_KEY 后重试") from None


@contextmanager
def client(request, token=None):
    config = request.app.state.settings
    if not config.upstream_url:
        raise HTTPException(503, "尚未配置 AutoTune 后端地址")
    transport = getattr(request.app.state, "platform_transport", None)
    with httpx.Client(
        base_url=config.upstream_url.rstrip("/") + "/api/",
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=httpx.Timeout(30, connect=5),
        follow_redirects=False,
        trust_env=False,
        proxy=config.upstream_proxy if transport is None and config.upstream_proxy else None,
        transport=transport,
    ) as http:
        yield http


def request_response(http, method, path, *, body=None, params=None):
    try:
        response = http.request(
            method, path, params=params, **({"json": body} if body is not None else {})
        )
    except httpx.TimeoutException:
        raise HTTPException(504, "AutoTune 响应超时") from None
    except httpx.HTTPError:
        raise HTTPException(502, "无法连接 AutoTune，请检查后端和代理") from None
    if not 200 <= response.status_code < 300:
        try:
            value = response.json()
        except ValueError:
            value = {}
        detail = (
            value.get("detail", "AutoTune 请求失败")
            if isinstance(value, dict)
            else "AutoTune 请求失败"
        )
        status = response.status_code if 400 <= response.status_code < 500 else 502
        raise HTTPException(status, detail)
    return response


def call(http, method, path, *, body=None):
    response = request_response(http, method, path, body=body)
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, "AutoTune 返回了无效响应") from None


def request_operation(http, path, *, body=None, params=None):
    """Send work-creating POSTs once; a missing response is not proof of failure."""
    try:
        return request_response(http, "POST", path, body=body, params=params)
    except HTTPException as exc:
        if exc.status_code in {502, 504}:
            raise HTTPException(exc.status_code, {
                "code": "operation_uncertain",
                "message": "操作响应未确认，请先刷新记录核查，避免重复创建任务。",
            }) from None
        raise


def reconnect():
    raise HTTPException(
        409, {"code": "upstream_auth_required", "message": "请重新连接 AutoTune 账号"}
    )


@contextmanager
def authorized(request, session, db):
    config = request.app.state.settings
    credential = db.get(PlatformCredential, session.token_hash)
    if credential is None or credential.connection_url != config.upstream_url.rstrip("/"):
        reconnect()
    try:
        token = cipher(config).decrypt(credential.encrypted_token.encode()).decode()
    except InvalidToken:
        db.delete(credential)
        db.commit()
        reconnect()
    try:
        with client(request, token) as http:
            yield http
    except HTTPException as exc:
        if exc.status_code == 401:
            db.delete(credential)
            db.commit()
            reconnect()
        raise
