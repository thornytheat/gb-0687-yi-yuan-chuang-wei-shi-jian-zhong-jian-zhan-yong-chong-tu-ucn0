import csv
import io

from flask import Response, render_template, request

from app import holds, store
from app.config import BOARD, COLUMNS, DEFAULTS, MODULES, SAMPLES, TITLE
from app.pipeline import board, run


def view(stage):
    if not 1 <= stage <= 7:
        return "页面不存在", 404
    run_id = request.args.get("run")
    result = store.fetch(run_id) if run_id else run()
    sample = (SAMPLES / "dirty.csv").read_text(encoding="utf-8-sig")
    data = result["stages"][stage - 1]
    from datetime import datetime
    items = result["intervals"]
    start = min((datetime.fromisoformat(x["start"]).timestamp() for x in items), default=0)
    end = max((datetime.fromisoformat(x["end"]).timestamp() for x in items), default=1)
    tracks = []
    for bed in sorted({x["bed"] for x in items}):
        bars = []
        for x in items:
            if x["bed"] == bed:
                bars.append({**x, "left": (datetime.fromisoformat(x["start"]).timestamp() - start) / (end - start or 1) * 100, "width": (datetime.fromisoformat(x["end"]).timestamp() - datetime.fromisoformat(x["start"]).timestamp()) / (end - start or 1) * 100})
        tracks.append({"bed": bed, "bars": bars})
    return render_template(f"m{stage}.html", title=TITLE, modules=MODULES, stage=stage, result=result, data=data,
                           sample=sample, defaults=DEFAULTS, columns=COLUMNS, history=store.history(), tracks=tracks)


def _hold_state():
    return holds.active_view(), board(persist=False)


def view_holds():
    active, result = _hold_state()
    return render_template("m8.html", title=TITLE, modules=MODULES, stage=8, board_name=BOARD,
                           result=result, active=active, columns=COLUMNS, defaults=DEFAULTS,
                           sample=(SAMPLES / "dirty.csv").read_text(encoding="utf-8-sig"),
                           history=holds.history())


def _rows(result, active):
    for row in result["hold_board"]:
        yield ["批次封床", row["bed"], row["ward"], row["contending"], row["start"], row["end"],
               row["minutes"], row["los"], row["attribution"], row["responsible"],
               row["release_basis"], row["state"], result["manifest"]["run_id"]]
    for row in active.get("board") or []:
        yield ["生效封床", row["bed"], row["ward"], row["contending"], row["start"], row["end"],
               row["minutes"], row["los"], row["attribution"], row["responsible"],
               row["release_basis"], row["state"], active.get("id") or ""]


def holds_export():
    active, result = _hold_state()
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["来源", "床号", "病区", "争用患者", "开始 UTC", "结束 UTC", "封床 min",
                     "住院 min（患者:分钟）", "归属复核", "责任患者", "释放依据", "状态", "版本/批次"])
    for row in _rows(result, active):
        writer.writerow(row)
    writer.writerow([])
    writer.writerow(["容量日历", "日期", "病区", "床号", "容量 min", "占床 min", "封床 min",
                     "已释放 min", "可用 min", "可收治 min", "状态", "待裁决数", "已释放数"])
    for source, capacity in (("批次", result["board"]), ("生效", active.get("capacity") or [])):
        for row in capacity:
            writer.writerow([source, row["date"], row["ward"], row["bed"], row["capacity"], row["occupied"],
                             row["held"], row["released"], row["available"], row.get("bookable", 0),
                             row["state"], row["pending_holds"], row["released_holds"]])
    return Response("﻿" + stream.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=holds.csv"})
