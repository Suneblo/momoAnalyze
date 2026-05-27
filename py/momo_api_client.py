#!/usr/bin/env python3
"""Maimemo API helpers for syncing directly into SQLite.

This module is intentionally database-sync focused: it fetches API data and
builds in-memory statistics for direct SQLite writes.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STUDY_BASE = "https://open.maimemo.com/open/api/v1/study"
QUERY_STUDY_RECORDS_API = f"{STUDY_BASE}/query_study_records"
BJ = timezone(timedelta(hours=8))


def canonical_voc_id(obj: dict | None) -> str | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get("voc_id")
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def canonical_spelling(obj: dict | None) -> str | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get("voc_spelling")
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None

def read_token(token_file: Path) -> str:
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit(f"token 为空: {token_file}")
    return token


def _request_json(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network edge handling
            last_error = exc
            time.sleep(1 + attempt)

    raise RuntimeError(f"请求失败: {last_error}")


def post_json(token: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _request_json(f"{STUDY_BASE}/{endpoint}", token, payload or {})


def post_study_records_json(token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _request_json(QUERY_STUDY_RECORDS_API, token, payload)


def assert_success(name: str, data: dict[str, Any]) -> None:
    if not data.get("success", False):
        raise SystemExit(f"接口返回失败: {name}\n" + json.dumps(data, ensure_ascii=False, indent=2))


def iso(dt: datetime) -> str:
    return dt.astimezone(BJ).isoformat(timespec="seconds")


def record_key(item: dict[str, Any]) -> str:
    return canonical_voc_id(item) or f"{canonical_spelling(item) or ''}|{item.get('add_date','')}|{item.get('next_study_date','')}"


def count_range(token: str, start_dt: datetime, end_dt: datetime) -> int:
    resp = post_study_records_json(
        token,
        {
            "as_count": True,
            "next_study_date": {
                "start": iso(start_dt),
                "end": iso(end_dt),
            },
        },
    )
    if not resp.get("success", False):
        raise RuntimeError("范围计数失败: " + json.dumps(resp, ensure_ascii=False))
    return int(resp.get("data", {}).get("count") or 0)


def fetch_range(token: str, start_dt: datetime, end_dt: datetime, expected_count: int) -> list[dict[str, Any]]:
    if expected_count > 1000:
        raise RuntimeError(f"内部错误: 范围数量仍超过 1000: {expected_count}")

    resp = post_study_records_json(
        token,
        {
            "limit": 1000,
            "next_study_date": {
                "start": iso(start_dt),
                "end": iso(end_dt),
            },
        },
    )
    if not resp.get("success", False):
        raise RuntimeError("范围拉取失败: " + json.dumps(resp, ensure_ascii=False))

    records = resp.get("data", {}).get("records", []) or []
    if len(records) < expected_count:
        print(
            f"警告: 范围 {iso(start_dt)} ~ {iso(end_dt)} 计数={expected_count} 实取={len(records)}",
            file=sys.stderr,
        )
    return records


def fetch_all_records(token: str) -> tuple[list[dict[str, Any]], int, int]:
    count_resp = post_study_records_json(token, {"as_count": True})
    if not count_resp.get("success", False):
        raise SystemExit("获取总数失败: " + json.dumps(count_resp, ensure_ascii=False))

    expected_total = int(count_resp.get("data", {}).get("count") or 0)
    root_start = datetime(1970, 1, 1, 0, 0, 0, tzinfo=BJ)
    root_end = datetime(2100, 1, 1, 0, 0, 0, tzinfo=BJ)

    leaf_specs: list[tuple[datetime, datetime, int]] = []
    stack: list[tuple[datetime, datetime, int]] = [(root_start, root_end, expected_total)]

    while stack:
        start_dt, end_dt, cnt = stack.pop()
        if cnt == 0:
            continue
        if cnt <= 1000:
            leaf_specs.append((start_dt, end_dt, cnt))
            continue

        span = end_dt - start_dt
        if span.total_seconds() <= 1:
            raise RuntimeError(f"无法继续拆分，1秒内仍有 {cnt} 条: {iso(start_dt)} ~ {iso(end_dt)}")

        mid_dt = start_dt + span / 2
        with ThreadPoolExecutor(max_workers=2) as executor:
            left_future = executor.submit(count_range, token, start_dt, mid_dt)
            right_future = executor.submit(count_range, token, mid_dt, end_dt)
            left_count = left_future.result()
            right_count = right_future.result()

        stack.append((mid_dt, end_dt, right_count))
        stack.append((start_dt, mid_dt, left_count))

    records_map: dict[str, dict[str, Any]] = {}
    max_workers = max(1, min(6, len(leaf_specs)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_spec = {
            executor.submit(fetch_range, token, start_dt, end_dt, cnt): (start_dt, end_dt, cnt)
            for start_dt, end_dt, cnt in leaf_specs
        }
        for future in as_completed(future_to_spec):
            page = future.result()
            for item in page:
                records_map[record_key(item)] = item

    records = list(records_map.values())
    records.sort(key=lambda x: (x.get("next_study_date") or "", x.get("add_date") or "", x.get("voc_spelling") or ""))
    return records, expected_total, len(leaf_specs)


def item_match_keys(item: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    voc_id = canonical_voc_id(item)
    if voc_id:
        keys.add(("id", str(voc_id)))
    spelling = canonical_spelling(item)
    if spelling:
        keys.add(("spelling", str(spelling)))
    return keys


def record_match_keys(record: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    voc_id = canonical_voc_id(record)
    if voc_id:
        keys.add(("id", str(voc_id)))
    spelling = canonical_spelling(record)
    if spelling:
        keys.add(("spelling", str(spelling)))
    return keys


def build_study_count_lookup(records: list[dict[str, Any]]) -> dict[tuple[str, str], object]:
    lookup: dict[tuple[str, str], object] = {}
    for record in records:
        study_count = record.get("study_count", "")
        for key in record_match_keys(record):
            lookup[key] = study_count
    return lookup


def enrich_items_with_study_count(data: dict[str, Any], lookup: dict[tuple[str, str], object]) -> int:
    matched = 0
    items = data.get("data", {}).get("today_items", []) or []
    for item in items:
        study_count = ""
        for key in item_match_keys(item):
            if key in lookup:
                study_count = lookup[key]
                break
        item["study_count"] = study_count
        if study_count not in ("", None):
            matched += 1
    return matched


def today_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data.get("data", {}).get("today_items", []) or []


def count_first_response(data: dict[str, Any], response: str) -> int:
    return sum(1 for x in today_items(data) if x.get("first_response") == response)


def count_new_words(data: dict[str, Any]) -> int:
    return sum(1 for x in today_items(data) if x.get("is_new") is True)


def count_review_words(data: dict[str, Any]) -> int:
    return sum(1 for x in today_items(data) if x.get("is_new") is not True)


def count_pending_new_words(data: dict[str, Any]) -> int:
    return sum(1 for x in today_items(data) if x.get("is_new") is True and x.get("is_finished") is not True)
