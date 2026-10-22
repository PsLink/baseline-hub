"""Review and publish exports obtained with the current AutoTune browser session."""

import hashlib
import json
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from baseline_hub import publishing
from baseline_hub.auth import identity
from baseline_hub.catalog import slug
from baseline_hub.catalog.requests import CatalogExportRequest
from baseline_hub.catalog_exports import check_sources, preview_export
from baseline_hub.catalog_reports import check_comparison, upstream_source
from baseline_hub.models import CatalogPublication, GithubCredential, GithubIdentity, User
from baseline_hub.publication_files import prepare_files
from baseline_hub.publications import (
    output,
    owned_publication,
    publication_body,
    publication_status_query,
    publication_summary_query,
    publication_title,
)
from baseline_hub.sessions import current_session, db_session
from baseline_hub.upstream import authorized

router = APIRouter(prefix="/catalog-export", tags=["catalog publications"])


class Variant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: int = Field(gt=0)
    role: str = ""
    label: str = ""
    id: str = ""
    description: str = ""
    gpu_product: list[str] = Field(default_factory=list)
    nodes: int | None = Field(default=None, ge=1)
    image_tag: str = ""


class ExportRequest(CatalogExportRequest):
    model_config = ConfigDict(extra="forbid")
    variants: list[Variant] = Field(min_length=1, max_length=50)
    track_slug: str = Field(default="", pattern=r"^[a-zA-Z0-9_.-]{0,128}$")


def credential(db, user_id):
    token = db.get(GithubCredential, user_id)
    linked = db.scalar(select(GithubIdentity).where(GithubIdentity.user_id == user_id))
    if not token or not linked or token.github_id != linked.github_id:
        raise HTTPException(
            409,
            {
                "code": "github_authorization_required",
                "message": "请先在账号页授权 GitHub 发布",
            },
        )
    return token


def result(row):
    review = output(row)
    # Keep complete HTML in the frozen publication, without sending its source/diff
    # to the review UI. The publisher reads the immutable database files directly.
    review["files"] = [
        {k: v for k, v in file.items() if k not in {"text", "diff"}} | {"diff": ""}
        if file.get("kind") == "report" or file["path"].endswith(".html") else file
        for file in review["files"]
    ]
    return review | {
        "repository": row.snapshot["repository"],
        "base_sha": row.snapshot["sha"],
        "base_branch": row.snapshot["base"],
        "warnings": row.request.get("export_warnings", []),
    }


@router.post("/publication-previews")
def preview(
    body: ExportRequest,
    request: Request,
    browser=Depends(current_session),
    db=Depends(db_session),
):
    config = request.app.state.settings
    token = credential(db, browser.user_id)
    source = body.model_dump(mode="json")
    with authorized(request, browser, db) as http:
        linked = identity(db, browser.user_id, config)
        campaigns = check_sources(http, source, linked)
        track = check_comparison(http, source, require_report=True)
        github = publishing.Github(
            publishing.decrypt(token.encrypted_token, config.credential_key),
            repository=config.catalog_repository,
        )
        try:
            snapshot = github.snapshot() | {"repository": config.catalog_repository}
            # Obtain complete variant documents, even when the published index is stale.
            exported = preview_export(
                http, upstream_source(source) | {"use_catalog": False}
            ).json()
            files, name, _ = prepare_files(
                github, snapshot, exported, upstream_source(source), http
            )
            benchmarks = []
            if track:
                benchmarks.append((
                    track.get("benchmark_slug") or track.get("name") or source["track_slug"],
                    source["track_slug"],
                ))
            else:
                benchmarks = [
                    (c.get("benchmark_slug") or c["track_slug"], c["track_slug"])
                    for c in campaigns if c.get("track_slug")
                ]
            exported_variants = exported.get("variants") or []
            variants = [
                (
                    item.get("label") or item.get("id")
                    or (exported_variants[i].get("id") if i < len(exported_variants) else "")
                    or item.get("role") or campaigns[i].get("name") or "variant",
                    item["run_id"],
                )
                for i, item in enumerate(source["variants"])
            ]
            description = publication_body(
                config.public_url, benchmarks, variants, source.get("report_id")
            )
            if source.get("track_slug") and not any(
                f.get("kind") == "report" and f["path"].endswith(".html")
                for f in exported.get("files", [])
            ):
                raise HTTPException(409, "导出缺少报告 HTML，请确认报告已生成")
        except (publishing.PublishingError, httpx.HTTPError) as exc:
            message = (
                str(exc) if isinstance(exc, publishing.PublishingError) else "GitHub 暂时不可用"
            )
            raise HTTPException(409, message) from None
        finally:
            github.close()
    frozen = source | {
        "connection_url": config.upstream_url.rstrip("/"),
        "export_warnings": exported.get("warnings", []),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "base": snapshot["sha"],
                "repository": snapshot["repository"],
                "github_id": token.github_id,
                "connection_url": frozen["connection_url"],
                "files": files,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    while True:
        query = select(CatalogPublication).where(
            CatalogPublication.user_id == browser.user_id,
            CatalogPublication.fingerprint == fingerprint,
        )
        row = db.scalar(query)
        if row is None or row.status != "needs_review":
            break
        # A conflicted branch needs a fresh, independently reviewed attempt.
        # Derive its identity from the old task so concurrent/repeated previews
        # still converge, without changing the old snapshot or branch.
        fingerprint = hashlib.sha256(f"{fingerprint}:{row.id}".encode()).hexdigest()
    if row:
        return result(row)
    ident = str(uuid.uuid4())
    row = CatalogPublication(
        id=ident,
        user_id=browser.user_id,
        github_id=token.github_id,
        fingerprint=fingerprint,
        status="preview" if files else "unchanged",
        snapshot=snapshot,
        files=files,
        request=frozen,
        title=publication_title(name, files),
        body=description,
        branch=f"baseline-hub/catalog/{slug(name)[:30]}-{ident}",
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(query)
        if row is None:
            raise
    return result(row)


@router.post("/publications/{ident}/submit", status_code=202)
def submit(
    ident: str,
    request: Request,
    browser=Depends(current_session),
    db=Depends(db_session),
):
    row = owned_publication(db, ident, db.get(User, browser.user_id))
    token = credential(db, browser.user_id)
    config = request.app.state.settings
    if token.github_id != row.github_id:
        raise HTTPException(409, "请授权预览时绑定的 GitHub 账号")
    if row.request.get("connection_url") != config.upstream_url.rstrip("/"):
        raise HTTPException(409, "AutoTune 实例已变化，请重新预览")
    if row.status in {"preview", "failed", "needs_authorization"}:
        with authorized(request, browser, db) as http:
            check_sources(http, row.request, identity(db, browser.user_id, config))
            check_comparison(http, row.request, require_report=True)
        # A second submit may finish its upstream access check after the worker
        # has already advanced the task. Never overwrite that newer state.
        db.execute(
            update(CatalogPublication)
            .where(
                CatalogPublication.id == row.id,
                CatalogPublication.status.in_(["preview", "failed", "needs_authorization"]),
            )
            .values(status="queued", error="", attempts=0, next_attempt_at=None)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.refresh(row)
    return result(row)


@router.get("/publications")
def publications(browser=Depends(current_session), db=Depends(db_session)):
    return [
        dict(row)
        for row in db.execute(
            publication_summary_query()
            .where(CatalogPublication.user_id == browser.user_id)
            .order_by(CatalogPublication.created_at.desc(), CatalogPublication.id.desc())
            .limit(100)
        ).mappings()
    ]


@router.get("/publications/{ident}/status")
def publication_status(ident: str, browser=Depends(current_session), db=Depends(db_session)):
    row = db.execute(
        publication_status_query().where(
            CatalogPublication.id == ident,
            CatalogPublication.user_id == browser.user_id,
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Publication not found")
    return dict(row)


@router.get("/publications/{ident}")
def publication(ident: str, browser=Depends(current_session), db=Depends(db_session)):
    row = owned_publication(db, ident, db.get(User, browser.user_id))
    if "repository" not in row.snapshot:
        raise HTTPException(404, "Publication not found")
    return result(row)
