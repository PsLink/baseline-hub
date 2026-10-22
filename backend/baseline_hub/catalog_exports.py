"""Export validated comparisons and filter artifacts for both HTTP and publication callers."""

import httpx
from fastapi import HTTPException

from baseline_hub.catalog_reports import check_comparison, upstream_source
from baseline_hub.export_files import export_archive, export_files
from baseline_hub.upstream import request_response


def preview_export(http, source, *, params=None):
    check_comparison(http, source)
    response = request_response(
        http, "POST", "catalog-export/preview", body=upstream_source(source), params=params
    )
    try:
        exported = response.json()
    except ValueError:
        raise HTTPException(502, "AutoTune 返回了无效响应") from None
    if not isinstance(exported, dict):
        raise HTTPException(502, "AutoTune 返回了无效响应")
    exported["files"] = export_files(exported.get("files", []))
    return httpx.Response(response.status_code, json=exported)


def archive_export(http, source, *, params=None):
    check_comparison(http, source, require_report=True)
    response = request_response(
        http, "POST", "catalog-export/archive", body=upstream_source(source), params=params
    )
    # HTTPX already decoded transport compression. Only artifact headers survive
    # rewriting; byte length must describe the newly filtered ZIP.
    headers = {
        name: response.headers[name]
        for name in ("content-type", "content-disposition") if name in response.headers
    }
    return httpx.Response(
        response.status_code, headers=headers, content=export_archive(response.content)
    )
