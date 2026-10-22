"""Explicit ordinary-user routes; execution and track rules stay in AutoTune."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Request

from baseline_hub.sessions import current_session, db_session
from baseline_hub.submission_commands import Entry, create_submission
from baseline_hub.upstream import authorized, call

router = APIRouter(prefix="/platform", tags=["platform hub"])
Slug = Annotated[str, Path(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")]


@router.get("/tracks")
def tracks(request: Request, session=Depends(current_session), db=Depends(db_session)):
    with authorized(request, session, db) as http:
        return call(http, "GET", "baseline-hub/tracks")


@router.get("/tracks/{slug}")
def track(slug: Slug, request: Request, session=Depends(current_session), db=Depends(db_session)):
    with authorized(request, session, db) as http:
        return call(http, "GET", f"baseline-hub/tracks/{quote(slug, safe='')}")


@router.post("/tracks/{slug}/submissions", status_code=201)
def submit(
    slug: Slug,
    body: Entry,
    request: Request,
    session=Depends(current_session),
    db=Depends(db_session),
):
    with authorized(request, session, db) as http:
        return create_submission(http, slug, body)


@router.get("/submissions/{ident}")
def submission(
    ident: Annotated[int, Path(gt=0)],
    request: Request,
    session=Depends(current_session),
    db=Depends(db_session),
):
    with authorized(request, session, db) as http:
        return call(http, "GET", f"baseline-hub/submissions/{ident}")
