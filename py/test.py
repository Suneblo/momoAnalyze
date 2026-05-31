#!/usr/bin/env python3
"""直接调用本地后端接口，查看某一天 dashboard-data-page 构建结果。"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def get_json(base_url: str, path: str, params: dict[str, Any] | None = None, timeout: float = 60.0) -> dict[str, Any]:
    params = dict(params or {})
    params["_debug_t"] = int(time.time() * 1000)

    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    print(f"\nGET {url}")
    started = time.perf_counter()

    with urllib.request.urlopen(url, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")

    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"HTTP 完成: {elapsed_ms:.1f}ms, size={len(raw.encode('utf-8')) / 1024:.1f}KB")

    return json.loads(raw)


def short_dict(data: dict[str, Any] | None, keys: list[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    return {key: data.get(key) for key in keys if key in data}


def print_trace(day_payload: dict[str, Any]) -> None:
    trace = day_payload.get("trace") or {}
    for group_name, items in trace.items():
        print(f"\ntrace.{group_name}:")
        if not items:
            print("  空")
            continue

        for item in items:
            step = item.get("step")
            ms = item.get("ms")
            extra = {
                k: v
                for k, v in item.items()
                if k not in ("step", "ms")
            }
            print(f"  - {step}: {ms}ms {extra}")


def inspect_day(base_url: str, target_date: str, output_dir: Path) -> None:
    days_payload = get_json(base_url, "/api/days")
    days = days_payload.get("days") or []

    if target_date not in days:
        print(f"\n找不到日期: {target_date}")
        print(f"当前数据库日期范围: {days[0] if days else '-'} ~ {days[-1] if days else '-'}")
        return

    offset = days.index(target_date)
    print(f"\n目标日期 {target_date} 在 /api/days 中的位置 offset={offset}")

    page_payload = get_json(
        base_url,
        "/api/dashboard-data-page",
        {
            "offset": offset,
            "limit": 1,
            "includeMeta": 0,
            "order": "asc",
        },
        timeout=120.0,
    )

    out_file = output_dir / f"debug_dashboard_{target_date}.json"
    out_file.write_text(json.dumps(page_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整返回已保存: {out_file}")

    days_data = page_payload.get("days") or []
    if not days_data:
        print("返回里没有 days 数据")
        return

    day_payload = days_data[0]

    print("\n========== page 级信息 ==========")
    print(json.dumps(
        short_dict(page_payload, [
            "success", "source", "schemaVersion", "studyDayBoundaryHour",
            "offset", "limit", "order", "total", "loaded", "nextOffset", "hasMore", "timings",
        ]),
        ensure_ascii=False,
        indent=2,
    ))

    print("\n========== day 级基础信息 ==========")
    print(json.dumps(
        short_dict(day_payload, [
            "date", "overviewUpdateTime", "rowCount", "todayItemCount", "timings",
        ]),
        ensure_ascii=False,
        indent=2,
    ))

    print_trace(day_payload)

    print("\n========== latestProgress 摘要 ==========")
    latest_progress = day_payload.get("latestProgress") or {}
    print(json.dumps(
        short_dict(latest_progress, [
            "capturedAt",
            "finished",
            "total",
            "unfinished",
            "studyTimeMs",
            "firstForgetDone",
            "firstForgetAll",
            "vagueDone",
            "vagueAll",
            "familiarDone",
            "familiarAll",
            "newWords",
            "reviewWords",
            "pendingNewWords",
            "allItemsCount",
            "doneItemsCount",
            "todoItemsCount",
        ]),
        ensure_ascii=False,
        indent=2,
    ))

    print("\n========== summary 顶层字段 ==========")
    summary = day_payload.get("summary") or {}
    print("summary keys:")
    print(", ".join(sorted(summary.keys())))

    print("\nsummary 常用字段:")
    print(json.dumps(
        short_dict(summary, [
            "totalWords",
            "knownState",
            "vagueState",
            "forgetState",
            "overdue",
            "wellKnown",
            "sticking",
            "avgStudyCount",
            "finished",
            "total",
            "unfinished",
            "todayKnownCount",
            "todayVagueCount",
            "todayForgetCount",
            "dailyReviewedCount",
            "dailyPendingReviewCount",
            "dailyAllReviewCount",
            "dailyNewLearnedCount",
            "dailyPendingNewCount",
            "dailyAllNewCount",
        ]),
        ensure_ascii=False,
        indent=2,
    ))

    print("\n========== studyStatus 摘要 ==========")
    study_status = day_payload.get("studyStatus") or {}
    print("today:")
    print(json.dumps(study_status.get("today") or {}, ensure_ascii=False, indent=2))

    print("\noverall:")
    print(json.dumps(study_status.get("overall") or {}, ensure_ascii=False, indent=2))

    print("\ncritical:")
    print(json.dumps(study_status.get("critical") or {}, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="后端地址")
    parser.add_argument("--date", action="append", default=[], help="要查看的日期，可重复传，例如 --date 2026-05-11")
    parser.add_argument("--out", default="debug_api_output", help="输出 JSON 目录")
    args = parser.parse_args()

    output_dir = Path(args.out).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    target_dates = args.date or ["2026-05-11", "2026-05-12"]

    for date in target_dates:
        print("\n" + "=" * 80)
        print(f"开始检查日期: {date}")
        inspect_day(args.base, date, output_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())