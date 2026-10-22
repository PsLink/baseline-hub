"""Publication writes are atomic, restartable and scoped to reviewed catalog files."""

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from baseline_hub import publishing as pub
from baseline_hub.catalog import CatalogFile
from baseline_hub.db import Base
from baseline_hub.models import CatalogPublication, User
from baseline_hub.models import GithubCredential as GithubPublishingCredential


@pytest.fixture
def jobs():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        user = User(username="publisher", password_hash="unused", role="user")
        session.add(user)
        session.flush()
        session.add(
            GithubPublishingCredential(
                user_id=user.id, github_id="12", encrypted_token="encrypted", scopes="public_repo"
            )
        )
        job = CatalogPublication(
            id="publication-1",
            user_id=user.id,
            github_id="12",
            fingerprint="fingerprint",
            status="queued",
            snapshot={
                "sha": "base",
                "tree": "old-tree",
                "base": "main",
                "repository_id": 100,
                "paths": {},
            },
            files=[
                {"path": "models/demo/metadata.yaml", "text": "name: demo\n"},
                {"path": "models/demo/demo.validation.json", "text": "{}"},
            ],
            request={"variants": [{"run_id": 1}]},
            title="catalog: demo",
            body="Reviewed export",
            branch="autotune/catalog/publication-1",
        )
        session.add(job)
        session.commit()
        yield session, job, factory
    engine.dispose()


class Provider:
    def __init__(self):
        self.calls = []
        self.ref = None
        self.pr = None
        self.base = "base"
        self.fork_ready = True
        self.modified = False
        self.drop_pr_response = False
        self.drop_ref_response = False

    def get(self, path, **kw):
        self.calls.append(("GET", path, kw))
        if path == "/user":
            return {"id": 12, "login": "alice"}
        if path == "/repos/alice/model-catalog":
            return (
                {
                    "fork": True,
                    "source": {"id": 999 if self.modified else 100},
                    "full_name": "alice/model-catalog",
                }
                if self.fork_ready
                else None
            )
        if path.endswith("/pulls"):
            return [self.pr] if self.pr else []
        if "/git/ref/" in path:
            return {"object": {"sha": self.ref}} if self.ref else None
        if path.endswith("/commits/main"):
            return {"sha": self.base}
        raise AssertionError(path)

    def post(self, path, data):
        self.calls.append(("POST", path, data))
        if path.endswith("/forks"):
            self.fork_ready = True
            return {}
        if path.endswith("/git/trees"):
            assert data["base_tree"] == "old-tree"
            assert len(data["tree"]) == 2
            return {"sha": "new-tree"}
        if path.endswith("/git/commits"):
            assert data["parents"] == ["base"]
            return {"sha": "commit"}
        if path.endswith("/git/refs"):
            self.ref = data["sha"]
            if self.drop_ref_response:
                self.drop_ref_response = False
                raise httpx.ReadTimeout("lost response")
            return {}
        if path.endswith("/pulls"):
            assert data["head"] == "alice:autotune/catalog/publication-1"
            self.pr = {
                "number": 42,
                "html_url": "https://github.com/modelsphere/model-catalog/pull/42",
            }
            if self.drop_pr_response:
                self.drop_pr_response = False
                raise httpx.ReadTimeout("lost response")
            return self.pr
        raise AssertionError(path)


def test_single_commit_and_pr_retry_is_idempotent(jobs):
    session, job, _ = jobs
    github = Provider()
    github.drop_pr_response = True
    with pytest.raises(httpx.ReadTimeout):
        pub.execute(session, job, github)
    assert job.commit_sha == "commit"
    pub.execute(session, job, github)
    assert job.status == "succeeded"
    assert job.pr_number == 42
    assert (
        sum(method == "POST" and path.endswith("/pulls") for method, path, _ in github.calls) == 1
    )
    assert (
        sum(method == "POST" and path.endswith("/git/commits") for method, path, _ in github.calls)
        == 1
    )


def test_lost_branch_response_does_not_duplicate_commit(jobs):
    session, job, _ = jobs
    github = Provider()
    github.drop_ref_response = True
    with pytest.raises(httpx.ReadTimeout):
        pub.execute(session, job, github)
    session.expire_all()  # simulate restart and reload durable checkpoints
    pub.execute(session, session.get(CatalogPublication, job.id), github)
    assert job.status == "succeeded"
    assert sum(m == "POST" and p.endswith("/git/commits") for m, p, _ in github.calls) == 1


def test_waits_for_fork_without_touching_default_branch(jobs):
    session, job, _ = jobs
    github = Provider()
    github.fork_ready = False
    pub.execute(session, job, github)
    assert job.status == "forking"
    pub.execute(session, job, github)
    assert job.status == "succeeded"
    assert not any(m == "PATCH" for m, _, _ in github.calls)


@pytest.mark.parametrize("problem", ["base", "fork", "branch", "identity"])
def test_conflicts_stop_before_writes(jobs, problem):
    session, job, _ = jobs
    github = Provider()
    if problem == "base":
        github.base = "changed"
    elif problem == "fork":
        github.modified = True
    elif problem == "branch":
        github.ref, job.commit_sha = "manual", "expected"
    else:
        job.github_id = "99"
    with pytest.raises(pub.PublishingError):
        pub.execute(session, job, github)
    assert not any(m == "POST" for m, _, _ in github.calls)


def test_worker_retry_persists_and_disconnect_stops_job(jobs, monkeypatch):
    session, job, factory = jobs
    monkeypatch.setattr(pub, "decrypt", lambda token: "token")
    github = Provider()
    github.drop_pr_response = True
    github.close = lambda: None
    monkeypatch.setattr(pub, "Github", lambda token: github)
    pub.tick(factory)
    session.expire_all()
    assert job.status == "queued" and job.next_attempt_at is not None
    session.delete(session.get(GithubPublishingCredential, job.user_id))
    job.next_attempt_at = None
    session.commit()
    pub.tick(factory)
    session.expire_all()
    assert job.status == "needs_authorization"
    assert session.scalar(select(CatalogPublication)).pr_url == ""


@pytest.mark.parametrize(
    "path", ["../secret", "/models/demo/a", "models/demo/../x", ".github/a.yml"]
)
def test_rejects_paths_outside_model(path):
    with pytest.raises(pub.PublishingError):
        pub.changes(None, {"paths": {}}, [CatalogFile(path, "variant", "x")], "demo")


def test_unchanged_files_are_not_committed():
    github = SimpleNamespace(blob=lambda sha: "same")
    assert (
        pub.changes(
            github,
            {"paths": {"models/demo/a.yaml": "sha"}},
            [CatalogFile("models/demo/a.yaml", "variant", "same")],
            "demo",
        )
        == []
    )


def test_snapshot_preserves_metadata_and_variant_versions(monkeypatch):
    github = pub.Github("token")
    data = {
        "meta": "source: {hf: Org/Model}\ntuning: [{id: old}]\n",
        "variant": "id: fast\nversion: 1.3.0\n",
    }
    monkeypatch.setattr(github, "blob", lambda sha: data[sha])
    try:
        model = github.model(
            {"paths": {"models/other/metadata.yaml": "meta", "models/other/fast.yaml": "variant"}},
            "demo",
            "Org/Model",
        )
        assert model.name == "other"
        assert model.metadata["tuning"] == [{"id": "old"}]
        assert model.variants["fast"]["version"] == "1.3.0"
    finally:
        github.close()


def test_rate_limit_preserves_provider_delay():
    github = pub.Github("token")
    github.client.close()
    github.client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "1800"})
        ),
    )
    try:
        with pytest.raises(pub.PublishingError) as error:
            github.get("/user")
        assert error.value.status == "queued"
        assert error.value.retry_after >= 1800
    finally:
        github.close()


def test_snapshot_refuses_hugging_face_name_collision(monkeypatch):
    github = pub.Github("token")
    monkeypatch.setattr(github, "blob", lambda sha: "source: {hf: Other/Model}\n")
    try:
        with pytest.raises(pub.PublishingError, match="different Hugging Face"):
            github.model({"paths": {"models/demo/metadata.yaml": "sha"}}, "demo", "Org/Model")
    finally:
        github.close()


def test_forked_target_uses_network_identity_and_frozen_repository(jobs):
    session, job, _ = jobs
    job.snapshot = job.snapshot | {
        "repository": "team/model-catalog",
        "source_repository_id": 999,
    }
    github = Provider()
    github.modified = True  # User fork's source is network root, not target fork ID.
    pub.execute(session, job, github)
    assert job.status == "succeeded"
    assert any(p == "/repos/team/model-catalog/pulls" for _, p, _ in github.calls)
    assert not any("/repos/PsLink/" in p for _, p, _ in github.calls)


def test_target_owner_can_publish_a_branch_in_their_own_repository(jobs):
    session, job, _ = jobs
    job.snapshot = job.snapshot | {"repository": "alice/model-catalog"}
    github = Provider()
    original_get = github.get

    def get(path, **kw):
        if path == "/repos/alice/model-catalog":
            return {"id": 100, "full_name": "alice/model-catalog", "fork": False}
        return original_get(path, **kw)

    github.get = get
    pub.execute(session, job, github)
    assert job.status == "succeeded"
    assert not any(p.endswith("/forks") for _, p, _ in github.calls)
