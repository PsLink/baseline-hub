"""Export operations keep comparison checks and artifact filtering together."""

import gzip
import json
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest
from fastapi import HTTPException

from baseline_hub.catalog_exports import archive_export, preview_export


@pytest.fixture
def exports():
    source = {
        "track_slug": "demo", "report_id": 4,
        "variants": [{"run_id": 1, "role": "baseline"}, {"run_id": 2, "role": "optimized"}],
    }
    files = {
        "models/demo/base.yaml": "id: base\n",
        "models/demo/base.validation.json": "{}",
        "models/demo/report.html": "<html>report</html>",
    }
    state = {"requests": [], "report_opt": 2, "invalid_json": False}

    def upstream(request):
        state["requests"].append(request)
        if request.url.path.endswith("/tracks/demo"):
            return httpx.Response(200, json={"board": [
                {"run_id": 1, "is_reference": True}, {"run_id": 2},
            ]})
        if request.url.path.endswith("/reports/4"):
            return httpx.Response(200, json={
                "baseline_run_id": 1, "attempt_run_ids": [state["report_opt"]],
            })
        if request.url.path.endswith("/preview"):
            if state["invalid_json"]:
                return httpx.Response(200, content=b"not-json")
            return httpx.Response(200, json={"valid": True, "files": [
                {"path": path, "text": text, "kind": "variant"} for path, text in files.items()
            ]})
        archive = BytesIO()
        with ZipFile(archive, "w") as output:
            for path, text in files.items():
                output.writestr(path, text)
        return httpx.Response(200, content=archive.getvalue(), headers={
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="demo.zip"',
        })

    with httpx.Client(
        base_url="http://autotune/api/", transport=httpx.MockTransport(upstream)
    ) as http:
        yield http, source, state


@pytest.mark.parametrize("operation", [preview_export, archive_export])
def test_export_validates_the_pair_and_report_before_contacting_the_exporter(exports, operation):
    http, source, state = exports
    state["report_opt"] = 3
    with pytest.raises(HTTPException) as error:
        operation(http, source)
    assert error.value.status_code == 422
    assert all(request.method == "GET" for request in state["requests"])


def test_preview_filters_files_and_keeps_source_and_query_parameters(exports):
    http, source, state = exports
    result = preview_export(http, source, params=[("lang", "zh"), ("lang", "en")])
    assert [file["path"] for file in result.json()["files"]] == [
        "models/demo/base.yaml", "models/demo/report.html",
    ]
    sent = state["requests"][-1]
    assert "track_slug" not in json.loads(sent.content)
    assert source["track_slug"] == "demo"
    assert sent.url.params.get_list("lang") == ["zh", "en"]


def test_archive_requires_a_report_and_retains_headers_and_binary_content(exports):
    http, source, state = exports
    without_report = source | {"report_id": None}
    preview_export(http, without_report)
    before = sum(request.method == "POST" for request in state["requests"])
    with pytest.raises(HTTPException) as error:
        archive_export(http, without_report)
    assert error.value.status_code == 409
    assert sum(request.method == "POST" for request in state["requests"]) == before
    result = archive_export(http, source)
    assert result.headers["content-type"] == "application/zip"
    assert result.headers["content-disposition"] == 'attachment; filename="demo.zip"'
    with ZipFile(BytesIO(result.content)) as archive:
        assert archive.namelist() == ["models/demo/base.yaml", "models/demo/report.html"]
        assert archive.read("models/demo/base.yaml") == b"id: base\n"


def test_invalid_preview_response_is_a_controlled_upstream_error(exports):
    http, source, state = exports
    state["invalid_json"] = True
    with pytest.raises(HTTPException) as error:
        preview_export(http, source)
    assert error.value.status_code == 502


@pytest.mark.parametrize("operation", [preview_export, archive_export])
def test_transformed_exports_do_not_reuse_upstream_compression_or_length_headers(operation):
    if operation is preview_export:
        payload = b'{"files": []}'
        mime = "application/json"
    else:
        archive = BytesIO()
        with ZipFile(archive, "w"):
            pass
        payload = archive.getvalue()
        mime = "application/zip"
    compressed = gzip.compress(payload)
    with httpx.Client(base_url="http://autotune/api/", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=compressed, headers={
            "content-type": mime, "content-encoding": "gzip",
            "content-length": str(len(compressed)),
        })
    )) as http:
        response = operation(http, {})
    assert "content-encoding" not in response.headers
    assert int(response.headers["content-length"]) == len(response.content)
    if operation is preview_export:
        assert response.json() == {"files": []}
    else:
        assert response.content == payload
