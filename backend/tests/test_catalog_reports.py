"""Track export pairing is enforced independently of browser controls."""

import httpx
import pytest
from fastapi import HTTPException

from baseline_hub.catalog_reports import check_comparison


@pytest.fixture
def comparison():
    state = {
        "board": [{"run_id": 1, "is_reference": True}, {"run_id": 2}, {"run_id": 3}],
        "report": {"baseline_run_id": 1, "attempt_run_ids": [2]},
    }

    def remote(request):
        if "/tracks/" in request.url.path:
            return httpx.Response(200, json={"board": state["board"]})
        return httpx.Response(200, json=state["report"])

    with httpx.Client(
        base_url="http://autotune/api/", transport=httpx.MockTransport(remote)
    ) as http:
        yield http, state


def source(base=1, opts=(2,), report=4):
    return {
        "track_slug": "demo",
        "variants": [{"run_id": base, "role": "baseline"}]
        + [{"run_id": opt, "role": "optimized"} for opt in opts],
        "report_id": report,
    }


def test_fixed_baseline_and_exact_report(comparison):
    http, state = comparison
    check_comparison(http, source(), require_report=True)
    invalid_sources = (source(base=3), source(opts=(2, 3)), source(opts=(1,)), source(opts=(9,)))
    for invalid in invalid_sources:
        with pytest.raises(HTTPException) as exc:
            check_comparison(http, invalid, require_report=True)
        assert exc.value.status_code == 422
    state["report"]["attempt_run_ids"] = [3]
    with pytest.raises(HTTPException) as exc:
        check_comparison(http, source(), require_report=True)
    assert exc.value.status_code == 422


def test_no_reference_allows_chosen_pair_but_export_waits_for_report(comparison):
    http, state = comparison
    state["board"][0]["is_reference"] = False
    state["report"]["baseline_run_id"] = 3
    check_comparison(http, source(base=3), require_report=True)
    check_comparison(http, source(base=3, report=None))
    with pytest.raises(HTTPException) as exc:
        check_comparison(http, source(base=3, report=None), require_report=True)
    assert exc.value.status_code == 409
