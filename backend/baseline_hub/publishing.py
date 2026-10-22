"""GitHub catalog snapshots and restartable, single-commit publication jobs."""

from __future__ import annotations

import base64
import difflib
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import or_, select, update

from baseline_hub.catalog_lookup import CatalogLookup, catalog_cache
from baseline_hub.config import Settings
from baseline_hub.models import CatalogPublication
from baseline_hub.models import GithubCredential as GithubPublishingCredential
from baseline_hub.publishing_errors import PublishingError

TARGET = "PsLink/model-catalog"


def cipher(key=None):
    try:
        return Fernet((Settings().credential_key if key is None else key).encode())
    except (ValueError, TypeError):
        raise PublishingError("GitHub publishing encryption key is not configured") from None


def decrypt(value, key=None):
    try:
        return cipher(key).decrypt(value.encode()).decode()
    except InvalidToken:
        raise PublishingError("Authorize GitHub publishing again", "needs_authorization") from None


class Github:
    def __init__(self, token, *, repository=TARGET, cache=None):
        self.repository = repository
        scope = repository.lower(), hashlib.sha256(token.encode()).hexdigest()
        self._catalog = CatalogLookup(scope, catalog_cache if cache is None else cache)
        self.client = httpx.Client(
            base_url="https://api.github.com",
            timeout=20,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self):
        self.client.close()

    def call(self, method, path, *, missing=False, **kwargs):
        r = self.client.request(method, path, **kwargs)
        if missing and r.status_code == 404:
            return None
        if r.status_code == 401:
            raise PublishingError("Authorize GitHub publishing again", "needs_authorization")
        if (
            r.status_code == 429
            or r.status_code >= 500
            or (
                r.status_code == 403
                and (r.headers.get("retry-after") or r.headers.get("x-ratelimit-remaining") == "0")
            )
        ):
            try:
                retry_after = max(
                    int(r.headers.get("retry-after", "0")),
                    int(r.headers.get("x-ratelimit-reset", "0"))
                    - int(datetime.now(UTC).timestamp()),
                )
            except ValueError:
                retry_after = 600
            raise PublishingError(
                "GitHub rate limited or unavailable; retry scheduled", "queued", retry_after
            )
        if r.status_code == 403:
            raise PublishingError(
                "GitHub denied access; check publishing authorization", "needs_authorization"
            )
        if r.status_code >= 400:
            # Never propagate provider bodies or request URLs containing credentials.
            raise PublishingError(
                f"GitHub returned HTTP {r.status_code}; retry later or check access"
            )
        return r.json() if r.content else None

    def get(self, path, **kwargs):
        return self.call("GET", path, **kwargs)

    def post(self, path, data):
        return self.call("POST", path, json=data)

    def blob(self, sha):
        def read():
            blob = self.get(f"/repos/{self.repository}/git/blobs/{sha}")
            return base64.b64decode(blob["content"]).decode()

        return self._catalog.blob(sha, read)

    def snapshot(self):
        repo = self.get(f"/repos/{self.repository}")
        branch = repo["default_branch"]
        commit = self.get(f"/repos/{self.repository}/commits/{quote(branch, safe='')}")
        tree_sha = commit["commit"]["tree"]["sha"]
        paths = self._catalog.paths(tree_sha, lambda: self.get(
            f"/repos/{self.repository}/git/trees/{tree_sha}", params={"recursive": "1"}
        ))
        return {
            "repository_id": repo["id"],
            "source_repository_id": (repo.get("source") or repo)["id"],
            "base": branch,
            "sha": commit["sha"],
            "tree": commit["commit"]["tree"]["sha"],
            "paths": paths,
        }

    def model(self, snapshot, name, hf):
        return self._catalog.model(snapshot, name, hf, self.blob)


def changes(github, snapshot, files, name):
    result = []
    for file in files:
        path = file.path
        parts = PurePosixPath(path).parts
        if (
            len(parts) < 3
            or parts[:2] != ("models", name)
            or ".." in parts
            or path.startswith("/")
            or "\\" in path
        ):
            raise PublishingError("Export contains an invalid publication path")
        old = github.blob(snapshot["paths"][path]) if path in snapshot["paths"] else ""
        if old == file.text:
            continue
        result.append(
            {
                "path": path,
                "text": file.text,
                "kind": file.kind,
                "diff": "".join(
                    difflib.unified_diff(
                        old.splitlines(True),
                        file.text.splitlines(True),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                    )
                ),
            }
        )
    if sum(len(f["text"].encode()) for f in result) > 10_000_000:
        raise PublishingError("Export exceeds the 10 MB publication limit")
    return result


def execute(session, job, github):
    """Check remote state before mutations; durable checkpoints survive lost responses."""
    credential = session.get(GithubPublishingCredential, job.user_id)
    if not credential or credential.github_id != job.github_id:
        raise PublishingError("Authorize GitHub publishing again", "needs_authorization")
    profile = github.get("/user")
    if str(profile["id"]) != job.github_id:
        raise PublishingError("GitHub account does not match", "needs_authorization")
    target = job.snapshot.get("repository", TARGET)
    repository_name = target.split("/")[1]
    branch = job.branch
    if not job.fork:
        fork = github.get(f"/repos/{profile['login']}/{repository_name}", missing=True)
        if fork is None:
            job.status = "forking"
            session.commit()
            github.post(f"/repos/{target}/forks", {"default_branch_only": True})
            return  # the next worker tick checks readiness, including after restart
        is_target = fork.get("id") == job.snapshot["repository_id"]
        same_network = fork.get("fork") and (fork.get("source") or {}).get("id") == (
            job.snapshot.get("source_repository_id", job.snapshot["repository_id"])
        )
        if not (is_target or same_network):
            raise PublishingError(
                "An unrelated model-catalog repository already exists in your account"
            )
        job.fork = fork["full_name"]
        session.commit()
    root = f"/repos/{job.fork}"
    prs = github.get(
        f"/repos/{target}/pulls",
        params={
            "state": "all",
            "head": f"{profile['login']}:{branch}",
            "base": job.snapshot["base"],
        },
    )
    if prs:
        pr = prs[0]
    else:
        ref = github.get(f"{root}/git/ref/heads/{branch}", missing=True)
        if ref and job.commit_sha and ref["object"]["sha"] != job.commit_sha:
            raise PublishingError(
                "Publication branch was modified; create a new preview", "needs_review"
            )
        if ref and not job.commit_sha:
            commit = github.get(f"{root}/git/commits/{ref['object']['sha']}")
            expected = f"autotune-publication:{job.id}"
            if expected not in commit["message"]:
                raise PublishingError("Publication branch already exists", "needs_review")
            job.commit_sha = ref["object"]["sha"]
            session.commit()
        if not ref:
            current = github.get(f"/repos/{target}/commits/{quote(job.snapshot['base'], safe='')}")
            if current["sha"] != job.snapshot["sha"]:
                raise PublishingError(
                    "Catalog changed; refresh the publication preview", "needs_review"
                )
            job.status = "committing"
            session.commit()
            if not job.commit_sha:
                tree = github.post(
                    f"{root}/git/trees",
                    {
                        "base_tree": job.snapshot["tree"],
                        "tree": [
                            {
                                "path": f["path"],
                                "mode": "100644",
                                "type": "blob",
                                "content": f["text"],
                            }
                            for f in job.files
                        ],
                    },
                )
                commit = github.post(
                    f"{root}/git/commits",
                    {
                        "message": f"{job.title}\n\nautotune-publication:{job.id}",
                        "tree": tree["sha"],
                        "parents": [job.snapshot["sha"]],
                    },
                )
                job.commit_sha = commit["sha"]
                session.commit()
            github.post(f"{root}/git/refs", {"ref": f"refs/heads/{branch}", "sha": job.commit_sha})
        job.status = "opening_pr"
        session.commit()
        pr = github.post(
            f"/repos/{target}/pulls",
            {
                "title": job.title,
                "body": job.body,
                "head": f"{profile['login']}:{branch}",
                "base": job.snapshot["base"],
                "maintainer_can_modify": True,
            },
        )
    job.pr_number, job.pr_url = pr["number"], pr["html_url"]
    job.pr_state = "merged" if pr.get("merged_at") else pr.get("state", "open")
    job.status, job.error = "succeeded", ""
    session.commit()


def tick(session_factory):
    """Called by the singleton worker; never hold database transactions over retries."""
    with session_factory() as session:
        job = session.scalar(
            select(CatalogPublication)
            .where(
                CatalogPublication.status.in_(["queued", "forking", "committing", "opening_pr"]),
                or_(
                    CatalogPublication.lease_until.is_(None),
                    CatalogPublication.lease_until < datetime.now(UTC),
                ),
                or_(
                    CatalogPublication.next_attempt_at.is_(None),
                    CatalogPublication.next_attempt_at <= datetime.now(UTC),
                ),
            )
            .order_by(CatalogPublication.created_at)
            .limit(1)
        )
        if job is None:
            return
        claimed = session.execute(
            update(CatalogPublication)
            .where(
                CatalogPublication.id == job.id,
                or_(
                    CatalogPublication.lease_until.is_(None),
                    CatalogPublication.lease_until < datetime.now(UTC),
                ),
            )
            .values(lease_until=datetime.now(UTC) + timedelta(minutes=10))
        )
        if claimed.rowcount != 1:
            session.rollback()
            return
        github = None
        job.attempts += 1
        job.next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=min(600, 10 * 2 ** min(job.attempts, 6))
        )
        session.commit()
        try:
            if job.attempts > 12:
                raise PublishingError("GitHub publishing retry limit reached; retry manually")
            credential = session.get(GithubPublishingCredential, job.user_id)
            if credential is None:
                raise PublishingError("Authorize GitHub publishing again", "needs_authorization")
            if "repository" in job.snapshot:
                github = Github(
                    decrypt(credential.encrypted_token), repository=job.snapshot["repository"]
                )
            else:
                github = Github(decrypt(credential.encrypted_token))
            execute(session, job, github)
        except (PublishingError, httpx.HTTPError) as exc:
            job.status = exc.status if isinstance(exc, PublishingError) else "queued"
            job.error = (
                str(exc) if isinstance(exc, PublishingError) else "GitHub unavailable; retry later"
            )
            if isinstance(exc, PublishingError) and exc.retry_after:
                job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=max(1, exc.retry_after))
            session.commit()
        finally:
            if github:
                github.close()
            job.lease_until = None
            session.commit()


def main():
    import logging
    import time

    from baseline_hub.db import database

    logging.basicConfig(level=logging.INFO)
    engine, factory = database(Settings().database_url)
    try:
        while True:
            try:
                tick(factory)
            except Exception:
                logging.getLogger(__name__).error("Publication reconciliation failed")
            time.sleep(5)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
