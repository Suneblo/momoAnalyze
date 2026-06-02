#!/usr/bin/env python3
"""Shared helpers for momo_db compact storage.

This module contains only reusable helpers and value transformations.
It does not own database query endpoints or legacy schema compatibility.
"""

from __future__ import annotations

import json
import sqlite3
import time
import sys
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Iterable

BJ = timezone(timedelta(hours=8))
SCHEMA_VERSION = 28
STUDY_DAY_BOUNDARY_HOUR = 4
_RECONSTRUCT_CACHE_MAX = 24
_RECONSTRUCT_CACHE: OrderedDict[tuple[str, str, str], list[dict[str, Any]]] = OrderedDict()

RESPONSE_CN = {
    "FAMILIAR": "认识",
    "VAGUE": "模糊",
    "FORGET": "忘记",
    "WELL_FAMILIAR": "熟知",
    "CANCEL_WELL_FAMILIAR": "取消熟知",
    "STUDY_RESPONSE_UNSPECIFIED": "未作答",
    None: "",
    "": "",
}

FALLBACK_MEMORY_THRESHOLDS = "1,2,3,4,5,6,7,15,30"


def _format_threshold_number(value: float) -> str:
    if value == float("inf") or value == float("-inf"):
        return ""
    return str(int(value)) if float(value).is_integer() else str(value).rstrip("0").rstrip(".")


def _make_threshold_spec(spec_id: str, lower: float, lower_inclusive: bool, upper: float, upper_inclusive: bool) -> dict[str, Any]:
    return {
        "id": spec_id,
        "lower": lower,
        "lowerInclusive": lower_inclusive,
        "upper": upper,
        "upperInclusive": upper_inclusive,
    }


def parse_memory_threshold_specs_from_text(text: str | None) -> list[dict[str, Any]]:
    raw_parts = [
        part.strip()
        for part in str(text or "").replace("，", ",").replace("；", ",").replace(";", ",").split(",")
        if part.strip()
    ]
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    import re

    for raw in raw_parts:
        normalized = raw.replace("～", "-").replace("—", "-").replace("–", "-")
        normalized = "".join(normalized.split())
        spec: dict[str, Any] | None = None

        cmp = re.match(r"^(<=|>=|<|>)(-?\d+(?:\.\d+)?)$", normalized)
        if cmp:
            op = cmp.group(1)
            value = float(cmp.group(2))
            label_value = _format_threshold_number(value)
            if op == ">":
                spec = _make_threshold_spec(f"gt:{label_value}", value, False, float("inf"), True)
            elif op == ">=":
                spec = _make_threshold_spec(f"gte:{label_value}", value, True, float("inf"), True)
            elif op == "<":
                spec = _make_threshold_spec(f"lt:{label_value}", float("-inf"), True, value, False)
            elif op == "<=":
                spec = _make_threshold_spec(f"lte:{label_value}", float("-inf"), True, value, True)

        if spec is None:
            rng = re.match(r"^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$", normalized)
            if rng:
                start = float(rng.group(1))
                end = float(rng.group(2))
                lower = min(start, end)
                upper = max(start, end)
                spec = _make_threshold_spec(
                    f"range:{_format_threshold_number(lower)}-{_format_threshold_number(upper)}",
                    lower,
                    True,
                    upper,
                    True,
                )

        if spec is None:
            try:
                value = float(normalized)
            except Exception:
                continue
            spec = _make_threshold_spec(f"exact:{_format_threshold_number(value)}", value, True, value, True)

        if spec["id"] in seen:
            continue
        seen.add(spec["id"])
        specs.append(spec)
    return specs


def _threshold_value_matches(spec: dict[str, Any], value: float) -> bool:
    lower = spec.get("lower", float("-inf"))
    upper = spec.get("upper", float("inf"))
    lower_ok = value >= lower if spec.get("lowerInclusive", True) else value > lower
    upper_ok = value <= upper if spec.get("upperInclusive", True) else value < upper
    return lower_ok and upper_ok


def _memory_threshold_matches(spec: dict[str, Any], days: Any, mode: str) -> bool:
    try:
        raw_value = float(days)
    except Exception:
        return False
    if mode == "review_span":
        return raw_value >= 1 and _threshold_value_matches(spec, raw_value)
    if mode == "critical_point":
        return raw_value >= 0 and _threshold_value_matches(spec, raw_value)
    if mode == "overdue":
        if raw_value >= 0:
            return False
        upper = spec.get("upper", float("inf"))
        if upper != float("inf") and upper <= 0:
            return _threshold_value_matches(spec, raw_value)
        overdue_age = max(1, abs(int(raw_value // 1)))
        return _threshold_value_matches(spec, float(overdue_age))
    return _threshold_value_matches(spec, raw_value)


def _new_memory_stats(specs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        "counts": {str(spec["id"]): 0 for spec in specs},
        "avgs": {str(spec["id"]): None for spec in specs},
        "_sums": {str(spec["id"]): 0.0 for spec in specs},
        "_avgCounts": {str(spec["id"]): 0 for spec in specs},
    }


def _add_memory_stat(stats: dict[str, Any], specs: list[dict[str, Any]], days: Any, study_count: float | None, mode: str) -> None:
    for spec in specs:
        spec_id = str(spec["id"])
        if not _memory_threshold_matches(spec, days, mode):
            continue
        stats["counts"][spec_id] = int(stats["counts"].get(spec_id) or 0) + 1
        if study_count is not None and study_count == study_count:
            stats["_sums"][spec_id] = float(stats["_sums"].get(spec_id) or 0.0) + float(study_count)
            stats["_avgCounts"][spec_id] = int(stats["_avgCounts"].get(spec_id) or 0) + 1


def _finalize_memory_stats(stats: dict[str, Any]) -> dict[str, Any]:
    sums = stats.pop("_sums", {})
    counts = stats.pop("_avgCounts", {})
    for spec_id, total in sums.items():
        n = int(counts.get(spec_id) or 0)
        stats["avgs"][spec_id] = (float(total) / n) if n else None
    return stats


def _cache_get(kind: str, day_key: str, snapshot_time: str) -> list[dict[str, Any]] | None:
    """从重建缓存中读取指定类型、日期和快照时间的数据。"""
    key = (kind, day_key, snapshot_time)
    value = _RECONSTRUCT_CACHE.get(key)
    if value is None:
        return None
    _RECONSTRUCT_CACHE.move_to_end(key)
    # 返回浅拷贝，避免调用方误改缓存中的字典列表结构。
    return [dict(x) for x in value]


def _cache_put(kind: str, day_key: str, snapshot_time: str, value: list[dict[str, Any]]) -> None:
    """写入重建缓存，并按 LRU 规则限制缓存容量。"""
    key = (kind, day_key, snapshot_time)
    _RECONSTRUCT_CACHE[key] = [dict(x) for x in value]
    _RECONSTRUCT_CACHE.move_to_end(key)
    while len(_RECONSTRUCT_CACHE) > _RECONSTRUCT_CACHE_MAX:
        _RECONSTRUCT_CACHE.popitem(last=False)


def db_perf_log(message: str) -> None:
    """向 stderr 输出带北京时间的数据库性能日志。"""
    ts = now_bj().strftime("%H:%M:%S")
    print(f"[{ts}] DB {message}", file=sys.stderr, flush=True)


def _trace_add(trace: list[dict[str, Any]] | None, step: str, elapsed_ms: float, **extra: Any) -> None:
    """向 trace 列表追加一个步骤耗时记录；trace 为空时静默跳过。"""
    if trace is None:
        return
    item: dict[str, Any] = {"step": step, "ms": round(float(elapsed_ms), 2)}
    for key, value in extra.items():
        if value is not None:
            item[key] = value
    trace.append(item)


def _trace_step_start() -> float:
    """返回当前高精度计时点，用于后续计算步骤耗时。"""
    return time.perf_counter()


def _trace_step_done(trace: list[dict[str, Any]] | None, step: str, start: float, **extra: Any) -> None:
    """根据开始时间计算耗时，并追加到 trace 记录。"""
    _trace_add(trace, step, (time.perf_counter() - start) * 1000, **extra)


def connect(db_path: Path) -> sqlite3.Connection:
    """创建 SQLite 连接，并统一设置 row_factory 与性能相关 PRAGMA。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


@contextmanager
def db_connect(db_path: Path):
    """数据库连接上下文管理器：退出时提交事务并关闭连接。"""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_bj() -> datetime:
    """返回当前北京时间。"""
    return datetime.now(BJ)


def iso_bj(dt: datetime | None = None, *, milliseconds: bool = True) -> str:
    """把给定时间转换为北京时间 ISO 字符串，默认保留毫秒。"""
    dt = (dt or now_bj()).astimezone(BJ)
    return dt.isoformat(timespec="milliseconds" if milliseconds else "seconds")


def snapshot_name(dt: datetime | None = None) -> str:
    """生成用于快照文件名或标识的北京时间字符串。"""
    return (dt or now_bj()).astimezone(BJ).strftime("%Y.%m.%d_%H-%M-%S")


def study_day_key_from_dt(dt: datetime | None = None) -> str:
    """根据学习日边界小时计算学习日 key；凌晨边界前归入前一天。"""
    dt = (dt or now_bj()).astimezone(BJ)
    if dt.hour < STUDY_DAY_BOUNDARY_HOUR:
        dt = dt - timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def month_key_from_day(day_key: str) -> str:
    """从 YYYY-MM-DD 日期 key 截取 YYYY-MM 月份 key。"""
    return str(day_key)[:7]


def parse_api_datetime(value: str | None) -> datetime | None:
    """解析 API 时间字符串，并统一转换为北京时间；解析失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(BJ)
    except Exception:
        return None


def json_text(value: Any) -> str:
    """用紧凑 JSON 格式序列化对象，并保留中文字符。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    """基于稳定 JSON 文本生成 SHA1 哈希。"""
    return sha1(json_text(value).encode("utf-8")).hexdigest()


def response_cn(value: str | None) -> str:
    """把 API 反馈枚举转换为中文文案；未知值原样返回。"""
    return RESPONSE_CN.get(value, value or "")


#-----------------
#转换数据类型工具
#—————————————————
def nullable_bool_int(value: Any) -> int | None:
    """把常见布尔值表示转换为 1/0；无法判断时返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    s = str(value).strip().lower()
    if s in {"true", "1", "yes", "y", "是"}:
        return 1
    if s in {"false", "0", "no", "n", "否"}:
        return 0
    return None


def nullable_int(value: Any) -> int | None:
    """把输入转换为整数；空值或转换失败返回 None。"""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return None


def nullable_float(value: Any) -> float | None:
    """把输入转换为浮点数；空值或转换失败返回 None。"""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None
#-----------------
#转换数据类型工具
#—————————————————


def text_or_none(value: Any) -> str | None:
    """把非空值转换为字符串；None 或空字符串返回 None。"""
    if value is None:
        return None
    s = str(value)
    return s if s != "" else None


def tag_values(tags: Any) -> set[str]:
    """把标签列表或分隔字符串规范化为标签集合。"""
    if tags is None:
        return set()
    if isinstance(tags, list):
        return {str(x) for x in tags if x}
    text = str(tags).strip()
    if not text:
        return set()
    return {x.strip() for x in text.replace(";", ",").split(",") if x.strip()}


def tag_summary(tags: Any) -> tuple[str, str, str]:
    """汇总标签信息，返回是否熟知、是否顽固和展示文本。"""
    vals = tag_values(tags)
    labels: list[str] = []
    is_well = "WELL_FAMILIAR" in vals
    is_sticking = "STICKING" in vals
    if is_well:
        labels.append("熟知")
    if is_sticking:
        labels.append("顽固")
    for raw in sorted(vals - {"WELL_FAMILIAR", "STICKING"}):
        labels.append(raw)
    return ("是" if is_well else "否", "是" if is_sticking else "否", ";".join(labels))


def current_state(last_response: str | None, next_study_date: str | None, reference_day: str) -> tuple[str, str]:
    """根据最近反馈、下次复习日期和参考日计算当前状态与是否逾期。"""
    next_dt = parse_api_datetime(next_study_date)
    try:
        ref_date = datetime.strptime(reference_day, "%Y-%m-%d").date()
    except Exception:
        ref_date = now_bj().date()
    if next_dt and next_dt.date() < ref_date:
        return "逾期", "是"
    if last_response == "FAMILIAR":
        return "认识", "否"
    if last_response == "VAGUE":
        return "模糊", "否"
    if last_response == "FORGET":
        return "忘记", "否"
    if last_response == "STUDY_RESPONSE_UNSPECIFIED":
        return "未作答", "否"
    if last_response == "CANCEL_WELL_FAMILIAR":
        return "取消熟知", "否"
    return "", "否"


def response_bucket(first_response: Any) -> str:
    """把首次作答枚举归类为统计桶。"""
    raw = str(first_response or "").strip().upper()
    if raw == "FORGET":
        return "forget"
    if raw == "VAGUE":
        return "vague"
    if raw == "FAMILIAR":
        return "known"
    return "unknown"


def response_counts_empty() -> dict[str, int]:
    """返回一个初始化为 0 的作答统计字典。"""
    return {"known": 0, "vague": 0, "forget": 0, "unknown": 0}


def extract_progress(progress_payload: dict[str, Any] | None) -> dict[str, Any]:
    """从 API progress payload 中提取 progress 节点。"""
    return (((progress_payload or {}).get("data") or {}).get("progress") or {})


def extract_today_items(items_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """从 API today_items payload 中提取有效的今日单词对象列表。"""
    items = (((items_payload or {}).get("data") or {}).get("today_items") or [])
    return [x for x in items if isinstance(x, dict)]


def _stable_text_key(value: Any) -> str:
    """生成用于匹配的稳定小写文本 key。"""
    return str(value or "").strip().casefold()


def canonical_voc_id(obj: dict[str, Any] | None) -> str | None:
    """从对象中提取规范化后的 voc_id。"""
    if not isinstance(obj, dict):
        return None
    value = obj.get("voc_id")
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def canonical_spelling(obj: dict[str, Any] | None) -> str | None:
    """从对象中提取规范化后的单词拼写。"""
    if not isinstance(obj, dict):
        return None
    value = obj.get("voc_spelling")
    if value in (None, ""):
        value = obj.get("spelling")
    if value in (None, ""):
        value = obj.get("word")
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def canonical_word_key(obj: dict[str, Any] | None) -> str | None:
    """本地分析使用 spelling 作为单词键；空 spelling 时仅用 voc_id 兜底。"""
    if isinstance(obj, dict):
        explicit = obj.get("word_key")
        if explicit not in (None, ""):
            text = str(explicit).strip()
            if text:
                return text

    spelling = canonical_spelling(obj)
    if spelling:
        return spelling
    voc_id = canonical_voc_id(obj)
    return f"voc:{voc_id}" if voc_id else None


def _json_obj(text: Any) -> dict[str, Any]:
    """把 JSON 文本安全解析为 dict；失败或非 dict 时返回空字典。"""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        obj = json.loads(text)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def item_key(item: dict[str, Any]) -> str:
    """生成今日 item 的稳定身份 key，使用本地 word_key。"""
    return canonical_word_key(item) or ""


def record_key(record: dict[str, Any]) -> str:
    """生成 overview record 的稳定身份 key，使用本地 word_key。"""
    return canonical_word_key(record) or ""


def item_payload(item: dict[str, Any], *, present_state: str = "observed") -> dict[str, Any]:
    """把 API 今日 item 转换为数据库存储用 payload。"""
    return {
        "item_key": item_key(item),
        "word_key": canonical_word_key(item),
        "voc_id": text_or_none(canonical_voc_id(item)),
        "voc_spelling": text_or_none(canonical_spelling(item)),
        "order_index": nullable_int(item.get("order")),
        "first_response": text_or_none(item.get("first_response")),
        "is_new": nullable_bool_int(item.get("is_new")),
        "is_finished": nullable_bool_int(item.get("is_finished")),
        "present_state": present_state,
        "raw_json": json_text(item),
    }


def item_state_hash(payload: dict[str, Any]) -> str:
    """根据 item 关键状态字段生成哈希，用于判断状态是否变化。"""
    return stable_hash({
        "word_key": payload.get("word_key"),
        "voc_id": payload.get("voc_id"),
        "voc_spelling": payload.get("voc_spelling"),
        "order_index": payload.get("order_index"),
        "first_response": payload.get("first_response"),
        "is_new": payload.get("is_new"),
        "is_finished": payload.get("is_finished"),
        "present_state": payload.get("present_state"),
    })


def record_payload(record: dict[str, Any], reference_day: str) -> dict[str, Any]:
    """把 API overview record 转换为数据库存储用 payload。"""
    tags = record.get("tags") or []
    tag_well, tag_sticking, tag_text = tag_summary(tags)
    last_response = text_or_none(record.get("last_response"))
    state, overdue = current_state(last_response, text_or_none(record.get("next_study_date")), reference_day)
    return {
        "record_key": record_key(record),
        "word_key": canonical_word_key(record),
        "voc_id": text_or_none(canonical_voc_id(record)),
        "spelling": text_or_none(canonical_spelling(record)),
        "add_date": text_or_none(record.get("add_date")),
        "first_study_date": text_or_none(record.get("first_study_date")),
        "last_study_date": text_or_none(record.get("last_study_date")),
        "next_study_date": text_or_none(record.get("next_study_date")),
        "last_response": last_response,
        "last_response_cn": response_cn(last_response),
        "study_count": nullable_float(record.get("study_count")),
        "tags_json": json_text(tags),
        "tags_text": tag_text,
        "tag_well": tag_well,
        "tag_sticking": tag_sticking,
        "current_state": state,
        "is_overdue": overdue,
        "raw_json": json_text(record),
    }


def record_state_hash(payload: dict[str, Any]) -> str:
    """根据 record 关键状态字段生成哈希，用于判断状态是否变化。"""
    return stable_hash({
        "word_key": payload.get("word_key"),
        "voc_id": payload.get("voc_id"),
        "spelling": payload.get("spelling"),
        "add_date": payload.get("add_date"),
        "first_study_date": payload.get("first_study_date"),
        "last_study_date": payload.get("last_study_date"),
        "next_study_date": payload.get("next_study_date"),
        "last_response": payload.get("last_response"),
        "study_count": payload.get("study_count"),
        "tags_json": payload.get("tags_json"),
    })


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """读取 SQLite 表的字段名集合。"""
    return {str(r["name"] if isinstance(r, sqlite3.Row) else r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def row_value(row: sqlite3.Row | dict[str, Any] | None, key: str, default: Any = None) -> Any:
    """兼容 sqlite3.Row 和 dict 的安全取值函数。"""
    if row is None:
        return default
    if isinstance(row, sqlite3.Row):
        return row[key] if key in row.keys() else default
    return row.get(key, default)


def count_response(items: Iterable[dict[str, Any]], response: str) -> int:
    """统计 item 列表中指定 first_response 的数量。"""
    return sum(1 for x in items if x.get("first_response") == response)


def count_new(items: Iterable[dict[str, Any]]) -> int:
    """统计新学 item 数量。"""
    return sum(1 for x in items if x.get("is_new") is True)


def count_review(items: Iterable[dict[str, Any]]) -> int:
    """统计复习 item 数量。"""
    return sum(1 for x in items if x.get("is_new") is not True)


def count_pending_new(items: Iterable[dict[str, Any]]) -> int:
    """统计未完成的新学 item 数量。"""
    return sum(1 for x in items if x.get("is_new") is True and x.get("is_finished") is not True)


def progress_today_breakdown(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    active = [x for x in items if x.get("present_state") != "confirmed_absent"]
    done = [x for x in active if x.get("is_finished") is True]
    todo = [x for x in active if x.get("is_finished") is not True]
    done_new = sum(1 for x in done if x.get("is_new") is True)
    todo_new = sum(1 for x in todo if x.get("is_new") is True)
    done_review = sum(1 for x in done if x.get("is_new") is not True)
    todo_review = sum(1 for x in todo if x.get("is_new") is not True)
    return {
        "todayKnownCount": count_response(done, "FAMILIAR"),
        "todayVagueCount": count_response(done, "VAGUE"),
        "todayForgetCount": count_response(done, "FORGET"),
        "todayAllKnownCount": count_response(active, "FAMILIAR"),
        "todayAllVagueCount": count_response(active, "VAGUE"),
        "todayAllForgetCount": count_response(active, "FORGET"),
        "dailyNewLearnedCount": done_new,
        "dailyPendingNewCount": todo_new,
        "dailyAllNewCount": done_new + todo_new,
        "dailyReviewedCount": done_review,
        "dailyPendingReviewCount": todo_review,
        "dailyAllReviewCount": done_review + todo_review,
        "unfinishedCount": len(todo),
    }


def build_latest_progress_payload_from_items(row: sqlite3.Row | dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the single canonical progress payload used by dashboard APIs.

    Historical code stored several overlapping counters in progress and
    the frontend also recalculated them from allItems.  This helper makes the
    API source of truth explicit: counters are derived once from the reconstructed
    study-day items, and the raw allItems array is not embedded in dashboard-day
    payloads.  The full item list is still available from /api/today-workspace
    and /api/words when needed.
    """
    breakdown = progress_today_breakdown(items)
    study_time = row_value(row, "study_time")
    snapshot = row_value(row, "snapshot_time")
    finished = breakdown["dailyReviewedCount"] + breakdown["dailyNewLearnedCount"]
    total = breakdown["dailyAllReviewCount"] + breakdown["dailyAllNewCount"]
    return {
        "snapshot": snapshot,
        "capturedAt": snapshot,
        "progress": {"finished": finished, "total": total, "study_time": study_time},
        "finished": finished,
        "total": total,
        "studyTimeMs": study_time,
        "todayFirstForgetCount": breakdown["todayForgetCount"],
        "todayKnownCount": breakdown["todayKnownCount"],
        "todayVagueCount": breakdown["todayVagueCount"],
        "todayAllFirstForgetCount": breakdown["todayAllForgetCount"],
        "todayAllKnownCount": breakdown["todayAllKnownCount"],
        "todayAllVagueCount": breakdown["todayAllVagueCount"],
        "dailyNewLearnedCount": breakdown["dailyNewLearnedCount"],
        "dailyPendingNewCount": breakdown["dailyPendingNewCount"],
        "dailyAllNewCount": breakdown["dailyAllNewCount"],
        "dailyReviewedCount": breakdown["dailyReviewedCount"],
        "dailyPendingReviewCount": breakdown["dailyPendingReviewCount"],
        "dailyAllReviewCount": breakdown["dailyAllReviewCount"],
        "unfinishedCount": breakdown["unfinishedCount"],
    }


def _study_status_overall_from_records(records: list[dict[str, Any]], day_key: str) -> dict[str, Any]:
    """Compute current long-term word-state counters directly from overview records.

    This is intentionally not named "overview" anymore.  It is the overall word
    state for the day and exists only as supporting data for the combined study
    status card and export.
    """
    total_study_count = 0.0
    total_study_count_n = 0
    out: dict[str, Any] = {
        "totalWords": len(records),
        "wellKnown": 0,
        "sticking": 0,
        "knownState": 0,
        "vagueState": 0,
        "forgetState": 0,
        "overdue": 0,
        "avgStudyCount": 0,
    }
    for record in records:
        tag_text = str(record.get("tags_text") or "")
        tags = [x.strip() for x in tag_text.replace("；", ";").replace("，", ",").replace(",", ";").split(";") if x.strip()]
        if record.get("tag_well") == "是" or "熟知" in tags:
            out["wellKnown"] += 1
        if record.get("tag_sticking") == "是" or "顽固" in tags:
            out["sticking"] += 1

        state = str(record.get("current_state") or "").strip()
        if not state and record.get("is_overdue") == "是":
            state = "逾期"
        if state == "认识":
            out["knownState"] += 1
        elif state == "模糊":
            out["vagueState"] += 1
        elif state == "忘记":
            out["forgetState"] += 1
        elif state == "逾期":
            out["overdue"] += 1

        try:
            study_count = float(record.get("study_count"))
            total_study_count += study_count
            total_study_count_n += 1
        except Exception:
            pass
    out["avgStudyCount"] = (total_study_count / total_study_count_n) if total_study_count_n else 0
    return out


def _study_status_critical_from_records(records: list[dict[str, Any]], day_key: str) -> dict[str, Any]:
    """Compute memory-critical due counts directly from overview records."""
    due_by_offset: dict[str, int] = {}
    due_today_count = 0
    for record in records:
        days = _date_diff_days(record.get("next_study_date"), f"{day_key}T12:00:00+08:00")
        if days is None:
            continue
        rounded = int(round(days))
        due_by_offset[str(rounded)] = due_by_offset.get(str(rounded), 0) + 1
        if days <= 0:
            due_today_count += 1
    return {
        "dueToday": due_today_count,
        "dueByOffset": due_by_offset,
    }


def _study_status_critical_from_today_items(today_items: list[dict[str, Any]], day_key: str) -> dict[str, Any] | None:
    """Compute time-point critical counts from the current study task list.

    For an unfinished review item, the item is still waiting at its previous due
    date. For a finished item, the current next study date is already the
    post-review due date. This keeps intra-day snapshots from all showing the
    final overview critical count.
    """
    due_by_offset: dict[str, int] = {}
    due_today_count = 0
    seen = 0

    for item in today_items:
        if item.get("present_state") == "confirmed_absent":
            continue
        if item.get("is_new") is True:
            continue

        if item.get("is_finished") is True:
            due_date = item.get("next_study_date") or item.get("nextStudyDate")
        else:
            due_date = (
                item.get("previous_next_study_date")
                or item.get("previousNextStudyDate")
                or item.get("next_study_date")
                or item.get("nextStudyDate")
            )

        days = _date_diff_days(due_date, f"{day_key}T12:00:00+08:00")
        if days is None:
            continue

        seen += 1
        rounded = int(round(days))
        due_by_offset[str(rounded)] = due_by_offset.get(str(rounded), 0) + 1
        if days <= 0:
            due_today_count += 1

    if not seen:
        return None
    return {
        "dueToday": due_today_count,
        "dueByOffset": due_by_offset,
    }


def build_study_status_from_raw(day: str, records: list[dict[str, Any]], today_items: list[dict[str, Any]], progress_row: sqlite3.Row | dict[str, Any] | None, snapshot_time_value: str | None = None) -> dict[str, Any]:
    """Build the canonical study-status API from raw reconstructed data.

    The new API has one source of truth and validates the two formulas required
    by the UI:
      今日认识 + 今日模糊 + 今日忘记 = 今日已复习 + 今日新学 = 今日已完成
      今日总数 = 今日新学 + 今日复习

    Here:
      今日新学/今日复习 in the equality mean completed new/review words.
      今日总数 uses total new/review tasks, including unfinished words.
    """
    active = [x for x in today_items if x.get("present_state") != "confirmed_absent"]
    done = [x for x in active if x.get("is_finished") is True]
    todo = [x for x in active if x.get("is_finished") is not True]

    known_done = count_response(done, "FAMILIAR")
    vague_done = count_response(done, "VAGUE")
    forget_done = count_response(done, "FORGET")
    all_known = count_response(active, "FAMILIAR")
    all_vague = count_response(active, "VAGUE")
    all_forget = count_response(active, "FORGET")

    new_done = sum(1 for x in done if x.get("is_new") is True)
    new_pending = sum(1 for x in todo if x.get("is_new") is True)
    review_done = sum(1 for x in done if x.get("is_new") is not True)
    review_pending = sum(1 for x in todo if x.get("is_new") is not True)

    response_done_total = known_done + vague_done + forget_done
    task_done_total = review_done + new_done
    task_total = review_done + review_pending + new_done + new_pending
    unfinished_total = review_pending + new_pending

    row_finished = nullable_int(row_value(progress_row, "finished"))
    row_total = nullable_int(row_value(progress_row, "total"))
    study_time_ms = nullable_int(row_value(progress_row, "study_time")) or 0
    snapshot = snapshot_time_value or row_value(progress_row, "snapshot_time") or ""

    checks = {
        "responseDoneEqualsTaskDone": response_done_total == task_done_total,
        "taskDoneEqualsFinished": row_finished is None or task_done_total == row_finished,
        "taskTotalEqualsDbTotal": row_total is None or task_total == row_total,
        "unfinishedEqualsTotalMinusFinished": row_finished is None or row_total is None or unfinished_total == max(row_total - row_finished, 0),
        "responseDoneTotal": response_done_total,
        "taskDoneTotal": task_done_total,
        "dbFinished": row_finished,
        "taskTotal": task_total,
        "dbTotal": row_total,
        "unfinishedTotal": unfinished_total,
    }

    # Prefer raw reconstructed counts.  Database aggregate columns are kept only
    # as dbSnapshot for diagnostics, not as a source of truth for UI math.
    today = {
        "known": known_done,
        "vague": vague_done,
        "forget": forget_done,
        "allKnown": all_known,
        "allVague": all_vague,
        "allForget": all_forget,
        "finished": task_done_total,
        "total": task_total,
        "unfinished": unfinished_total,
        "newDone": new_done,
        "newPending": new_pending,
        "newTotal": new_done + new_pending,
        "reviewDone": review_done,
        "reviewPending": review_pending,
        "reviewTotal": review_done + review_pending,
        "studyTimeMs": study_time_ms,
        "studyTimeSeconds": study_time_ms / 1000,
    }

    return {
        "version": 2,
        "date": day,
        "snapshot": snapshot,
        "capturedAt": snapshot,
        "today": today,
        "overall": _study_status_overall_from_records(records, day),
        "critical": _study_status_critical_from_today_items(active, day) or _study_status_critical_from_records(records, day),
        "checks": checks,
        "dbSnapshot": {
            "finished": row_finished,
            "total": row_total,
            "studyTimeMs": study_time_ms,
            "firstForgetDone": nullable_int(row_value(progress_row, "first_forget_done")),
            "firstForgetAll": nullable_int(row_value(progress_row, "first_forget_all")),
            "vagueDone": nullable_int(row_value(progress_row, "vague_done")),
            "vagueAll": nullable_int(row_value(progress_row, "vague_all")),
            "familiarDone": nullable_int(row_value(progress_row, "familiar_done")),
            "familiarAll": nullable_int(row_value(progress_row, "familiar_all")),
            "newWords": nullable_int(row_value(progress_row, "new_words")),
            "reviewWords": nullable_int(row_value(progress_row, "review_words")),
            "pendingNewWords": nullable_int(row_value(progress_row, "pending_new_words")),
            "allItemsCount": nullable_int(row_value(progress_row, "all_items_count")),
            "doneItemsCount": nullable_int(row_value(progress_row, "done_items_count")),
            "todoItemsCount": nullable_int(row_value(progress_row, "todo_items_count")),
        },
    }


@dataclass(frozen=True)
class ImportResult:
    snapshot_id: int
    snapshot_time: str
    day_key: str
    snapshot_name: str
    inserted_records: int
    today_item_changes: int = 0
    overview_changes: int = 0
    custom_word_stats: dict[str, int] | None = None


def _date_only(value: str | None) -> str:
    """从时间字符串中提取日期部分；优先按 API 时间解析。"""
    dt = parse_api_datetime(value)
    if dt:
        return dt.date().isoformat()
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return ""


def _days_between_date_text(a: str | None, b: str | None) -> int | None:
    """计算两个日期/时间文本的日期差：a - b，失败返回 None。"""
    da = _date_only(a)
    db = _date_only(b)
    if not da or not db:
        return None
    try:
        return (datetime.strptime(da, "%Y-%m-%d").date() - datetime.strptime(db, "%Y-%m-%d").date()).days
    except Exception:
        return None


def _sort_value(item: dict[str, Any], sort: str):
    """根据指定排序字段返回用于排序的值。"""
    state_order = {"逾期": 0, "忘记": 1, "模糊": 2, "认识": 3, "未作答": 4, "": 9}
    if sort == "word":
        return item.get("word") or ""
    if sort == "study_count":
        return -10**18 if item.get("studyCount") is None else item.get("studyCount")
    if sort == "add_date":
        return item.get("addDate") or ""
    if sort == "first_study_date":
        return item.get("firstStudyDate") or ""
    if sort == "last_study_date":
        return item.get("lastStudyDate") or ""
    if sort == "next_study_date":
        return item.get("nextStudyDate") or ""
    if sort == "current_state":
        return state_order.get(item.get("currentState") or "", 8)
    if sort == "overdue_first":
        return 0 if item.get("isOverdue") == "是" else 1
    if sort == "review_span":
        return -10**18 if item.get("reviewSpanDays") is None else item.get("reviewSpanDays")
    if sort == "critical_day":
        return -10**18 if item.get("criticalDays") is None else item.get("criticalDays")
    if sort == "today_order":
        return 10**18 if item.get("todayOrder") is None else item.get("todayOrder")
    return item.get("nextStudyDate") or ""


def _date_diff_days(a: str | None, b: str | None) -> float | None:
    """计算两个日期文本的天数差；当前实现返回整数天或 None。"""
    return _days_between_date_text(a, b)


def _today_item_lookup(items: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """为 today items 建立按稳定 key 和 spelling 查询的索引。"""
    by_key: dict[str, dict[str, Any]] = {}
    by_spelling: dict[str, dict[str, Any]] = {}
    for it in items:
        key = item_key({"voc_id": it.get("voc_id"), "voc_spelling": it.get("voc_spelling")})
        by_key[key] = it
        spelling = _stable_text_key(it.get("voc_spelling"))
        if spelling:
            by_spelling[spelling] = it
    return by_key, by_spelling


def _today_item_for_record(record: dict[str, Any], by_key: dict[str, dict[str, Any]], by_spelling: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """根据 record 在 today item 索引中查找对应 item。"""
    key = record_key(record)
    if key in by_key:
        return by_key[key]
    spelling = _stable_text_key(record.get("spelling"))
    if spelling:
        return by_spelling.get(spelling)
    return None


def _matches_due_filter(next_study_date: str, due: str, reference_day: str, overdue_days: int | None) -> bool:
    """判断 next_study_date 是否命中指定到期筛选条件。"""
    if not due:
        return True
    nd = _date_only(next_study_date)
    if not nd:
        return False
    try:
        next_date = datetime.strptime(nd, "%Y-%m-%d").date()
        ref_date = datetime.strptime(reference_day, "%Y-%m-%d").date()
    except Exception:
        return False
    delta = (next_date - ref_date).days
    if due == "today":
        return delta == 0
    if due == "tomorrow":
        return delta == 1
    if due == "next7":
        return 0 <= delta <= 7
    if due == "overdue":
        return delta < 0
    if due == "overdue_gt":
        n = max(0, int(overdue_days or 0))
        return delta <= -n if n else delta < 0
    return True


def _matches_today_filter(today_item: dict[str, Any] | None, today_filter: str) -> bool:
    """判断 today item 是否命中指定今日任务筛选条件。"""
    if not today_filter:
        return True
    if today_item is None:
        return False
    present = (today_item.get("present_state") or "observed") != "confirmed_absent"
    if not present:
        return False
    is_finished = today_item.get("is_finished") is True
    is_new = today_item.get("is_new") is True
    response = today_item.get("first_response") or ""
    if today_filter == "all":
        return True
    if today_filter == "done":
        return is_finished
    if today_filter == "todo":
        return not is_finished
    if today_filter == "new":
        return is_new
    if today_filter == "review":
        return today_item.get("is_new") is False
    if today_filter == "forget":
        return response == "FORGET"
    if today_filter == "vague":
        return response == "VAGUE"
    if today_filter == "familiar":
        return response == "FAMILIAR"
    return True


def build_dashboard_summary_from_records(records: list[dict[str, Any]], today_items: list[dict[str, Any]], day_key: str, snapshot_time_value: str | None = None, *, memory_thresholds_text: str | None = None, include_memory_items: bool = False) -> dict[str, Any]:
    """Build the lightweight summary needed by the dashboard without shipping all word rows."""
    total_study_count = 0.0
    total_study_count_n = 0
    out: dict[str, Any] = {
        "totalWords": len(records),
        "熟知": 0,
        "顽固": 0,
        "认识": 0,
        "模糊": 0,
        "忘记": 0,
        "逾期": 0,
        "avgStudyCount": 0,
        "dailyNewLearnedCount": 0,
        "dailyPendingNewCount": 0,
        "dailyReviewedCount": 0,
        "dailyAllReviewAvgStudyCount": None,
        "newLearnedResponseCounts": response_counts_empty(),
        "newLearnedResponseSampleCount": 0,
        "newLearnedResponseExcludedDate": "2026-05-11",
        "memoryReferenceNowUtc": day_key,
    }
    if include_memory_items:
        out.update({
            "memoryDiffs": [],
            "memoryDiffItems": [],
            "memoryReviewSpanItems": [],
            "memoryNextDueItems": [],
            "memoryReviewOutcomeItems": [],
        })

    memory_specs = parse_memory_threshold_specs_from_text(memory_thresholds_text) or parse_memory_threshold_specs_from_text(FALLBACK_MEMORY_THRESHOLDS)
    review_span_stats = _new_memory_stats(memory_specs)
    critical_stats = _new_memory_stats(memory_specs)
    overdue_stats = _new_memory_stats(memory_specs)

    today_by_key, today_by_spelling = _today_item_lookup(today_items)
    review_keys: set[str] = set()
    for item in today_items:
        if item.get("is_new") is True:
            continue
        if item.get("present_state") == "confirmed_absent":
            continue
        key = item_key({"voc_id": item.get("voc_id"), "voc_spelling": item.get("voc_spelling")})
        if key:
            review_keys.add(key)
        spelling = _stable_text_key(item.get("voc_spelling"))
        if spelling:
            review_keys.add(f"word:{spelling}")

    # 新学词反馈分布单独统计；按需求排除 2026-05-11 当天的新学词。
    if day_key != "2026-05-11":
        for item in today_items:
            if item.get("is_new") is True and item.get("is_finished") is True and item.get("present_state") != "confirmed_absent":
                bucket = response_bucket(item.get("first_response"))
                out["newLearnedResponseCounts"][bucket] = int(out["newLearnedResponseCounts"].get(bucket, 0)) + 1
                out["newLearnedResponseSampleCount"] += 1

    review_study_counts: list[float] = []
    for p in records:
        tag_text = str(p.get("tags_text") or "")
        tags = [x.strip() for x in tag_text.replace("；", ";").replace("，", ",").replace(",", ";").split(";") if x.strip()]
        if p.get("tag_well") == "是" or "熟知" in tags:
            out["熟知"] += 1
        if p.get("tag_sticking") == "是" or "顽固" in tags:
            out["顽固"] += 1

        state = str(p.get("current_state") or "").strip()
        if not state and p.get("is_overdue") == "是":
            state = "逾期"
        if state in ("认识", "模糊", "忘记", "逾期"):
            out[state] += 1

        study_count = p.get("study_count")
        try:
            study_count_f = float(study_count)
            total_study_count += study_count_f
            total_study_count_n += 1
        except Exception:
            study_count_f = float("nan")

        span_days = _date_diff_days(p.get("next_study_date"), p.get("last_study_date"))
        word_label = p.get("spelling") or p.get("word") or p.get("voc_spelling") or ""
        voc_id = p.get("voc_id") or p.get("id") or ""
        if span_days is not None:
            item = {
                "days": span_days,
                "studyCount": study_count_f if study_count_f == study_count_f else None,
                "state": state,
                "word": word_label,
                "vocId": voc_id,
                "addDate": _date_only(p.get("add_date")) or (p.get("add_date") or ""),
                "firstStudyDate": _date_only(p.get("first_study_date")) or (p.get("first_study_date") or ""),
                "lastStudyDate": _date_only(p.get("last_study_date")) or (p.get("last_study_date") or ""),
                "nextStudyDate": _date_only(p.get("next_study_date")) or (p.get("next_study_date") or ""),
            }
            _add_memory_stat(review_span_stats, memory_specs, span_days, study_count_f if study_count_f == study_count_f else None, "review_span")
            if include_memory_items:
                out["memoryDiffs"].append(span_days)
                out["memoryDiffItems"].append(item)
                out["memoryReviewSpanItems"].append(item)

        critical_days = _date_diff_days(p.get("next_study_date"), f"{day_key}T12:00:00+08:00")
        if critical_days is not None:
            _add_memory_stat(critical_stats, memory_specs, critical_days, study_count_f if study_count_f == study_count_f else None, "critical_point")
            _add_memory_stat(overdue_stats, memory_specs, critical_days, study_count_f if study_count_f == study_count_f else None, "overdue")
            if include_memory_items:
                out["memoryNextDueItems"].append({
                    "days": critical_days,
                    "studyCount": study_count_f if study_count_f == study_count_f else None,
                    "state": state,
                    "word": word_label,
                    "vocId": voc_id,
                    "addDate": _date_only(p.get("add_date")) or (p.get("add_date") or ""),
                    "firstStudyDate": _date_only(p.get("first_study_date")) or (p.get("first_study_date") or ""),
                    "lastStudyDate": _date_only(p.get("last_study_date")) or (p.get("last_study_date") or ""),
                    "nextStudyDate": _date_only(p.get("next_study_date")) or (p.get("next_study_date") or ""),
                })

        rec_key = record_key(p)
        spelling_key = _stable_text_key(p.get("spelling"))
        if rec_key in review_keys or (spelling_key and f"word:{spelling_key}" in review_keys):
            if study_count_f == study_count_f:
                review_study_counts.append(study_count_f)
            if span_days is not None:
                resp_item = today_by_key.get(rec_key) or (today_by_spelling.get(spelling_key) if spelling_key else None)
                if resp_item and resp_item.get("is_finished") is True and resp_item.get("is_new") is False:
                    if include_memory_items:
                        out["memoryReviewOutcomeItems"].append({
                            "days": span_days,
                            "studyCount": study_count_f if study_count_f == study_count_f else None,
                            "response": response_bucket(resp_item.get("first_response")),
                            "word": word_label,
                            "vocId": voc_id,
                            "addDate": _date_only(p.get("add_date")) or (p.get("add_date") or ""),
                            "firstStudyDate": _date_only(p.get("first_study_date")) or (p.get("first_study_date") or ""),
                            "lastStudyDate": _date_only(p.get("last_study_date")) or (p.get("last_study_date") or ""),
                            "nextStudyDate": _date_only(p.get("next_study_date")) or (p.get("next_study_date") or ""),
                        })

    out["memoryReviewSpanStats"] = _finalize_memory_stats(review_span_stats)
    out["memoryCriticalStats"] = _finalize_memory_stats(critical_stats)
    out["memoryOverdueStats"] = _finalize_memory_stats(overdue_stats)
    out["avgStudyCount"] = (total_study_count / total_study_count_n) if total_study_count_n else 0
    out["dailyNewLearnedCount"] = sum(1 for x in today_items if x.get("is_new") is True and x.get("is_finished") is True and x.get("present_state") != "confirmed_absent")
    out["dailyPendingNewCount"] = sum(1 for x in today_items if x.get("is_new") is True and x.get("is_finished") is not True and x.get("present_state") != "confirmed_absent")
    out["dailyReviewedCount"] = sum(1 for x in today_items if x.get("is_new") is False and x.get("is_finished") is True and x.get("present_state") != "confirmed_absent")
    out["dailyAllReviewAvgStudyCount"] = (sum(review_study_counts) / len(review_study_counts)) if review_study_counts else None
    return out


def parse_snapshot_display_time(captured_at: str) -> int:
    """把快照时间字符串转换为毫秒时间戳；解析失败返回 0。"""
    try:
        return int(datetime.fromisoformat(str(captured_at)).timestamp() * 1000)
    except Exception:
        return 0


#返回单词结构
def enrich_today_items_with_prediction_raw_data(items: list[dict[str, Any]], overview_records: list[dict[str, Any]], day_key: str, snapshot_time: str | None = None, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Attach memory fields from the same historical input chain used by FSRS.

    Current values are built from the current overview records, via the same
    raw summary buckets used by dashboard prediction displays.

    Previous values are NOT derived from time-point A and NOT from patch
    event old_* fields.  They come from the per-word historical review event
    sequence used as FSRS input.  For a current study_count=N, previous is the
    latest recovered review event for the same voc_id with study_count<N.
    """
    if not items:
        return items

    def _num_or_none(value: Any) -> float | None:
        """把输入转换为 float；空值或转换失败返回 None。"""
        if value is None or value == "":
            return None
        try:
            n = float(value)
        except Exception:
            return None
        return n

    def _history_previous(events: list[dict[str, Any]], current_count: Any, current_raw: dict[str, Any] | None) -> dict[str, Any] | None:
        if not events:
            return None
        current_n = _num_or_none(current_count)
        if current_n is not None:
            candidates = [event for event in events if (_num_or_none(event.get("studyCount")) is not None and _num_or_none(event.get("studyCount")) < current_n)]
            if candidates:
                return candidates[-1]

        # Fallback: match the current state by study_count/date tuple and take the
        # immediately preceding event.  This covers cases where current_count is
        # unavailable but dates are present.
        if current_raw:
            current_key = (
                str(current_raw.get("lastStudyDate") or current_raw.get("last_study_date") or ""),
                str(current_raw.get("nextStudyDate") or current_raw.get("next_study_date") or ""),
                str(current_raw.get("studyCount") or current_raw.get("study_count") or ""),
            )
            for idx, event in enumerate(events):
                event_key = (
                    str(event.get("lastStudyDate") or ""),
                    str(event.get("nextStudyDate") or ""),
                    str(event.get("studyCount") or ""),
                )
                if event_key == current_key and idx > 0:
                    return events[idx - 1]

        return None

    def _raw_from_overview_record(rec: dict[str, Any]) -> dict[str, Any] | None:
        vid = str(rec.get("voc_id") or rec.get("vocId") or "").strip()
        if not vid:
            return None
        last_study = rec.get("last_study_date") or rec.get("lastStudyDate") or ""
        next_study = rec.get("next_study_date") or rec.get("nextStudyDate") or ""
        span_days = _date_diff_days(next_study, last_study)
        study_count = rec.get("study_count")
        if study_count is None:
            study_count = rec.get("studyCount")
        if span_days is None and study_count in (None, "") and not last_study and not next_study:
            return None
        return {
            "word": rec.get("spelling") or rec.get("voc_spelling") or rec.get("word") or "",
            "vocId": vid,
            "days": span_days,
            "studyCount": study_count,
            "lastStudyDate": _date_only(last_study) or (last_study or ""),
            "nextStudyDate": _date_only(next_study) or (next_study or ""),
            "lastResponse": rec.get("last_response_cn") or rec.get("last_response") or rec.get("lastResponse") or "",
        }

    raw_by_voc_id: dict[str, dict[str, Any]] = {}

    if overview_records:
        try:
            raw_summary = build_dashboard_summary_from_records(overview_records, items, day_key, snapshot_time, include_memory_items=True)
        except Exception:
            raw_summary = {}

        for bucket_name in ("memoryReviewSpanItems", "memoryNextDueItems", "memoryReviewOutcomeItems", "memoryDiffItems"):
            for raw in raw_summary.get(bucket_name) or []:
                vid = str(raw.get("vocId") or raw.get("voc_id") or "").strip()
                if vid and vid not in raw_by_voc_id:
                    raw_by_voc_id[vid] = raw

        # Fallback: raw summary buckets may omit some rows; use the overview
        # record itself in the same shape.
        for rec in overview_records:
            vid = str(rec.get("voc_id") or rec.get("vocId") or "").strip()
            if not vid or vid in raw_by_voc_id:
                continue
            raw = _raw_from_overview_record(rec)
            if raw:
                raw_by_voc_id[vid] = raw

    voc_ids: set[str] = set()
    for item in items:
        vid = str(item.get("voc_id") or item.get("vocId") or "").strip()
        if vid:
            voc_ids.add(vid)

    history_index: dict[str, list[dict[str, Any]]] = {}
    if conn is not None and voc_ids:
        try:
            from momo_fsrs import build_review_history_state_index
            history_index = build_review_history_state_index(conn, voc_ids=voc_ids, max_words=100000)
        except Exception:
            history_index = {}

    out: list[dict[str, Any]] = []
    for item in items:
        merged = dict(item)
        vid = str(item.get("voc_id") or item.get("vocId") or "").strip()
        raw = raw_by_voc_id.get(vid) if vid else None

        if raw:
            span_days = raw.get("days")
            if span_days is None:
                span_days = raw.get("reviewSpanDays") or raw.get("memoryDurabilityDays")
            merged.update({
                "study_count": raw.get("studyCount"),
                "studyCount": raw.get("studyCount"),
                "last_study_date": raw.get("lastStudyDate") or "",
                "lastStudyDate": raw.get("lastStudyDate") or "",
                "next_study_date": raw.get("nextStudyDate") or "",
                "nextStudyDate": raw.get("nextStudyDate") or "",
                "last_response": raw.get("lastResponse") or raw.get("response") or "",
                "review_span_days": span_days,
                "memory_durability_days": span_days,
                "days": span_days,
                "prediction_raw_memory": raw,
            })

        current_count = None
        if raw:
            current_count = raw.get("studyCount") or raw.get("study_count")
        if current_count is None:
            current_count = merged.get("study_count") or merged.get("studyCount")

        previous_raw = _history_previous(history_index.get(vid) or [], current_count, raw) if vid else None
        if previous_raw:
            previous_span_days = previous_raw.get("memoryDurabilityDays")
            if previous_span_days is None:
                previous_span_days = previous_raw.get("reviewSpanDays")
            previous_last = _date_only(previous_raw.get("lastStudyDateRaw") or previous_raw.get("lastStudyDate")) or (previous_raw.get("lastStudyDate") or "")
            previous_next = _date_only(previous_raw.get("nextStudyDateRaw") or previous_raw.get("nextStudyDate")) or (previous_raw.get("nextStudyDate") or "")
            merged.update({
                "previous_study_count": previous_raw.get("studyCount"),
                "previousStudyCount": previous_raw.get("studyCount"),
                "previous_last_study_date": previous_last,
                "previousLastStudyDate": previous_last,
                "previous_next_study_date": previous_next,
                "previousNextStudyDate": previous_next,
                "previous_review_span_days": previous_span_days,
                "previous_memory_durability_days": previous_span_days,
                "previousDays": previous_span_days,
                "prediction_previous_raw_memory": previous_raw,
            })

        out.append(merged)
    return out


__all__ = [name for name in globals() if not name.startswith('__')]
