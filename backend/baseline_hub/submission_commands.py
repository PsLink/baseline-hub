"""Submission validation and user-owned operations shared by HTTP route adapters."""

from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from baseline_hub.upstream import call, request_operation, request_response


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    image: str = Field(min_length=1, max_length=255)
    command: str = Field(default="", max_length=32768)
    engine_args: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=10000)
    machine_count: int = Field(default=1, ge=1)
    service_port: int = Field(default=28200, ge=1024, le=65535)
    extra_env: dict[str, str] = Field(default_factory=dict)
    extra_volumes: dict[str, str] = Field(default_factory=dict)
    share_machine: bool = False
    is_baseline: bool = False
    contributor: str = Field(default="", max_length=255)
    remember_contributor: bool = False
    contest_api_token: str = Field(default="", max_length=512)
    remember_contest_token: bool = False

    @model_validator(mode="after")
    def config_present(self):
        if not self.name.strip():
            raise ValueError("请填写名称")
        return self


def validate_entry(body):
    try:
        return Entry.model_validate(body)
    except ValidationError:
        # Never echo contest tokens or environment values in validation errors.
        raise HTTPException(422, "提交字段无效，请检查名称、镜像和引擎参数") from None


def _check_track(http, slug, entry):
    path = f"baseline-hub/tracks/{quote(slug, safe='')}"
    rules = call(http, "GET", path)
    if rules.get("status") != "open":
        raise HTTPException(409, "赛道尚未开放或已关闭")
    if entry.image not in {item.get("image") for item in rules.get("images", [])}:
        raise HTTPException(422, "请选择该赛道已验证的镜像")
    return path


def create_submission(http, slug, entry):
    path = _check_track(http, slug, entry)
    try:
        return call(http, "POST", path + "/submissions", body=entry.model_dump(exclude_unset=True))
    except HTTPException as exc:
        if exc.status_code in {502, 504}:
            raise HTTPException(exc.status_code, {
                "code": "submission_uncertain",
                "message": (
                    "提交响应未确认，后端可能已接收。"
                    "请先刷新赛道提交列表核查，再决定是否重新提交。"
                ),
            }) from None
        raise


def _owned_submission(http, ident, linked):
    submission = call(http, "GET", f"baseline-hub/submissions/{ident}")
    if submission.get("campaign_id"):
        campaign = call(http, "GET", f"campaigns/{submission['campaign_id']}")
        owns = campaign.get("owner_id") == linked.upstream_user_id
    else:
        # Queued jobs have no campaign; historical names must fail closed after rename.
        owns = submission.get("created_by_name") == linked.username
    if not owns:
        raise HTTPException(403, "只能操作自己提交的记录")
    return submission


def submission_action(http, ident, action, body, linked, *, params=None):
    if action not in {"abort", "resubmit", "preflight"}:
        raise HTTPException(404, "不支持的提交操作")
    submission = _owned_submission(http, ident, linked)
    path = f"baseline-hub/submissions/{ident}/{action}"
    if action == "resubmit":
        if not submission.get("track_slug"):
            raise HTTPException(403, "普通用户只能在赛道中重投")
        entry = validate_entry(body)
        _check_track(http, submission["track_slug"], entry)
        body = entry.model_dump(exclude_unset=True, exclude={
            "contributor", "remember_contributor", "contest_api_token", "remember_contest_token",
        })
        return request_operation(http, path, body=body, params=params)
    return request_response(http, "POST", path, body=body, params=params)


def stop_run(http, ident, linked, *, body=None, params=None):
    run = call(http, "GET", f"runs/{ident}")
    campaign = call(http, "GET", f"campaigns/{run['campaign_id']}")
    if campaign.get("owner_id") != linked.upstream_user_id:
        raise HTTPException(403, "只能停止自己提交的运行")
    return request_response(http, "POST", f"runs/{ident}/stop", body=body, params=params)
