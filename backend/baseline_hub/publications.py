"""Shared publication serialization and ownership checks."""

from urllib.parse import quote

import yaml
from fastapi import HTTPException
from sqlalchemy import select

from baseline_hub.models import CatalogPublication


def publication_status_query():
    """Project mutable status without loading frozen files, requests or snapshots."""
    # Legacy jobs lack this frozen repository and use another source contract.
    return select(
        CatalogPublication.id,
        CatalogPublication.status,
        CatalogPublication.pr_url,
        CatalogPublication.pr_number,
        CatalogPublication.pr_state,
        CatalogPublication.error,
        CatalogPublication.commit_sha,
    ).where(CatalogPublication.snapshot["repository"].as_string().is_not(None))


def publication_summary_query():
    return publication_status_query().add_columns(
        CatalogPublication.title,
        CatalogPublication.created_at,
        CatalogPublication.snapshot["repository"].as_string().label("repository"),
    )


def publication_title(name, files):
    products = []
    for file in files:
        if file.get("kind") != "variant":
            continue
        document = yaml.safe_load(file["text"])
        products.extend((document.get("requires") or {}).get("gpuProduct") or [])
    gpu = ", ".join(dict.fromkeys(products)) or "GPU"
    return f"baseline_hub: {name} on {gpu} measured variants"


def publication_body(public_url, benchmarks, variants, report_id=None):
    root = public_url.rstrip("/")
    sections = []
    if benchmarks:
        sections.append("\n".join(
            f"{name}: {root}/baseline-hub/tracks/{quote(slug, safe='')}"
            for name, slug in dict.fromkeys(benchmarks)
        ))
    sections.append("\n".join(f"Entry: {name} Run ID: {run_id}" for name, run_id in variants))
    if report_id:
        sections.append(f"Details: [Report]({root}/reports/{report_id})")
    return "\n\n".join(sections)


def output(job):
    return {
        k: getattr(job, k)
        for k in (
            "id",
            "status",
            "title",
            "body",
            "files",
            "pr_url",
            "pr_number",
            "pr_state",
            "error",
            "commit_sha",
            "created_at",
        )
    }


def owned_publication(db, ident, user):
    row = db.get(CatalogPublication, ident)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Publication not found")
    return row


