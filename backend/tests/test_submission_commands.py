"""Submission rules exercised independently of browser routing and database setup."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from baseline_hub.submission_commands import (
    Entry,
    create_submission,
    stop_run,
    submission_action,
    validate_entry,
)


@pytest.fixture
def commands():
    state = {
        "status": "open", "owner": 1, "campaign": 10, "creator": "alice",
        "track": "demo", "timeout": False, "requests": [],
    }

    def upstream(request):
        state["requests"].append(request)
        if request.method == "POST":
            if state["timeout"]:
                raise httpx.ReadTimeout("lost response", request=request)
            return httpx.Response(201, json={"id": 7})
        path = request.url.path
        if "/tracks/" in path:
            return httpx.Response(200, json={
                "status": state["status"], "images": [{"image": "verified:v1"}],
            })
        if "/submissions/" in path:
            return httpx.Response(200, json={
                "campaign_id": state["campaign"], "created_by_name": state["creator"],
                "track_slug": state["track"],
            })
        if "/runs/" in path:
            return httpx.Response(200, json={"campaign_id": 10})
        return httpx.Response(200, json={"owner_id": state["owner"]})

    with httpx.Client(
        base_url="http://autotune/api/", transport=httpx.MockTransport(upstream)
    ) as http:
        yield http, state, SimpleNamespace(upstream_user_id=1, username="alice")


@pytest.mark.parametrize("operation", ["create", "resubmit"])
@pytest.mark.parametrize("invalid", ["closed", "image"])
def test_create_and_resubmit_apply_the_same_track_rules(commands, operation, invalid):
    http, state, linked = commands
    state["status"] = "closed" if invalid == "closed" else "open"
    body = {"name": "entry", "image": "wrong" if invalid == "image" else "verified:v1"}
    with pytest.raises(HTTPException) as error:
        if operation == "create":
            create_submission(http, "demo", validate_entry(body))
        else:
            submission_action(http, 7, "resubmit", body, linked)
    assert error.value.status_code == (409 if invalid == "closed" else 422)
    assert all(request.method == "GET" for request in state["requests"])


def test_new_submission_preserves_explicit_fields_and_resubmit_drops_account_changes(commands):
    http, state, linked = commands
    body = {
        "name": "entry", "image": "verified:v1", "engine_args": {"tp": 2},
        "extra_env": {"CUSTOM": "value"}, "contributor": "Alice",
        "remember_contributor": True, "contest_api_token": "private",
        "remember_contest_token": True,
    }
    assert create_submission(http, "demo", Entry.model_validate(body)) == {"id": 7}
    assert json.loads(state["requests"][-1].content) == body
    result = submission_action(
        http, 7, "resubmit", body, linked, params=[("tag", "a"), ("tag", "b")]
    )
    assert result.status_code == 201
    sent = state["requests"][-1]
    assert sent.url.params.get_list("tag") == ["a", "b"]
    assert json.loads(sent.content) == {
        key: value for key, value in body.items()
        if key not in {
            "contributor", "remember_contributor", "contest_api_token", "remember_contest_token",
        }
    }


@pytest.mark.parametrize("action", ["abort", "preflight", "resubmit", "stop"])
def test_other_users_cannot_write_submissions_or_runs(commands, action):
    http, state, linked = commands
    state["owner"] = 2
    with pytest.raises(HTTPException) as error:
        if action == "stop":
            stop_run(http, 5, linked)
        else:
            submission_action(http, 7, action, {}, linked)
    assert error.value.status_code == 403
    assert all(request.method == "GET" for request in state["requests"])


def test_queued_submission_ownership_uses_the_linked_name_and_fails_closed_after_rename(commands):
    http, state, linked = commands
    state["campaign"] = None
    assert submission_action(http, 7, "abort", None, linked).status_code == 201
    state["creator"] = "alice-before-rename"
    with pytest.raises(HTTPException) as error:
        submission_action(http, 7, "preflight", None, linked)
    assert error.value.status_code == 403


@pytest.mark.parametrize("operation,code", [
    ("create", "submission_uncertain"), ("resubmit", "operation_uncertain"),
])
def test_lost_work_response_is_explicit_and_never_retried(commands, operation, code):
    http, state, linked = commands
    state["timeout"] = True
    body = {"name": "entry", "image": "verified:v1"}
    with pytest.raises(HTTPException) as error:
        if operation == "create":
            create_submission(http, "demo", validate_entry(body))
        else:
            submission_action(http, 7, "resubmit", body, linked)
    assert error.value.status_code == 504
    assert error.value.detail["code"] == code
    assert sum(request.method == "POST" for request in state["requests"]) == 1


def test_invalid_entry_does_not_expose_credentials_or_environment_values():
    with pytest.raises(HTTPException) as error:
        validate_entry({
            "name": " ", "image": "verified:v1", "contest_api_token": "private-token",
            "extra_env": {"SECRET": "private-env"},
        })
    assert error.value.status_code == 422
    assert "private" not in str(error.value.detail)
