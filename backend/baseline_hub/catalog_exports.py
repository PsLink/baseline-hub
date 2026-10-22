"""Export validated comparisons and filter artifacts for both HTTP and publication callers."""

import re

import httpx
from fastapi import HTTPException

from baseline_hub.catalog_reports import check_comparison, upstream_source
from baseline_hub.export_files import export_archive, export_files
from baseline_hub.upstream import call, request_response


def check_sources(http, source, linked):
    """Authorize every run, allowing only the track's confirmed shared baseline."""
    if linked is None:
        raise HTTPException(409, "请连接 AutoTune 账号")
    variants = source.get("variants")
    if not isinstance(variants, list) or not 1 <= len(variants) <= 50 or any(
        not isinstance(item, dict)
        or type(item.get("run_id")) is not int or item["run_id"] <= 0
        for item in variants
    ):
        raise HTTPException(422, "请选择有效的导出运行")
    track_slug = source.get("track_slug")
    if track_slug and (
        not isinstance(track_slug, str)
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", track_slug)
    ):
        raise HTTPException(422, "赛道标识无效")
    owns = False
    campaigns = []
    track = None
    for item in variants:
        run = call(http, "GET", f"runs/{item['run_id']}")
        campaign = call(http, "GET", f"campaigns/{run['campaign_id']}")
        campaigns.append(campaign)
        if campaign.get("owner_id") == linked.upstream_user_id:
            owns = True
            continue
        if track_slug and item.get("role") == "baseline":
            if track is None:
                track = call(http, "GET", f"baseline-hub/tracks/{track_slug}")
            reference = next((row for row in track.get("board", [])
                              if row.get("is_reference")), None)
            if reference and reference.get("run_id") == item["run_id"]:
                continue
        raise HTTPException(403, "只能导出自己提交的结果，公共 baseline 仅限管理员指定的对照")
    if not owns:
        raise HTTPException(403, "请至少选择一项自己的测量结果")
    return campaigns


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
