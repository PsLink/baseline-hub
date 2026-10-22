"""Both user route adapters preserve the shared operation contracts."""

import httpx
import pytest
from test_platform import platform as platform_env
from test_platform import sign_in

platform = platform_env


@pytest.mark.parametrize("path", [
    "/api/platform/tracks/qwen-a100/submissions",
    "/api/platform/api/baseline-hub/tracks/qwen-a100/submissions",
])
def test_both_submission_routes_preserve_validation_and_uncertain_results(platform, path):
    client, _, _, state, track = platform
    _, headers = sign_in(client)
    body = {"name": "entry", "image": "engine:v1", "engine_args": {"tp": 2}}
    assert client.post(path, json=body, headers=headers).status_code == 201
    track["status"] = "closed"
    assert client.post(path, json=body, headers=headers).status_code == 409
    track["status"] = "open"
    state["timeout"] = True
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 504
    assert response.json()["code"] == "submission_uncertain"
    assert isinstance(response.json()["detail"], str)
    assert sum(
        request.method == "POST" and request.url.path.endswith("/submissions")
        for request in state["requests"]
    ) == 2


@pytest.mark.parametrize("path", [
    "baseline-hub/submissions/73/resubmit", "autoreport/jobs", "autoreport/jobs/8/retry",
])
def test_creating_work_keeps_uncertain_result_and_csrf_contract(platform, path):
    client, _, _, _, _ = platform
    _, headers = sign_in(client)
    original = client.app.state.platform_transport
    posts = []

    def upstream(request):
        if request.method == "POST" and request.url.path == f"/api/{path}":
            posts.append(request)
            raise httpx.ReadTimeout("response lost", request=request)
        return original.handle_request(request)

    client.app.state.platform_transport = httpx.MockTransport(upstream)
    url = "/api/platform/api/" + path
    body = {"name": "entry", "image": "engine:v1"}
    assert client.post(url, json=body).status_code == 403
    assert posts == []
    response = client.post(url, json=body, headers=headers)
    assert response.status_code == 504
    assert response.json()["code"] == "operation_uncertain"
    assert isinstance(response.json()["detail"], str)
    assert len(posts) == 1
