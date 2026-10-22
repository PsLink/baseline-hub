"""Platform publishing uses server exports and immutable, user-owned review snapshots."""

import json
from datetime import timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event
from test_platform import platform as platform_env
from test_platform import sign_in

from baseline_hub.db import now
from baseline_hub.models import CatalogPublication, GithubCredential, GithubIdentity

platform = platform_env

ROOT = "/api/catalog-export"
BODY = {"variants": [{"run_id": 5, "role": "baseline"}], "name": "demo", "hf": "Org/Model"}


@pytest.fixture
def publisher(platform, monkeypatch):
    client, factory, config, state, _ = platform
    login, headers = sign_in(client)
    with factory() as db:
        db.add(GithubIdentity(github_id="12", user_id=login["user"]["id"], login="alice"))
        db.add(
            GithubCredential(
                user_id=login["user"]["id"],
                github_id="12",
                scopes="public_repo",
                encrypted_token=Fernet(config.credential_key.encode())
                .encrypt(b"github-token")
                .decode(),
            )
        )
        db.commit()
    original = client.app.state.platform_transport
    state["export"] = {
        "name": "demo",
        "valid": True,
        "errors": [],
        "provenance": ["run 5"],
        "metadata": {
            "apiVersion": "catalog.swiss/v2",
            "name": "demo",
            "source": {"hf": "Org/Model"},
        },
        "files": [
            {
                "path": "models/demo/metadata.yaml",
                "kind": "metadata",
                "text": "apiVersion: catalog.swiss/v2\nname: demo\nsource: {hf: Org/Model}\n",
            },
            {
                "path": "models/demo/base.yaml",
                "kind": "variant",
                "text": (
                    "apiVersion: catalog.swiss/v2\nkind: Variant\n"
                    "model: demo\nid: base\nversion: 1.0.0\n"
                ),
            },
        ],
    }

    def remote(request):
        if request.url.path == "/api/catalog-export/preview":
            state["export_body"] = json.loads(request.content)
            return httpx.Response(200, json=state["export"])
        return original.handle_request(request)

    client.app.state.platform_transport = httpx.MockTransport(remote)

    class Github:
        def __init__(self, token, **kwargs):
            assert token == "github-token"

        def snapshot(self):
            return {
                "repository_id": 100,
                "base": "main",
                "sha": "base",
                "tree": "old-tree",
                "paths": {},
            }

        def model(self, snapshot, name, hf):
            return None

        def close(self):
            pass

    monkeypatch.setattr("baseline_hub.publishing.Github", Github, raising=False)
    return client, factory, config, state, headers


def test_preview_submit_and_poll_are_immutable_and_idempotent(publisher):
    client, factory, config, state, headers = publisher
    response = client.post(ROOT + "/publication-previews", json=BODY, headers=headers)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "preview" and job["files"][0]["diff"]
    assert job["repository"] == config.catalog_repository
    assert state["export_body"]["use_catalog"] is False
    assert "github-token" not in response.text
    again = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    assert again["id"] == job["id"]
    submit = ROOT + f"/publications/{job['id']}/submit"
    assert client.post(submit).status_code == 403
    for _ in range(2):
        queued = client.post(submit, headers=headers)
        assert queued.status_code == 202, queued.text
        assert queued.json()["files"] == job["files"]
        assert queued.json()["status"] == "queued"
    with factory() as db:
        row = db.get(CatalogPublication, job["id"])
        row.status, row.pr_url, row.pr_number = (
            "succeeded",
            "https://github.com/PsLink/model-catalog/pull/42",
            42,
        )
        db.commit()
    assert client.get(ROOT + f"/publications/{job['id']}").json()["pr_number"] == 42
    assert client.get(ROOT + "/publications").json()[0]["id"] == job["id"]
    sign_in(client, "bob")
    assert client.get(ROOT + f"/publications/{job['id']}").status_code == 404


def test_preview_requires_publication_authorization_and_owned_source(publisher):
    client, factory, _, state, headers = publisher
    state["owner"] = 2
    response = client.post(ROOT + "/publication-previews", json=BODY, headers=headers)
    assert response.status_code == 403
    state["owner"] = 1
    with factory() as db:
        db.query(GithubCredential).delete()
        db.commit()
    response = client.post(ROOT + "/publication-previews", json=BODY, headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "github_authorization_required"


@pytest.mark.parametrize("endpoint", ["history", "status"])
def test_lightweight_publication_queries_do_not_load_frozen_documents(publisher, endpoint):
    client, factory, _, _, headers = publisher
    reviewed = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    ident = reviewed["id"]
    with factory() as db:
        row = db.get(CatalogPublication, ident)
        large_text = "frozen-document-" + "x" * 100_000
        row.files = row.files + [{
            "path": "models/demo/report.html", "kind": "report",
            "text": large_text, "diff": large_text,
        }]
        row.snapshot = row.snapshot | {"paths": {"large-tree": large_text}}
        row.request = row.request | {"export_warnings": [large_text]}
        row.status, row.pr_number = "succeeded", 42
        row.pr_url = "https://github.com/PsLink/model-catalog/pull/42"
        db.commit()

    projections = []

    def capture_query(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "catalog_publications" in statement:
            projections.append({
                column.key for column in context.compiled.statement.selected_columns
            })

    engine = client.app.state.engine
    event.listen(engine, "before_cursor_execute", capture_query)
    try:
        path = "/publications" if endpoint == "history" else f"/publications/{ident}/status"
        response = client.get(ROOT + path)
    finally:
        event.remove(engine, "before_cursor_execute", capture_query)
    assert response.status_code == 200, response.text
    result = response.json()[0] if endpoint == "history" else response.json()
    assert result["id"] == ident and result["pr_number"] == 42
    assert result["status"] == "succeeded"
    assert result["pr_url"].endswith("/pull/42")
    assert not {"files", "snapshot", "request", "body", "warnings"} & result.keys()
    assert len(response.content) < 2000
    assert len(projections) == 1
    assert not {"files", "snapshot", "request", "body"} & projections[0]
    if endpoint == "history":
        assert result["title"] == reviewed["title"]
        assert result["repository"] == reviewed["repository"]

    detail = client.get(ROOT + f"/publications/{ident}").json()
    assert detail["files"][0] == reviewed["files"][0]
    assert detail["body"] == reviewed["body"]
    assert "text" not in detail["files"][-1] and detail["files"][-1]["diff"] == ""


def test_history_filters_legacy_jobs_before_limit_and_status_enforces_ownership(publisher):
    client, factory, _, _, headers = publisher
    reviewed = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    with factory() as db:
        row = db.get(CatalogPublication, reviewed["id"])
        for i in range(100):
            db.add(CatalogPublication(
                id=f"legacy-{i}", user_id=row.user_id, github_id=row.github_id,
                fingerprint=f"legacy-{i}", snapshot={}, files=[], request={},
                title="Legacy", body="", branch=f"legacy-{i}",
                created_at=now() + timedelta(seconds=i + 1),
            ))
        db.commit()
    response = client.get(ROOT + "/publications")
    assert [job["id"] for job in response.json()] == [reviewed["id"]]
    status_url = ROOT + f"/publications/{reviewed['id']}/status"
    assert client.get(status_url).status_code == 200
    assert client.get(ROOT + "/publications/missing/status").status_code == 404
    assert client.get(ROOT + "/publications/legacy-0/status").status_code == 404
    sign_in(client, "bob")
    assert client.get(status_url).status_code == 404
    assert client.get(ROOT + "/publications").json() == []
    client.cookies.clear()
    assert client.get(status_url).status_code == 401


def test_repreview_after_branch_conflict_starts_an_idempotent_new_review(publisher):
    client, factory, _, _, headers = publisher
    previous = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    # Conflicts can happen again on a replacement branch, without changing the
    # reviewed files or the target's base commit.
    for _ in range(2):
        with factory() as db:
            old = db.get(CatalogPublication, previous["id"])
            old.status = "needs_review"
            old.error = "Publication branch was modified; create a new preview"
            old.commit_sha = "reviewed-commit"
            old_branch = old.branch
            old_fingerprint = old.fingerprint
            db.commit()

        response = client.post(ROOT + "/publication-previews", json=BODY, headers=headers)
        assert response.status_code == 200, response.text
        reviewed = response.json()
        assert reviewed["id"] != previous["id"]
        assert reviewed["status"] == "preview"
        assert reviewed["files"] == previous["files"]
        assert reviewed["base_sha"] == previous["base_sha"]
        repeated = client.post(ROOT + "/publication-previews", json=BODY, headers=headers)
        assert repeated.json()["id"] == reviewed["id"]

        with factory() as db:
            old = db.get(CatalogPublication, previous["id"])
            replacement = db.get(CatalogPublication, reviewed["id"])
            assert old.status == "needs_review" and old.commit_sha == "reviewed-commit"
            assert old.branch == old_branch and old.fingerprint == old_fingerprint
            assert replacement.branch != old_branch
            assert replacement.commit_sha == "" and replacement.fork == ""

        submitted = client.post(
            ROOT + f"/publications/{reviewed['id']}/submit", headers=headers
        )
        assert submitted.status_code == 202, submitted.text
        assert submitted.json()["status"] == "queued"
        repeated = client.post(ROOT + "/publication-previews", json=BODY, headers=headers)
        assert repeated.json()["id"] == reviewed["id"]
        previous = reviewed


def test_invalid_export_and_browser_supplied_files_are_refused(publisher):
    client, _, _, state, headers = publisher
    state["export"]["valid"] = False
    state["export"]["errors"] = ["No measured result"]
    assert (
        client.post(ROOT + "/publication-previews", json=BODY, headers=headers).status_code == 422
    )
    assert (
        client.post(
            ROOT + "/publication-previews", json=BODY | {"files": []}, headers=headers
        ).status_code
        == 422
    )


def test_submit_rechecks_ownership_and_connection(publisher):
    client, _, config, state, headers = publisher
    job = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    url = ROOT + f"/publications/{job['id']}/submit"
    state["owner"] = 2
    assert client.post(url, headers=headers).status_code == 403
    state["owner"] = 1
    config.upstream_url = "http://another-instance"
    assert client.post(url, headers=headers).status_code == 409


def test_target_and_identity_are_part_of_preview_identity(publisher):
    client, _, config, _, headers = publisher
    first = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    config.catalog_repository = "modelsphere/model-catalog"
    second = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    assert first["id"] != second["id"]
    assert (
        client.get(ROOT + f"/publications/{first['id']}").json()["repository"]
        == "PsLink/model-catalog"
    )


def test_background_worker_creates_pr_and_query_returns_link(publisher, monkeypatch):
    from test_catalog_publishing import Provider

    from baseline_hub import publishing

    client, _, config, _, headers = publisher
    reviewed = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    url = ROOT + f"/publications/{reviewed['id']}"
    assert client.post(url + "/submit", headers=headers).status_code == 202

    class Remote(Provider):
        def post(self, path, data):
            if path.endswith("/pulls"):
                self.calls.append(("POST", path, data))
                self.pr = {
                    "number": 42,
                    "html_url": "https://github.com/PsLink/model-catalog/pull/42",
                }
                return self.pr
            return super().post(path, data)

        def close(self):
            pass

    remote = Remote()
    targets = []

    def github(token, *, repository):
        assert token == "github-token"
        targets.append(repository)
        return remote

    monkeypatch.setattr(publishing, "Github", github)
    monkeypatch.setattr(
        publishing,
        "decrypt",
        lambda value: Fernet(config.credential_key.encode()).decrypt(value.encode()).decode(),
    )
    config.catalog_repository = "changed/target"
    publishing.tick(client.app.state.factory)
    done = client.get(url).json()
    assert targets == ["PsLink/model-catalog"]
    assert done["status"] == "succeeded" and done["pr_url"].endswith("/pull/42")
    assert done["files"] == reviewed["files"]
    assert client.post(url + "/submit", headers=headers).json()["status"] == "succeeded"
    publishing.tick(client.app.state.factory)
    assert (
        sum(method == "POST" and path.endswith("/pulls") for method, path, _ in remote.calls) == 1
    )


def test_expired_backend_connection_cannot_submit_reviewed_job(publisher):
    client, _, _, state, headers = publisher
    reviewed = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    state["expired"] = True
    response = client.post(ROOT + f"/publications/{reviewed['id']}/submit", headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "upstream_auth_required"
    assert client.get("/api/auth/me").status_code == 200
    assert client.get(ROOT + f"/publications/{reviewed['id']}").json()["status"] == "preview"


def test_late_submit_cannot_requeue_a_job_completed_during_access_check(publisher, monkeypatch):
    from baseline_hub import publication_api

    client, factory, _, _, headers = publisher
    reviewed = client.post(ROOT + "/publication-previews", json=BODY, headers=headers).json()
    original = publication_api.check_sources

    def racing_check(*args):
        original(*args)
        # Another request queued the same task and a worker completed it meanwhile.
        with factory() as db:
            row = db.get(CatalogPublication, reviewed["id"])
            row.status = "succeeded"
            row.pr_number, row.pr_url = 42, "https://github.com/PsLink/model-catalog/pull/42"
            db.commit()

    monkeypatch.setattr(publication_api, "check_sources", racing_check)
    response = client.post(ROOT + f"/publications/{reviewed['id']}/submit", headers=headers)
    assert response.status_code == 202
    assert response.json()["status"] == "succeeded"


def track_export(publisher, *, html=True):
    client, _, _, _, _ = publisher
    original = client.app.state.platform_transport
    report = {"baseline_run_id": 5, "attempt_run_ids": [6]}

    def remote(request):
        if request.url.path == "/api/baseline-hub/tracks/demo":
            return httpx.Response(200, json={"board": [
                {"run_id": 5, "is_reference": True}, {"run_id": 6}, {"run_id": 7},
            ]})
        if request.url.path in {"/api/runs/6", "/api/runs/7"}:
            return httpx.Response(200, json={"campaign_id": 12})
        if request.url.path == "/api/agent/v1/reports/4":
            return httpx.Response(200, json=report)
        return original.handle_request(request)

    client.app.state.platform_transport = httpx.MockTransport(remote)
    if html:
        publisher[3]["export"]["files"].append({
            "path": "models/demo/demo-report.html", "kind": "report", "text": "<html>report</html>",
        })
    return BODY | {
        "track_slug": "demo", "report_id": 4,
        "variants": [{"run_id": 5, "role": "baseline"}, {"run_id": 6, "role": "optimized"}],
    }


def test_track_report_html_is_frozen_in_publication(publisher):
    client, factory, _, state, headers = publisher
    body = track_export(publisher)
    response = client.post(ROOT + "/publication-previews", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert "track_slug" not in state["export_body"]
    assert response.json()["body"] == (
        "demo: http://testserver/baseline-hub/tracks/demo\n\n"
        "Entry: baseline Run ID: 5\nEntry: optimized Run ID: 6\n\n"
        "Details: [Report](http://testserver/reports/4)"
    )
    with factory() as db:
        row = db.get(CatalogPublication, response.json()["id"])
        assert any(f["text"] == "<html>report</html>" for f in row.files)
    html = next(f for f in response.json()["files"] if f["kind"] == "report")
    assert html["diff"] == "" and "text" not in html
    # Reject a browser attempt to replace the administrator's baseline.
    bad = body | {"variants": [
        {"run_id": 7, "role": "baseline"}, {"run_id": 6, "role": "optimized"},
    ]}
    assert client.post(ROOT + "/publication-previews", json=bad, headers=headers).status_code == 422


def test_track_export_requires_completed_report_and_html(publisher):
    client, _, _, _, headers = publisher
    body = track_export(publisher, html=False)
    for source in (body, body | {"report_id": None}):
        response = client.post(ROOT + "/publication-previews", json=source, headers=headers)
        assert response.status_code == 409, response.text


@pytest.mark.parametrize("endpoint", [
    "/api/platform/api/catalog-export/preview",
    "/api/platform/api/catalog-export/archive",
    ROOT + "/publication-previews",
])
@pytest.mark.parametrize("scenario,expected", [
    ("foreign_opt", 403),
    ("shared_reference", 200),
    ("foreign_baseline_without_track", 403),
    ("foreign_baseline_without_reference", 200),
    ("foreign_opt_without_reference", 403),
    ("foreign_baseline_outside_board", 403),
    ("reference_not_ready", 403),
    ("foreign_reference_as_opt", 403),
])
def test_exports_require_own_results_except_the_tracks_permitted_baseline(
    publisher, endpoint, scenario, expected,
):
    client, _, _, state, headers = publisher
    body = track_export(publisher)
    original = client.app.state.platform_transport

    def remote(request):
        path = request.url.path
        if path in {"/api/runs/5", "/api/runs/6"}:
            return httpx.Response(200, json={"campaign_id": int(path.rsplit("/", 1)[1])})
        if path in {"/api/campaigns/5", "/api/campaigns/6"}:
            foreign = 6 if scenario in {"foreign_opt", "foreign_opt_without_reference"} else 5
            return httpx.Response(200, json={
                "owner_id": 2 if path.endswith(f"/{foreign}") else 1,
            })
        if path.endswith("/tracks/demo"):
            if scenario in {"foreign_baseline_without_reference", "foreign_opt_without_reference"}:
                return httpx.Response(200, json={"board": [{"run_id": 5}, {"run_id": 6}]})
            if scenario == "foreign_baseline_outside_board":
                return httpx.Response(200, json={"board": [{"run_id": 6}]})
            if scenario == "reference_not_ready":
                return httpx.Response(200, json={
                    "reference": {"measurement_id": 99},
                    "board": [{"run_id": 5}, {"run_id": 6}],
                })
        return original.handle_request(request)

    client.app.state.platform_transport = httpx.MockTransport(remote)
    if scenario == "foreign_baseline_without_track":
        body.pop("track_slug")
    if scenario == "foreign_reference_as_opt":
        body["variants"] = [
            {"run_id": 6, "role": "baseline"}, {"run_id": 5, "role": "optimized"},
        ]
    response = client.post(endpoint, json=body, headers=headers)
    assert response.status_code == expected, response.text
    if expected == 403:
        assert "export_body" not in state
        assert not any(r.method == "POST" and r.url.path.endswith("/archive")
                       for r in state["requests"])


@pytest.mark.parametrize("operation", ["preview", "archive"])
def test_empty_export_cannot_bypass_source_checks(publisher, operation):
    client, _, _, _, headers = publisher
    response = client.post(
        f"/api/platform/api/catalog-export/{operation}", json={"variants": []}, headers=headers,
    )
    assert response.status_code == 422


def test_submit_rejects_foreign_opt_even_when_baseline_is_still_owned(publisher):
    client, _, _, _, headers = publisher
    body = track_export(publisher)
    reviewed = client.post(ROOT + "/publication-previews", json=body, headers=headers).json()
    original = client.app.state.platform_transport

    def remote(request):
        if request.url.path == "/api/runs/6":
            return httpx.Response(200, json={"campaign_id": 13})
        if request.url.path == "/api/campaigns/13":
            return httpx.Response(200, json={"owner_id": 2})
        return original.handle_request(request)

    client.app.state.platform_transport = httpx.MockTransport(remote)
    path = ROOT + f"/publications/{reviewed['id']}"
    assert client.post(path + "/submit", headers=headers).status_code == 403
    assert client.get(path).json()["status"] == "preview"


@pytest.mark.parametrize("new_reference,expected", [(None, 202), (7, 403)])
def test_submit_rechecks_a_freely_selected_baseline(publisher, new_reference, expected):
    client, _, _, _, headers = publisher
    body = track_export(publisher)
    original = client.app.state.platform_transport
    reference = None

    def remote(request):
        if request.url.path.endswith("/tracks/demo"):
            return httpx.Response(200, json={"board": [
                {"run_id": run, "is_reference": run == reference} for run in (5, 6, 7)
            ]})
        if request.url.path == "/api/runs/5":
            return httpx.Response(200, json={"campaign_id": 13})
        if request.url.path == "/api/campaigns/13":
            return httpx.Response(200, json={"owner_id": 2})
        return original.handle_request(request)

    client.app.state.platform_transport = httpx.MockTransport(remote)
    preview = client.post(ROOT + "/publication-previews", json=body, headers=headers)
    assert preview.status_code == 200, preview.text
    reference = new_reference
    path = ROOT + f"/publications/{preview.json()['id']}"
    assert client.post(path + "/submit", headers=headers).status_code == expected
    assert client.get(path).json()["status"] == ("queued" if expected == 202 else "preview")
