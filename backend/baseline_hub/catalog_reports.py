"""Validate a track export's exact comparison before freezing publication files."""

from fastapi import HTTPException

from baseline_hub.upstream import call


def check_comparison(http, source, *, require_report=False):
    track_slug = source.get("track_slug")
    if not track_slug:
        return
    track = call(http, "GET", f"baseline-hub/tracks/{track_slug}")
    variants = source.get("variants", [])
    bases = [v["run_id"] for v in variants if v.get("role") == "baseline"]
    opts = [v["run_id"] for v in variants if v.get("role") == "optimized"]
    if len(variants) != 2 or len(bases) != 1 or len(opts) != 1 or bases == opts:
        raise HTTPException(422, "请选择一个 baseline 和一个 opt")
    board = track.get("board", [])
    runs = {r.get("run_id") for r in board if r.get("run_id")}
    if not set(bases + opts) <= runs:
        raise HTTPException(422, "只能导出当前赛道的测量结果")
    reference = next((r for r in board if r.get("is_reference")), None)
    if reference and bases[0] != reference.get("run_id"):
        raise HTTPException(422, "必须使用管理员指定的 baseline，请重新选择")
    if track.get("reference") and reference is None:
        raise HTTPException(409, "管理员指定的 baseline 暂无可导出的运行")
    report_id = source.get("report_id")
    if not report_id:
        if require_report:
            raise HTTPException(409, "请等待对应的报告生成完成后再导出")
        return track
    report = call(http, "GET", f"agent/v1/reports/{report_id}")
    if report.get("baseline_run_id") != bases[0] or report.get("attempt_run_ids") != opts:
        raise HTTPException(422, "报告必须对应所选 baseline 和 opt")
    return track


def upstream_source(source):
    return {k: v for k, v in source.items() if k != "track_slug"}
