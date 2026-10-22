"""Allowlisted user API surface consumed by the original Hub components.

Always uses the current session's upstream credential. It is not an arbitrary
reverse proxy: admin endpoints, URLs and unsupported methods never reach upstream.
"""

import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from baseline_hub.auth import identity
from baseline_hub.catalog_exports import archive_export, check_sources, preview_export
from baseline_hub.sessions import current_session, db_session
from baseline_hub.submission_commands import (
    create_submission,
    stop_run,
    submission_action,
    validate_entry,
)
from baseline_hub.upstream import authorized, request_operation, request_response

router = APIRouter(prefix="/platform/api", tags=["Hub UI compatibility"])
SLUG = r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}"
READ = (
    rf"baseline-hub/tracks(?:/{SLUG}(?:/benchmark(?:/yaml)?)?)?",
    r"baseline-hub/submissions(?:/[1-9][0-9]*)?",
    r"baseline-hub/(?:flag-catalog|track-templates|track-defaults|leaderboard)",
    r"machines/gpu-types",
    r"campaigns/[1-9][0-9]*/runs",
    r"runs/[1-9][0-9]*(?:/log)?",
    r"auth/me(?:/contest-token)?",
    rf"agent/v1/tracks(?:/{SLUG})?",
    r"agent/v1/reports(?:/[1-9][0-9]*(?:/(?:export\.html|bundle\.zip|assets/[\w.-]+))?)?",
    r"agent/v1/report-blocks",
    r"autoreport/jobs(?:/[1-9][0-9]*)?",
)
WRITE = {
    "POST": (
        r"baseline-hub/parse",
        rf"baseline-hub/tracks/{SLUG}/submissions",
        r"baseline-hub/submissions/[1-9][0-9]*/(?:abort|resubmit|preflight)",
        r"runs/[1-9][0-9]*/stop",
        r"catalog-export/(?:preview|archive)",
        r"autoreport/(?:jobs|preview|jobs/[1-9][0-9]*/retry)",
    ),
    "PUT": (
        r"auth/me/(?:contributor|contest-token)",
        r"catalog-export/env-categories/[A-Za-z_][A-Za-z0-9_]{0,127}",
    ),
    "DELETE": (r"auth/me/contest-token",),
}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def forward(
    path: str,
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    session=Depends(current_session),
    db=Depends(db_session),
):
    method = request.method
    patterns = READ if method == "GET" else WRITE.get(method, ())
    if any(s in {".", ".."} for s in path.split("/")) or not any(
        re.fullmatch(pattern, path) for pattern in patterns
    ):
        raise HTTPException(404, "此接口不在 Hub 用户功能范围内")
    params = list(request.query_params.multi_items())
    with authorized(request, session, db) as http:
        create = re.fullmatch(rf"baseline-hub/tracks/({SLUG})/submissions", path)
        if method == "POST" and create:
            return JSONResponse(
                create_submission(http, create[1], validate_entry(body)), status_code=201
            )
        linked = identity(db, session.user_id, request.app.state.settings)
        if linked is None:
            raise HTTPException(403, "此会话没有关联的 AutoTune 身份")
        action = re.fullmatch(
            r"baseline-hub/submissions/([1-9][0-9]*)/(abort|resubmit|preflight)", path
        )
        stop = re.fullmatch(r"runs/([1-9][0-9]*)/stop", path)
        if method == "POST" and action:
            response = submission_action(
                http, int(action[1]), action[2], body, linked, params=params
            )
        elif method == "POST" and stop:
            response = stop_run(http, int(stop[1]), linked, body=body, params=params)
        elif method == "POST" and path == "catalog-export/preview":
            check_sources(http, body or {}, linked)
            response = preview_export(http, body or {}, params=params)
        elif method == "POST" and path == "catalog-export/archive":
            check_sources(http, body or {}, linked)
            response = archive_export(http, body or {}, params=params)
        elif method == "POST" and re.fullmatch(r"autoreport/jobs(?:/[1-9][0-9]*/retry)?", path):
            response = request_operation(http, path, body=body, params=params)
        else:
            response = request_response(http, method, path, body=body, params=params)
        content = response.content
        headers = {"X-Content-Type-Options": "nosniff"}
        for name in ("content-type", "content-disposition"):
            if name in response.headers:
                headers[name] = response.headers[name]
        if re.fullmatch(r"agent/v1/reports/[1-9][0-9]*/assets/[\w.-]+", path):
            # Reports accept user supplied assets upstream. Never place executable
            # HTML/SVG on the Hub origin, including through renderer blob URLs.
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            content = response.content
            signatures = {
                "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
                "image/jpeg": content.startswith(b"\xff\xd8\xff"),
                "image/gif": content.startswith((b"GIF87a", b"GIF89a")),
                "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
            }
            if not signatures.get(mime, False):
                raise HTTPException(415, "报告附件仅支持 PNG、JPEG、GIF、WebP 图片")
            headers["content-type"] = mime
            headers["content-disposition"] = "inline"
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
        if path.endswith(("/export.html", "/bundle.zip")):
            headers["content-disposition"] = "attachment"
        return Response(content, status_code=response.status_code, headers=headers)
