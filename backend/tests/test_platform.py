"""User journeys against the unpatched AutoTune contract, with real Hub sessions."""

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from baseline_hub.config import Settings
from baseline_hub.db import Base, database
from baseline_hub.main import create_app


@pytest.fixture
def platform(tmp_path):
    config = Settings(
        database_url=f"sqlite:///{tmp_path / 'platform.db'}",
        upstream_url="http://autotune",
        public_url="http://testserver",
        cookie_secure=False,
        credential_key=Fernet.generate_key().decode(),
        github_client_id="client",
        github_client_secret="secret",
    )
    state = {"requests": [], "expired": False, "timeout": False, "username": "alice", "owner": 1}
    track = {
        "id": 11,
        "slug": "qwen-a100",
        "name": "Qwen A100",
        "status": "open",
        "images": [{"image": "engine:v1", "engine": "sglang"}],
        "entries": [],
        "board": [],
    }

    def upstream(request):
        state["requests"].append(request)
        path = request.url.path
        if path == "/api/auth/options":
            if state.get("options_timeout"):
                raise httpx.ReadTimeout("unavailable", request=request)
            return httpx.Response(200, json={"signup": state.get("signup", False)})
        if path == "/api/auth/register":
            if state.get("register_timeout"):
                raise httpx.ReadTimeout("response lost", request=request)
            if not state.get("signup", False):
                return httpx.Response(403, json={"detail": "Registration disabled"})
            if state.get("duplicate"):
                return httpx.Response(409, json={"detail": "username taken"})
            body = json.loads(request.content)
            state["username"] = body["username"]
            return httpx.Response(200, json={"access_token": "token-" + body["username"]})
        if path == "/api/auth/login":
            body = json.loads(request.content)
            if body["password"] != "secret123":
                return httpx.Response(401, json={"detail": "bad credentials"})
            return httpx.Response(200, json={"access_token": "token-" + body["username"]})
        token = request.headers.get("Authorization")
        if state["expired"]:
            return httpx.Response(401, json={"detail": "expired"})
        if path == "/api/auth/me":
            bob = token == "Bearer token-bob"
            return httpx.Response(
                200,
                json={
                    "id": 2 if bob else 1,
                    "username": "bob" if bob else state["username"],
                    "role": "admin",
                    "contributor": "Alice",
                },
            )
        if path == "/api/baseline-hub/tracks":
            return httpx.Response(200, json=[track])
        if path == "/api/baseline-hub/tracks/qwen-a100":
            return httpx.Response(200, json=track)
        if request.method == "POST" and path.endswith("/submissions"):
            if state["timeout"]:
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(201, json={"id": 73, "status": "queued", "track_id": 11})
        if path == "/api/baseline-hub/submissions/73":
            return httpx.Response(
                200,
                json={
                    "id": 73,
                    "status": "measured",
                    "measurements": [],
                    "campaign_id": 12,
                    "track_slug": "qwen-a100",
                    "created_by_name": "alice",
                },
            )
        if path == "/api/campaigns/12":
            return httpx.Response(200, json={"id": 12, "owner_id": state["owner"]})
        if path == "/api/runs/5":
            return httpx.Response(200, json={"id": 5, "campaign_id": 12})
        if path == "/api/runs/5/log":
            return httpx.Response(200, text="engine started\nmeasured\n")
        if "/assets/" in path:
            return httpx.Response(200, content=state["asset"], headers={
                "content-type": state["asset_type"],
            })
        if path == "/api/catalog-export/archive":
            return httpx.Response(
                200,
                content=b"PK\x05\x06" + b"\x00" * 18,
                headers={
                    "content-type": "application/zip",
                    "content-disposition": 'attachment; filename="catalog.zip"',
                },
            )
        if path in {"/api/baseline-hub/submissions/73/abort", "/api/runs/5/stop"}:
            return httpx.Response(200, json={"id": 73, "status": "aborted"})
        if path == "/api/baseline-hub/submissions/73/resubmit":
            return httpx.Response(201, json={"id": 74, "status": "queued"})
        if path == "/api/baseline-hub/parse":
            return httpx.Response(200, json={"engine": "sglang", "engine_args": {"tp": 1}})
        if path == "/api/autoreport/jobs":
            return httpx.Response(
                201 if request.method == "POST" else 200,
                json={"id": 4, "status": "queued"} if request.method == "POST" else [],
            )
        return httpx.Response(404, json={"detail": "missing"})

    engine, factory = database(config.database_url)
    Base.metadata.create_all(engine)
    app = create_app(config, engine=engine, factory=factory)
    app.state.platform_transport = httpx.MockTransport(upstream)
    with TestClient(app) as client:
        yield client, factory, config, state, track
    engine.dispose()


def sign_in(client, username="alice"):
    response = client.post("/api/auth/login", json={"username": username, "password": "secret123"})
    assert response.status_code == 200, response.text
    return response.json(), {"X-CSRF-Token": response.json()["csrf"]}


def test_platform_login_uses_backend_identity_and_private_session(platform):
    client, factory, config, state, _ = platform
    result, headers = sign_in(client)
    assert result["user"]["username"] == "alice"
    assert result["user"]["role"] == "user"  # Never inherit backend administrator status.
    assert "token-alice" not in json.dumps(result)
    assert client.get("/api/auth/me").json()["upstream"]["connected"] is True
    from baseline_hub.models import PlatformCredential

    with factory() as db:
        row = db.query(PlatformCredential).one()
        assert "token-alice" not in row.encrypted_token
        assert (
            Fernet(config.credential_key.encode()).decrypt(row.encrypted_token.encode())
            == b"token-alice"
        )
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/platform/tracks").status_code == 401
    with factory() as db:
        assert db.query(PlatformCredential).count() == 0
    assert len(state["requests"]) == 2


def test_registration_follows_upstream_policy_and_legacy_execution_is_not_exposed(platform):
    client, *_ = platform
    assert client.get("/api/auth/options").json()["signup"] is False
    assert client.post("/api/auth/register", json={}).status_code == 422
    assert client.get("/api/tracks").status_code == 404
    assert client.get("/api/resources").status_code == 404


def test_track_queries_and_submit_match_deployed_contract(platform):
    client, _, _, state, _ = platform
    _, headers = sign_in(client)
    assert client.get("/api/platform/tracks").json()[0]["slug"] == "qwen-a100"
    assert client.get("/api/platform/tracks/qwen-a100").json()["images"]
    body = {"name": "My baseline", "image": "engine:v1", "engine_args": {"tp": 1}}
    url = "/api/platform/tracks/qwen-a100/submissions"
    assert client.post(url, json=body).status_code == 403
    response = client.post(url, json=body, headers=headers)
    assert response.status_code == 201, response.text
    sent = state["requests"][-1]
    assert sent.url.path == "/api/baseline-hub/tracks/qwen-a100/submissions"
    assert sent.headers["Authorization"] == "Bearer token-alice"
    assert json.loads(sent.content) == body
    assert client.get("/api/platform/submissions/73").json()["status"] == "measured"
    # No arbitrary execution-resource fields or unverified images.
    assert client.post(url, json=body | {"gpu_count": 8}, headers=headers).status_code == 422
    assert (
        client.post(url, json=body | {"image": "unverified:v1"}, headers=headers).status_code == 422
    )


def test_closed_track_refuses_submission(platform):
    client, _, _, state, track = platform
    _, headers = sign_in(client)
    track["status"] = "retired"
    result = client.post(
        "/api/platform/tracks/qwen-a100/submissions",
        headers=headers,
        json={"name": "x", "image": "engine:v1", "command": "sglang serve /model --tp 1"},
    )
    assert result.status_code == 409
    assert all(r.method == "GET" for r in state["requests"][2:])


def test_expired_upstream_token_preserves_hub_and_reconnects(platform):
    client, _, _, state, _ = platform
    _, headers = sign_in(client)
    state["expired"] = True
    response = client.get("/api/platform/tracks")
    assert response.status_code == 409
    assert response.json()["code"] == "upstream_auth_required"
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/auth/me").json()["upstream"]["connected"] is False
    state["expired"] = False
    url = "/api/auth/upstream/connect"
    assert (
        client.post(
            url, headers=headers, json={"username": "bob", "password": "secret123"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            url, headers=headers, json={"username": "alice", "password": "secret123"}
        ).status_code
        == 200
    )
    assert client.get("/api/platform/tracks").status_code == 200


def test_response_loss_does_not_retry_submission(platform):
    client, _, _, state, _ = platform
    _, headers = sign_in(client)
    state["timeout"] = True
    response = client.post(
        "/api/platform/tracks/qwen-a100/submissions",
        headers=headers,
        json={"name": "x", "image": "engine:v1", "engine_args": {"tp": 1}},
    )
    assert response.status_code == 504
    assert response.json()["code"] == "submission_uncertain"
    posts = [
        r for r in state["requests"] if r.method == "POST" and r.url.path.endswith("/submissions")
    ]
    assert len(posts) == 1


def test_identities_are_stable_and_credentials_are_per_session(platform):
    client, _, config, state, _ = platform
    a, _ = sign_in(client)
    state["username"] = "alice-renamed"
    same, _ = sign_in(client)
    assert same["user"]["id"] == a["user"]["id"]
    assert same["user"]["username"] == "alice-renamed"
    b, _ = sign_in(client, "bob")
    assert b["user"]["id"] != a["user"]["id"]
    client.get("/api/platform/tracks")
    assert state["requests"][-1].headers["Authorization"] == "Bearer token-bob"
    config.upstream_url = "http://another-autotune"
    assert client.get("/api/platform/tracks").status_code == 409


def test_missing_key_and_bad_password_are_explicit(platform):
    client, _, config, _, _ = platform
    assert (
        client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code
        == 401
    )
    config.credential_key = ""
    assert (
        client.post(
            "/api/auth/login", json={"username": "alice", "password": "secret123"}
        ).status_code
        == 503
    )


def test_reconnect_bad_password_does_not_revoke_hub_session(platform):
    client, *_ = platform
    _, headers = sign_in(client)
    response = client.post(
        "/api/auth/upstream/connect",
        headers=headers,
        json={"username": "alice", "password": "wrong"},
    )
    assert response.status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_github_binding_then_github_login_requires_backend_reconnect(platform, monkeypatch):
    client, _, _, _, _ = platform
    _, headers = sign_in(client)
    response = client.post("/api/auth/github/bind", headers=headers)
    state = parse_qs(urlsplit(response.json()["url"]).query)["state"][0]
    real_client = httpx.Client

    def github(request):
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "github-token"})
        return httpx.Response(200, json={"id": 1234, "login": "alice-gh"})

    monkeypatch.setattr(
        "baseline_hub.github.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(github)),
    )
    callback = f"/api/auth/github/callback?state={state}&code=code"
    assert client.get(callback, follow_redirects=False).status_code == 303
    assert client.get(callback, follow_redirects=False).status_code == 400
    assert client.get("/api/auth/github/identity").json()["login"] == "alice-gh"
    client.post("/api/auth/logout", headers=headers)
    login = client.get("/api/auth/github/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    client.get(f"/api/auth/github/callback?state={state}&code=code", follow_redirects=False)
    assert client.get("/api/auth/me").json()["upstream"]["connected"] is False
    assert client.get("/api/platform/tracks").status_code == 409


def test_github_cancel_and_invalid_state_do_not_bind(platform):
    client, *_ = platform
    _, headers = sign_in(client)
    assert client.post("/api/auth/github/bind").status_code == 403
    response = client.post("/api/auth/github/bind", headers=headers)
    state = parse_qs(urlsplit(response.json()["url"]).query)["state"][0]
    assert client.get("/api/auth/github/callback?state=wrong&code=code").status_code == 400
    response = client.get(f"/api/auth/github/callback?state={state}", follow_redirects=False)
    assert "github_error" in response.headers["location"]
    assert client.get("/api/auth/github/identity").json()["login"] is None


def mock_github(monkeypatch, github_id=1234):
    real_client = httpx.Client

    def response(request):
        if request.url.path.endswith("/access_token"):
            return httpx.Response(200, json={"access_token": "github-only-token"})
        return httpx.Response(200, json={"id": github_id, "login": "alice-gh"})

    monkeypatch.setattr(
        "baseline_hub.github.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(response)),
    )


def test_github_conflict_never_rebinds_another_backend_user(platform, monkeypatch):
    from baseline_hub.models import GithubIdentity

    client, factory, *_ = platform
    alice, _ = sign_in(client)
    with factory() as db:
        db.add(GithubIdentity(github_id="1234", user_id=alice["user"]["id"], login="alice-gh"))
        db.commit()
    bob, headers = sign_in(client, "bob")
    start = client.post("/api/auth/github/bind", headers=headers)
    state = parse_qs(urlsplit(start.json()["url"]).query)["state"][0]
    mock_github(monkeypatch)
    response = client.get(
        f"/api/auth/github/callback?state={state}&code=code", follow_redirects=False
    )
    assert "github_error" in response.headers["location"]
    assert client.get("/api/auth/me").json()["user"]["id"] == bob["user"]["id"]
    assert client.get("/api/auth/github/identity").json()["login"] is None
    with factory() as db:
        assert db.get(GithubIdentity, "1234").user_id == alice["user"]["id"]


def test_unbound_github_never_creates_platform_accounts(platform, monkeypatch):
    from baseline_hub.models import User

    client, factory, config, *_ = platform
    start = client.get("/api/auth/github/login", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    mock_github(monkeypatch)
    response = client.get(
        f"/api/auth/github/callback?state={state}&code=code", follow_redirects=False
    )
    assert "github_error" in response.headers["location"]
    assert client.get("/api/auth/me").status_code == 401
    with factory() as db:
        assert db.query(User).count() == 0


def test_original_ui_reads_and_parse_use_explicit_compat_routes(platform):
    client, _, _, state, _ = platform
    _, headers = sign_in(client)
    root = "/api/platform/api"
    assert client.get(root + "/baseline-hub/tracks?status=open").status_code == 200
    assert state["requests"][-1].url.params["status"] == "open"
    result = client.post(
        root + "/baseline-hub/parse",
        headers=headers,
        json={"command": "sglang serve /model --tp 1", "track": "qwen-a100"},
    )
    assert result.status_code == 200
    assert result.json()["engine_args"] == {"tp": 1}
    assert client.get(root + "/auth/me").json()["id"] == 1
    before = len(state["requests"])
    for method, path in [
        ("get", "/machines"),
        ("get", "/autoreport/settings"),
        ("post", "/baseline-hub/tracks"),
        ("delete", "/auth/users/1"),
    ]:
        assert getattr(client, method)(root + path, headers=headers).status_code == 404
    assert len(state["requests"]) == before


def test_original_submit_payload_preserves_advanced_options(platform):
    client, _, _, state, _ = platform
    _, headers = sign_in(client)
    body = {
        "name": "original dialog",
        "image": "engine:v1",
        "engine_args": {"tp": 1},
        "extra_env": {"NCCL_DEBUG": "INFO"},
        "extra_volumes": {"/cache": "/cache"},
        "is_baseline": True,
        "contributor": "Alice",
        "remember_contributor": True,
        "contest_api_token": "private-token",
        "remember_contest_token": False,
    }
    response = client.post(
        "/api/platform/api/baseline-hub/tracks/qwen-a100/submissions", headers=headers, json=body
    )
    assert response.status_code == 201, response.text
    assert json.loads(state["requests"][-1].content) == body


def test_track_defaults_can_be_submitted_without_extra_engine_flags(platform):
    client, *_ = platform
    _, headers = sign_in(client)
    response = client.post(
        "/api/platform/api/baseline-hub/tracks/qwen-a100/submissions",
        headers=headers, json={"name": "defaults", "image": "engine:v1", "engine_args": {}},
    )
    assert response.status_code == 201


def test_cancel_resubmit_and_stop_verify_backend_ownership(platform):
    client, _, _, state, _ = platform
    _, headers = sign_in(client)
    root = "/api/platform/api"
    assert client.post(root + "/baseline-hub/submissions/73/abort").status_code == 403
    assert (
        client.post(root + "/baseline-hub/submissions/73/abort", headers=headers).status_code == 200
    )
    body = {"name": "evolved", "image": "engine:v1", "engine_args": {"tp": 1}}
    assert (
        client.post(
            root + "/baseline-hub/submissions/73/resubmit", headers=headers, json=body
        ).status_code
        == 201
    )
    assert client.post(root + "/runs/5/stop", headers=headers).status_code == 200
    state["owner"] = 2
    before = len([r for r in state["requests"] if r.method == "POST"])
    assert (
        client.post(root + "/baseline-hub/submissions/73/abort", headers=headers).status_code == 403
    )
    assert (
        client.post(
            root + "/baseline-hub/submissions/73/resubmit", headers=headers, json=body
        ).status_code
        == 403
    )
    assert client.post(root + "/runs/5/stop", headers=headers).status_code == 403
    assert len([r for r in state["requests"] if r.method == "POST"]) == before


def test_logs_and_catalog_zip_are_forwarded_without_json_corruption(platform):
    client, *_ = platform
    _, headers = sign_in(client)
    root = "/api/platform/api"
    response = client.get(root + "/runs/5/log")
    assert response.status_code == 200
    assert response.text == "engine started\nmeasured\n"
    response = client.post(root + "/catalog-export/archive", headers=headers,
                           json={"variants": [{"run_id": 5, "role": "optimized"}]})
    assert response.status_code == 200
    assert response.content == b"PK\x05\x06" + b"\x00" * 18
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]


def test_original_autoreport_actions_are_supported_without_settings_access(platform):
    client, *_ = platform
    _, headers = sign_in(client)
    response = client.post(
        "/api/platform/api/autoreport/jobs",
        headers=headers,
        json={"track": "qwen-a100", "langs": ["zh"]},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "queued"


@pytest.mark.parametrize("mime,payload", [
    ("text/html", b"<script>alert(1)</script>"),
    ("image/svg+xml", b'<svg onload="alert(1)"/>'),
    ("image/png", b"<script>alert(1)</script>"),
])
def test_report_assets_reject_active_or_mislabeled_content(platform, mime, payload):
    client, _, _, state, _ = platform
    sign_in(client)
    state.update(asset=payload, asset_type=mime)
    response = client.get("/api/platform/api/agent/v1/reports/1/assets/chart.png")
    assert response.status_code == 415
    assert payload not in response.content


def test_report_raster_assets_keep_bytes_and_safe_headers(platform):
    client, _, _, state, _ = platform
    sign_in(client)
    payload = b"\x89PNG\r\n\x1a\nimage-data"
    state.update(asset=payload, asset_type="image/png")
    response = client.get("/api/platform/api/agent/v1/reports/1/assets/chart.png")
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def test_registration_creates_private_session_for_upstream_account(platform):
    from baseline_hub.models import PlatformCredential, PlatformIdentity

    client, factory, _, state, _ = platform
    state["signup"] = True
    assert client.get("/api/auth/options").json()["signup"] is True
    response = client.post(
        "/api/auth/register", json={"username": "new-user", "password": "secret123"}
    )
    assert response.status_code == 201
    result = response.json()
    assert result["user"]["username"] == "new-user"
    assert result["user"]["role"] == "user"
    assert result["upstream"]["connected"] is True
    assert "token-new-user" not in response.text
    assert client.get("/api/auth/me").json()["user"]["id"] == result["user"]["id"]
    assert client.get("/api/platform/tracks").status_code == 200
    assert state["requests"][-1].headers["Authorization"] == "Bearer token-new-user"
    with factory() as db:
        assert db.query(PlatformIdentity).count() == 1
        assert "token-new-user" not in db.query(PlatformCredential).one().encrypted_token


@pytest.mark.parametrize("scenario,status", [("closed", 403), ("duplicate", 409),
                                             ("register_timeout", 504)])
def test_registration_failures_never_create_local_account_or_retry(platform, scenario, status):
    from baseline_hub.models import User

    client, factory, _, state, _ = platform
    state.update(signup=scenario != "closed")
    state[scenario] = True
    response = client.post(
        "/api/auth/register", json={"username": "new-user", "password": "secret123"}
    )
    assert response.status_code == status
    assert len([r for r in state["requests"] if r.url.path == "/api/auth/register"]) == 1
    assert client.get("/api/auth/me").status_code == 401
    with factory() as db:
        assert db.query(User).count() == 0


def test_registration_validates_input_origin_and_encryption_before_remote_write(platform):
    client, _, config, state, _ = platform
    body = {"username": "new-user", "password": "secret123"}
    assert client.post("/api/auth/register", json=body | {"password": "short"}).status_code == 422
    assert client.post(
        "/api/auth/register", json=body, headers={"Origin": "http://other-site"}
    ).status_code == 403
    config.credential_key = ""
    assert client.post("/api/auth/register", json=body).status_code == 503
    assert state["requests"] == []


def test_registration_options_outage_preserves_github_login(platform):
    client, _, _, state, _ = platform
    state["options_timeout"] = True
    response = client.get("/api/auth/options")
    assert response.status_code == 200
    assert response.json()["github"] is True
    assert response.json()["signup"] is False
    assert response.json()["signup_error"]


def test_sessions_enforce_csrf_origin_expiry_and_logout(platform):
    from datetime import timedelta

    from sqlalchemy import select

    from baseline_hub.db import now
    from baseline_hub.models import LoginSession, PlatformCredential

    client, factory, *_ = platform
    _, headers = sign_in(client)
    token = client.cookies.get("hub_session")
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post(
        "/api/auth/logout", headers=headers | {"Origin": "https://attacker.example"}
    ).status_code == 403
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    # Reusing the old cookie cannot restore a revoked session or its upstream credentials.
    client.cookies.set("hub_session", token)
    assert client.get("/api/auth/me").status_code == 401
    with factory() as db:
        assert db.scalar(select(LoginSession)) is None
        assert db.scalar(select(PlatformCredential)) is None
    sign_in(client)
    with factory() as db:
        db.scalar(select(LoginSession)).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_github_binding_requires_original_live_session(platform):
    from sqlalchemy import select

    from baseline_hub.models import OAuthFlow

    client, factory, *_ = platform
    _, headers = sign_in(client)
    start = client.post("/api/auth/github/bind", headers=headers)
    state = parse_qs(urlsplit(start.json()["url"]).query)["state"][0]
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    response = client.get(
        "/api/auth/github/callback", params={"state": state, "code": "x"},
        follow_redirects=False,
    )
    assert "Sign+in+again" in response.headers["location"]
    with factory() as db:
        assert db.scalar(select(OAuthFlow)) is None
    assert client.get(
        "/api/auth/github/callback", params={"state": state, "code": "x"},
        follow_redirects=False,
    ).status_code == 400


def test_catalog_preview_and_zip_omit_validation_sidecars(platform):
    from io import BytesIO
    from zipfile import ZipFile

    client, *_ = platform
    _, headers = sign_in(client)
    files = {
        "models/demo/demo.validation.json": '{"old": true}',
        "models/demo/base.yaml": "id: base\n",
        "models/demo/report.html": "<html>report</html>",
        "models/demo/config.json": '{"keep": true}',
    }
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        for path, content in files.items():
            output.writestr(path, content)

    original = client.app.state.platform_transport

    def upstream(request):
        if request.method == "GET":
            return original.handle_request(request)
        if request.url.path.endswith("/preview"):
            return httpx.Response(200, json={"valid": True, "files": [
                {"path": path, "kind": "variant", "text": content}
                for path, content in files.items()
            ]})
        return httpx.Response(200, content=archive.getvalue(), headers={
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="demo.zip"',
        })

    client.app.state.platform_transport = httpx.MockTransport(upstream)
    root = "/api/platform/api/catalog-export"
    source = {"variants": [{"run_id": 5, "role": "optimized"}]}
    preview = client.post(root + "/preview", json=source, headers=headers)
    assert preview.status_code == 200
    expected = {path: content for path, content in files.items()
                if not path.endswith(".validation.json")}
    assert {file["path"]: file["text"] for file in preview.json()["files"]} == expected
    response = client.post(root + "/archive", json=source, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="demo.zip"'
    with ZipFile(BytesIO(response.content)) as output:
        assert {path: output.read(path).decode() for path in output.namelist()} == expected


def test_subpath_sessions_and_oauth_follow_public_url(platform):
    client, _, config, _, _ = platform
    config.public_url = "http://testserver/hub"
    app = client.app

    async def proxy(scope, receive, send):
        # Emulate Apache/Nginx stripping the external prefix before forwarding.
        if scope["type"] == "http":
            scope = dict(scope)
            scope["path"] = scope["path"].removeprefix("/hub")
            scope["raw_path"] = scope["path"].encode()
        await app(scope, receive, send)

    with TestClient(proxy) as browser:
        response = browser.post(
            "/hub/api/auth/login", json={"username": "alice", "password": "secret123"}
        )
        assert response.status_code == 200
        assert "Path=/hub/api;" in response.headers["set-cookie"]
        assert browser.get("/hub/api/auth/me").status_code == 200
        headers = {"X-CSRF-Token": response.json()["csrf"], "Origin": "http://testserver"}
        assert browser.post(
            "/hub/api/auth/github/bind", headers={**headers, "Origin": "http://evil.example"}
        ).status_code == 403
        response = browser.post("/hub/api/auth/github/bind", headers=headers)
        query = parse_qs(urlsplit(response.json()["url"]).query)
        assert query["redirect_uri"] == ["http://testserver/hub/api/auth/github/callback"]
        assert "Path=/hub/api/auth/github;" in response.headers["set-cookie"]
        response = browser.get(
            "/hub/api/auth/github/callback", params={"state": query["state"][0]},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith("http://testserver/hub/account?")
        assert "Path=/hub/api/auth/github;" in response.headers["set-cookie"]
        response = browser.post("/hub/api/auth/logout", headers=headers)
        assert response.status_code == 204
        assert "Path=/hub/api;" in response.headers["set-cookie"]
        assert browser.get("/hub/api/auth/me").status_code == 401
