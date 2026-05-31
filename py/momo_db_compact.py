#!/usr/bin/env python3
"""Compact v3 SQLite storage implementation for MomoAnalyze.

Only the latest compact_v3_voc_id schema is supported. Legacy wide tables and
migration compatibility paths are intentionally not supported in this module.
"""

from __future__ import annotations

from momo_db_common import *


# ---------------------------------------------------------------------------
# Snapshot safety helpers
# ---------------------------------------------------------------------------


def delete_latest_incomplete_snapshot(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Delete only the latest snapshot when it is explicitly incomplete.

    Complete historical snapshots are never touched. Older incomplete markers are
    ignored unless they are the latest snapshot in the database.
    """
    setup_schema(conn)
    row = conn.execute(
        """
        SELECT snapshot_time, study_day_key, incomplete_reason
        FROM snapshots
        ORDER BY snapshot_time DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None

    complete = conn.execute(
        """
        SELECT COALESCE(data_complete, 1) AS data_complete,
               COALESCE(api_success, 1) AS api_success
        FROM snapshots
        WHERE snapshot_time=?
        """,
        (row["snapshot_time"],),
    ).fetchone()
    if complete and int(complete["data_complete"] or 0) == 1 and int(complete["api_success"] or 0) == 1:
        return None

    info = {
        "snapshotTime": row["snapshot_time"],
        "studyDayKey": row["study_day_key"],
        "reason": row["incomplete_reason"] or "",
    }
    conn.execute("DELETE FROM snapshots WHERE snapshot_time=?", (row["snapshot_time"],))
    return info


def record_incomplete_snapshot(conn: sqlite3.Connection, *, captured_at: datetime, source: str, reason: str) -> str:
    """Record a failed/incomplete pull as a lightweight marker only."""
    setup_schema(conn)
    study_day_key = study_day_key_from_dt(captured_at)
    snap_time = iso_bj(captured_at, milliseconds=True)
    conn.execute(
        """
        INSERT INTO snapshots(snapshot_time, study_day_key, source, api_success, data_complete, incomplete_reason, created_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(snapshot_time) DO UPDATE SET
            study_day_key=excluded.study_day_key,
            source=excluded.source,
            api_success=excluded.api_success,
            data_complete=excluded.data_complete,
            incomplete_reason=excluded.incomplete_reason
        """,
        (snap_time, study_day_key, source, 0, 0, reason, iso_bj()),
    )
    return snap_time


# ---------------------------------------------------------------------------
# Small formatting / alert helpers
# ---------------------------------------------------------------------------


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _limit_alert_items(items: list[dict[str, Any]], limit: int = 300) -> tuple[list[dict[str, Any]], int]:
    total = len(items)
    return items[:limit], max(0, total - limit)


def _overview_alert_item(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "word": payload.get("spelling") or "",
        "vocId": payload.get("voc_id") or "",
        "currentState": payload.get("current_state") or "",
        "nextStudyDate": payload.get("next_study_date") or "",
        "lastResponse": payload.get("last_response_cn") or payload.get("last_response") or "",
        "studyCount": payload.get("study_count"),
        "tags": payload.get("tags_text") or "",
    }


def _today_alert_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "word": item.get("voc_spelling") or item.get("spelling") or "",
        "vocId": item.get("voc_id") or "",
        "order": item.get("order") if item.get("order") is not None else item.get("order_index"),
        "firstResponse": item.get("first_response") or "",
        "isNew": item.get("is_new"),
        "isFinished": item.get("is_finished"),
        "presentState": item.get("present_state") or "",
    }


# ---------------------------------------------------------------------------
# Compact performance schema
# ---------------------------------------------------------------------------


COMPACT_SCHEMA_VERSION = "compact_v3_voc_id"
COMPACT_REQUIRED_TABLES = {
    "compact_meta",
    "compact_overview_day_checkpoints",
    "compact_overview_day_records",
    "compact_overview_patch_events",
    "compact_study_day_initial_items",
    "compact_study_day_patch_events",
    "snapshots",
    "snapshot_progress",
    "words",
}

PATCH_EMPTY_MARKER = "enmpty"

PATCH_EVENT_FIELD_TYPES = {
    "order_index": "int",
    "is_new": "bool_int",
    "is_finished": "bool_int",
    "study_count": "int",
    "old_study_count": "int",
}

OVERVIEW_EVENT_FIELDS = (
    "last_study_date",
    "next_study_date",
    "last_response",
    "study_count",
    "current_state",
    "tags_text",
    "is_overdue",
)
OVERVIEW_OLD_EVENT_FIELDS = tuple(f"old_{field}" for field in OVERVIEW_EVENT_FIELDS)

STUDY_ITEM_EVENT_FIELDS = (
    "voc_spelling",
    "order_index",
    "first_response",
    "is_new",
    "is_finished",
    "present_state",
)


def _compact_table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _compact_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r["name"] if isinstance(r, sqlite3.Row) else r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}


def _overview_old_field(field_name: str) -> str:
    return f"old_{field_name}"


def _ensure_narrow_event_schema(conn: sqlite3.Connection) -> None:
    """Create sparse patch event tables used by the runtime."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS compact_overview_patch_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          snapshot_time TEXT NOT NULL,
          study_day_key TEXT NOT NULL,
          voc_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          last_study_date TEXT,
          next_study_date TEXT,
          last_response TEXT,
          study_count INTEGER,
          current_state TEXT,
          tags_text TEXT,
          is_overdue TEXT,
          old_last_study_date TEXT,
          old_next_study_date TEXT,
          old_last_response TEXT,
          old_study_count INTEGER,
          old_current_state TEXT,
          old_tags_text TEXT,
          old_is_overdue TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(snapshot_time, voc_id, event_type)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS compact_study_day_patch_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          snapshot_time TEXT NOT NULL,
          study_day_key TEXT NOT NULL,
          voc_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          voc_spelling TEXT,
          order_index INTEGER,
          first_response TEXT,
          is_new INTEGER,
          is_finished INTEGER,
          present_state TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(snapshot_time, voc_id, event_type)
        )
        """
    )

    existing_overview_event_cols = _compact_columns(conn, "compact_overview_patch_events")
    overview_old_column_defs = {
        "old_last_study_date": "TEXT",
        "old_next_study_date": "TEXT",
        "old_last_response": "TEXT",
        "old_study_count": "INTEGER",
        "old_current_state": "TEXT",
        "old_tags_text": "TEXT",
        "old_is_overdue": "TEXT",
    }
    for col, typ in overview_old_column_defs.items():
        if col not in existing_overview_event_cols:
            conn.execute(f"ALTER TABLE compact_overview_patch_events ADD COLUMN {col} {typ}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_overview_patch_events_day_time ON compact_overview_patch_events(study_day_key, snapshot_time, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_overview_patch_events_voc ON compact_overview_patch_events(voc_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_day_patch_events_day_time ON compact_study_day_patch_events(study_day_key, snapshot_time, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_day_patch_events_voc ON compact_study_day_patch_events(voc_id)")


def setup_schema(conn: sqlite3.Connection) -> None:
    _ensure_narrow_event_schema(conn)
    missing = sorted(t for t in COMPACT_REQUIRED_TABLES if not _compact_table_exists(conn, t))
    if missing:
        raise RuntimeError(
            "SQLite 数据库不是 compact_v3_voc_id 精简性能版，缺少表: "
            + ", ".join(missing)
            + "。请重新同步生成最新版数据库。"
        )

    required_columns = {
        "words": {"voc_id", "spelling", "first_seen_at", "last_seen_at"},
        "compact_overview_day_records": {"study_day_key", "snapshot_time", "voc_id", "spelling", "next_study_date", "study_count"},
        "compact_overview_day_checkpoints": {"study_day_key", "snapshot_time", "record_count", "created_at"},
        "compact_overview_patch_events": {"study_day_key", "snapshot_time", "voc_id", "event_type", "created_at"},
        "compact_study_day_initial_items": {"study_day_key", "snapshot_time", "voc_id", "voc_spelling", "order_index", "first_response", "is_new", "is_finished", "present_state"},
        "compact_study_day_patch_events": {"study_day_key", "snapshot_time", "voc_id", "event_type", "created_at"},
        "snapshot_progress": {"snapshot_time", "study_day_key", "finished", "total", "study_time"},
        "snapshots": {"snapshot_time", "study_day_key", "source", "api_success", "data_complete", "created_at"},
    }
    bad: list[str] = []
    for table, req in required_columns.items():
        cols = _compact_columns(conn, table)
        miss = sorted(req - cols)
        if miss:
            bad.append(f"{table} 缺少列 {miss}")

    forbidden_columns = {
        "words": {"word_key"},
        "compact_overview_day_records": {"record_key", "raw_json", "tags_json"},
        "compact_study_day_initial_items": {"item_key", "raw_json"},
        "snapshot_progress": {"raw_json"},
    }
    for table, forbidden in forbidden_columns.items():
        cols = _compact_columns(conn, table)
        present = sorted(cols & forbidden)
        if present:
            bad.append(f"{table} 仍包含废弃列 {present}")

    if bad:
        raise RuntimeError("SQLite compact_v3_voc_id schema 不完整: " + "; ".join(bad))

    row = conn.execute("SELECT value FROM compact_meta WHERE key='schema_version'").fetchone()
    if not row or str(row["value"] if isinstance(row, sqlite3.Row) else row[0]) != COMPACT_SCHEMA_VERSION:
        raise RuntimeError("SQLite schema_version 不是 compact_v3_voc_id，请重新同步生成最新版数据库。")

    conn.execute("PRAGMA optimize")


# ---------------------------------------------------------------------------
# Day / snapshot lookup
# ---------------------------------------------------------------------------


def _compact_latest_day(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT study_day_key FROM compact_overview_day_checkpoints ORDER BY study_day_key DESC LIMIT 1").fetchone()
    if row:
        return str(row["study_day_key"])
    row = conn.execute(
        """
        SELECT study_day_key
        FROM snapshots
        WHERE COALESCE(data_complete,1)=1 AND COALESCE(api_success,1)=1
        ORDER BY study_day_key DESC, snapshot_time DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["study_day_key"]) if row else None


def list_days(conn: sqlite3.Connection) -> list[str]:
    setup_schema(conn)
    rows = conn.execute(
        """
        SELECT DISTINCT c.study_day_key
        FROM compact_overview_day_checkpoints c
        JOIN snapshots s ON s.snapshot_time=c.snapshot_time
        WHERE COALESCE(s.data_complete,1)=1 AND COALESCE(s.api_success,1)=1
        ORDER BY c.study_day_key
        """
    ).fetchall()
    if rows:
        return [str(r["study_day_key"]) for r in rows]
    rows = conn.execute(
        """
        SELECT DISTINCT study_day_key
        FROM snapshots
        WHERE COALESCE(data_complete,1)=1 AND COALESCE(api_success,1)=1
        ORDER BY study_day_key
        """
    ).fetchall()
    return [str(r["study_day_key"]) for r in rows]


def latest_day(conn: sqlite3.Connection) -> str | None:
    setup_schema(conn)
    return _compact_latest_day(conn)


def latest_snapshot_time_for_day(conn: sqlite3.Connection, study_day_key: str) -> str | None:
    setup_schema(conn)

    # Prefer compact checkpoint only when the pointed snapshot is complete.
    row = conn.execute(
        """
        SELECT c.snapshot_time
        FROM compact_overview_day_checkpoints c
        JOIN snapshots s ON s.snapshot_time=c.snapshot_time
        WHERE c.study_day_key=?
          AND COALESCE(s.data_complete, 1)=1
          AND COALESCE(s.api_success, 1)=1
        ORDER BY c.snapshot_time DESC
        LIMIT 1
        """,
        (study_day_key,),
    ).fetchone()
    if row:
        return str(row["snapshot_time"])

    row = conn.execute(
        """
        SELECT snapshot_time
        FROM snapshots
        WHERE study_day_key=?
          AND COALESCE(data_complete, 1)=1
          AND COALESCE(api_success, 1)=1
        ORDER BY snapshot_time DESC
        LIMIT 1
        """,
        (study_day_key,),
    ).fetchone()
    return str(row["snapshot_time"]) if row else None


def latest_snapshot_time(conn: sqlite3.Connection) -> str | None:
    setup_schema(conn)
    row = conn.execute(
        """
        SELECT c.snapshot_time
        FROM compact_overview_day_checkpoints c
        JOIN snapshots s ON s.snapshot_time=c.snapshot_time
        WHERE COALESCE(s.data_complete,1)=1 AND COALESCE(s.api_success,1)=1
        ORDER BY c.study_day_key DESC, c.snapshot_time DESC
        LIMIT 1
        """
    ).fetchone()
    if row:
        return str(row["snapshot_time"])
    row = conn.execute(
        """
        SELECT snapshot_time
        FROM snapshots
        WHERE COALESCE(data_complete,1)=1 AND COALESCE(api_success,1)=1
        ORDER BY snapshot_time DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["snapshot_time"]) if row else None


# ---------------------------------------------------------------------------
# Payload transforms
# ---------------------------------------------------------------------------


def _v3_overview_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "record_key": row_value(row, "voc_id"),
        "voc_id": row_value(row, "voc_id"),
        "spelling": row_value(row, "spelling"),
        "add_date": row_value(row, "add_date"),
        "first_study_date": row_value(row, "first_study_date"),
        "last_study_date": row_value(row, "last_study_date"),
        "next_study_date": row_value(row, "next_study_date"),
        "last_response": row_value(row, "last_response"),
        "last_response_cn": row_value(row, "last_response_cn"),
        "study_count": row_value(row, "study_count"),
        "tags_text": row_value(row, "tags_text"),
        "tag_well": row_value(row, "tag_well"),
        "tag_sticking": row_value(row, "tag_sticking"),
        "current_state": row_value(row, "current_state"),
        "is_overdue": row_value(row, "is_overdue"),
        "state_hash": row_value(row, "state_hash"),
    }


def _v3_study_item_to_api(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "voc_id": row_value(row, "voc_id"),
        "voc_spelling": row_value(row, "voc_spelling"),
        "order": row_value(row, "order_index"),
        "first_response": row_value(row, "first_response"),
        "is_new": None if row_value(row, "is_new") is None else bool(row_value(row, "is_new")),
        "is_finished": None if row_value(row, "is_finished") is None else bool(row_value(row, "is_finished")),
        "present_state": row_value(row, "present_state") or "observed",
    }


def _overview_state_row_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_key": payload.get("voc_id"),
        "voc_id": payload.get("voc_id"),
        "spelling": payload.get("spelling") or "",
        "add_date": payload.get("add_date"),
        "first_study_date": payload.get("first_study_date"),
        "last_study_date": payload.get("last_study_date"),
        "next_study_date": payload.get("next_study_date"),
        "last_response": payload.get("last_response"),
        "last_response_cn": payload.get("last_response_cn"),
        "study_count": payload.get("study_count"),
        "tags_text": payload.get("tags_text"),
        "tag_well": payload.get("tag_well"),
        "tag_sticking": payload.get("tag_sticking"),
        "current_state": payload.get("current_state"),
        "is_overdue": payload.get("is_overdue"),
        "state_hash": payload.get("state_hash"),
    }


def overview_payload_to_csv_row(p: dict[str, Any], today_item: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "voc_id": p.get("voc_id") or "",
        "单词": p.get("spelling") or "",
        "加入日期": p.get("add_date") or "",
        "首次学习日期": p.get("first_study_date") or "",
        "最近学习日期": p.get("last_study_date") or "",
        "下次复习日期": p.get("next_study_date") or "",
        "最近一次反应": p.get("last_response_cn") or "",
        "学习次数": "" if p.get("study_count") is None else p.get("study_count"),
        "标签": p.get("tags_text") or "",
        "标签_熟知": p.get("tag_well") or "否",
        "标签_顽固": p.get("tag_sticking") or "否",
        "标签汇总": p.get("tags_text") or "",
        "当前状态": p.get("current_state") or "",
        "是否逾期": p.get("is_overdue") or "否",
        "熟悉程度(推断)": p.get("current_state") or "",
        "当日已新学": "是" if today_item and today_item.get("is_new") is True and today_item.get("is_finished") is True else "",
        "当日已复习": "是" if today_item and today_item.get("is_new") is False and today_item.get("is_finished") is True else "",
        "当日待新学": "是" if today_item and today_item.get("is_new") is True and today_item.get("is_finished") is not True else "",
    }


def row_to_overview_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return _v3_overview_payload(row)


# ---------------------------------------------------------------------------
# Patch event helpers
# ---------------------------------------------------------------------------


def _patch_event_value(value: Any) -> Any:
    if value is None:
        return PATCH_EMPTY_MARKER
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def _patch_event_cast(field_name: str, value: Any) -> Any:
    if value == PATCH_EMPTY_MARKER:
        return None
    typ = PATCH_EVENT_FIELD_TYPES.get(str(field_name))
    if typ == "int":
        return nullable_int(value)
    if typ == "bool_int":
        return nullable_bool_int(value)
    return value


def _insert_patch_event(
    conn: sqlite3.Connection,
    table: str,
    event_fields: Iterable[str],
    *,
    snapshot_time: str,
    study_day_key: str,
    voc_id: str,
    event_type: str,
    values: dict[str, Any] | None = None,
    old_values: dict[str, Any] | None = None,
    created_at: str,
) -> None:
    safe_values: dict[str, Any] = {}
    allowed = set(event_fields)
    if values:
        safe_values = {k: _patch_event_value(v) for k, v in values.items() if k in allowed}
    if old_values:
        safe_values.update({
            _overview_old_field(k): _patch_event_value(v)
            for k, v in old_values.items()
            if k in allowed
        })

    conn.execute(
        f"DELETE FROM {table} WHERE snapshot_time=? AND voc_id=? AND event_type=?",
        (snapshot_time, voc_id, event_type),
    )
    columns = ["snapshot_time", "study_day_key", "voc_id", "event_type", *safe_values.keys(), "created_at"]
    placeholders = ",".join("?" for _ in columns)
    params = [snapshot_time, study_day_key, voc_id, event_type, *safe_values.values(), created_at]
    conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})", params)


def _apply_patch_study_events(state: dict[str, dict[str, Any]], rows: list[sqlite3.Row]) -> int:
    applied = 0
    for ev in rows:
        vid = str(ev["voc_id"] or "").strip()
        if not vid:
            continue
        event_type = str(ev["event_type"] or "")
        if event_type == "record_removed":
            state.pop(vid, None)
            applied += 1
            continue
        cur = state.get(vid, {"voc_id": vid})
        if event_type not in {"patch", "record_inserted"}:
            continue
        for field_name in STUDY_ITEM_EVENT_FIELDS:
            value = ev[field_name]
            if value is not None:
                cur[field_name] = _patch_event_cast(field_name, value)
        cur.setdefault("present_state", "observed")
        state[vid] = cur
        applied += 1
    return applied


def _reverse_compact_overview_events(conn: sqlite3.Connection, state: dict[str, dict[str, Any]], rows: list[sqlite3.Row]) -> None:
    if not rows:
        return
    word_rows = conn.execute("SELECT voc_id, spelling FROM words").fetchall()
    spelling_by_voc_id = {str(r["voc_id"]): (r["spelling"] or "") for r in word_rows if r["voc_id"]}
    for ev in reversed(rows):
        vid = str(ev["voc_id"] or "").strip()
        if not vid:
            continue
        event_type = str(ev["event_type"] or "")
        old_values = {field: _patch_event_cast(field, row_value(ev, _overview_old_field(field))) for field in OVERVIEW_EVENT_FIELDS}
        has_old = any(row_value(ev, _overview_old_field(field)) is not None for field in OVERVIEW_EVENT_FIELDS)
        if event_type == "record_inserted":
            state.pop(vid, None)
            continue
        if not has_old:
            continue
        if event_type == "record_removed":
            state[vid] = _overview_state_row_from_payload({
                "voc_id": vid,
                "spelling": spelling_by_voc_id.get(vid, ""),
                **old_values,
            })
            continue
        if event_type == "patch":
            cur = state.get(vid) or _overview_state_row_from_payload({"voc_id": vid, "spelling": spelling_by_voc_id.get(vid, "")})
            for field, value in old_values.items():
                cur[field] = value
            state[vid] = cur


# ---------------------------------------------------------------------------
# Reconstruction APIs
# ---------------------------------------------------------------------------


def reconstruct_study_day_items_at(
    conn: sqlite3.Connection,
    study_day_key: str,
    snapshot_time: str,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    setup_schema(conn)
    t = _trace_step_start()
    cached = _cache_get("compact_v3_study_items", study_day_key, snapshot_time)
    if cached is not None:
        _trace_step_done(trace, "compact_v3_study_items_cache_hit", t, rows=len(cached))
        return cached

    base = conn.execute(
        """
        SELECT snapshot_time
        FROM compact_study_day_initial_items
        WHERE study_day_key=? AND snapshot_time<=?
        ORDER BY snapshot_time DESC
        LIMIT 1
        """,
        (study_day_key, snapshot_time),
    ).fetchone()

    if not base:
        _trace_step_done(trace, "compact_v3_study_reconstruct_no_baseline", t, rows=0)
        _cache_put("compact_v3_study_items", study_day_key, snapshot_time, [])
        return []

    base_snapshot = str(base["snapshot_time"])
    rows = conn.execute(
        """
        SELECT *
        FROM compact_study_day_initial_items
        WHERE study_day_key=? AND snapshot_time=?
        ORDER BY order_index IS NULL, order_index, voc_spelling COLLATE NOCASE
        """,
        (study_day_key, base_snapshot),
    ).fetchall()

    state: dict[str, dict[str, Any]] = {}
    for r in rows:
        vid = str(r["voc_id"] or "").strip()
        if not vid:
            continue
        state[vid] = {
            "voc_id": vid,
            "voc_spelling": r["voc_spelling"],
            "order_index": r["order_index"],
            "first_response": r["first_response"],
            "is_new": r["is_new"],
            "is_finished": r["is_finished"],
            "present_state": r["present_state"] or "observed",
        }

    initial_count = len(state)
    patch_changes = conn.execute(
        """
        SELECT *
        FROM compact_study_day_patch_events
        WHERE study_day_key=? AND snapshot_time>? AND snapshot_time<=?
        ORDER BY snapshot_time ASC, id ASC
        """,
        (study_day_key, base_snapshot, snapshot_time),
    ).fetchall()
    _apply_patch_study_events(state, patch_changes)

    out = [_v3_study_item_to_api(x) for x in state.values()]
    out.sort(key=lambda x: (x.get("order") is None, x.get("order") if x.get("order") is not None else 10**12, (x.get("voc_spelling") or "").lower()))
    _trace_step_done(trace, "compact_v3_study_reconstruct", t, baseline=base_snapshot, initial=initial_count, patchEvents=len(patch_changes), rows=len(out))
    _cache_put("compact_v3_study_items", study_day_key, snapshot_time, out)
    return out


def reconstruct_overview_records_at(
    conn: sqlite3.Connection,
    study_day_key: str,
    snapshot_time: str,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    setup_schema(conn)
    t = _trace_step_start()
    cached = _cache_get("compact_v3_overview", study_day_key, snapshot_time)
    if cached is not None:
        _trace_step_done(trace, "compact_v3_overview_cache_hit", t, rows=len(cached))
        return cached

    rows = conn.execute(
        """
        SELECT *
        FROM compact_overview_day_records
        WHERE study_day_key=?
        ORDER BY next_study_date, add_date, spelling COLLATE NOCASE
        """,
        (study_day_key,),
    ).fetchall()
    state = {str(r["voc_id"]): _v3_overview_payload(r) for r in rows if r["voc_id"]}

    checkpoint = conn.execute(
        "SELECT snapshot_time FROM compact_overview_day_checkpoints WHERE study_day_key=?",
        (study_day_key,),
    ).fetchone()
    latest_snapshot = str(checkpoint["snapshot_time"]) if checkpoint else None
    reversed_events = 0
    if latest_snapshot and snapshot_time < latest_snapshot:
        patch_rows = conn.execute(
            """
            SELECT *
            FROM compact_overview_patch_events
            WHERE study_day_key=? AND snapshot_time>? AND snapshot_time<=?
            ORDER BY snapshot_time ASC, id ASC
            """,
            (study_day_key, snapshot_time, latest_snapshot),
        ).fetchall()
        _reverse_compact_overview_events(conn, state, patch_rows)
        reversed_events = len(patch_rows)

    out = sorted(state.values(), key=lambda x: (x.get("next_study_date") or "", x.get("add_date") or "", (x.get("spelling") or "").lower()))
    _trace_step_done(trace, "compact_v3_overview_day_records_query", t, rows=len(out), latest=latest_snapshot, reversedEvents=reversed_events)
    _cache_put("compact_v3_overview", study_day_key, snapshot_time, out)
    return out


# ---------------------------------------------------------------------------
# Progress and dashboard day payloads
# ---------------------------------------------------------------------------


def _v3_snapshot_detail_item_count(row: sqlite3.Row | dict[str, Any] | None) -> int:
    if row is None:
        return 0
    total = 0
    for key in ("all_items_count", "done_items_count", "todo_items_count"):
        value = nullable_int(row_value(row, key))
        if value:
            total += value
    return total


def _v3_progress_has_positive_total(row_or_progress: sqlite3.Row | dict[str, Any] | None) -> bool:
    if row_or_progress is None:
        return False
    total = nullable_int(row_value(row_or_progress, "total"))
    finished = nullable_int(row_value(row_or_progress, "finished"))
    return bool((total or 0) > 0 or (finished or 0) > 0)


def _v3_progress_detail_missing(row: sqlite3.Row | dict[str, Any] | None) -> bool:
    return _v3_progress_has_positive_total(row) and _v3_snapshot_detail_item_count(row) <= 0


def get_latest_progress_for_day_payload(
    conn: sqlite3.Connection,
    day_key: str,
    trace: list[dict[str, Any]] | None = None,
    trace_prefix: str = "progress",
) -> dict[str, Any] | None:
    setup_schema(conn)
    t = _trace_step_start()
    row = conn.execute(
        """
        SELECT p.*
        FROM snapshot_progress p
        LEFT JOIN snapshots s ON s.snapshot_time=p.snapshot_time
        WHERE p.study_day_key=?
          AND COALESCE(s.api_success, 1)=1
          AND COALESCE(s.data_complete, 1)=1
        ORDER BY p.snapshot_time DESC
        LIMIT 20
        """,
        (day_key,),
    ).fetchall()
    selected = None
    for candidate in row:
        if not _v3_progress_detail_missing(candidate):
            selected = candidate
            break
    _trace_step_done(trace, f"{trace_prefix}_latest_row_query", t, found=bool(selected))
    if not selected:
        return None

    item_trace: list[dict[str, Any]] = []
    t = _trace_step_start()
    items = reconstruct_study_day_items_at(conn, day_key, selected["snapshot_time"], trace=item_trace)
    _trace_step_done(trace, f"{trace_prefix}_items_reconstruct_total", t, rows=len(items))
    if trace is not None:
        trace.extend(item_trace)
    return build_latest_progress_payload_from_items(selected, items)


def get_latest_progress_for_day(conn: sqlite3.Connection, day_key: str, trace: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    return get_latest_progress_for_day_payload(conn, day_key, trace=trace, trace_prefix="progress")


def build_dashboard_day_payload(
    conn: sqlite3.Connection,
    day: str,
    include_rows: bool = False,
    memory_thresholds_text: str | None = None,
) -> dict[str, Any]:
    day_start = time.perf_counter()
    timings: dict[str, float] = {}
    overview_trace: list[dict[str, Any]] = []
    progress_trace: list[dict[str, Any]] = []

    t0 = time.perf_counter()
    snapshot_time_value = latest_snapshot_time_for_day(conn, day)
    _trace_step_done(overview_trace, "overview_day_snapshot_lookup", t0, snapshot=snapshot_time_value)
    records = reconstruct_overview_records_at(conn, day, snapshot_time_value, trace=overview_trace) if snapshot_time_value else []
    timings["overview_rows_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    today_items = reconstruct_study_day_items_at(conn, day, snapshot_time_value, trace=overview_trace) if snapshot_time_value else []
    timings["today_items_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    summary = build_dashboard_summary_from_records(records, today_items, day, snapshot_time_value, memory_thresholds_text=memory_thresholds_text)
    timings["summary_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    progress_row = conn.execute(
        "SELECT * FROM snapshot_progress WHERE study_day_key=? AND snapshot_time=?",
        (day, snapshot_time_value),
    ).fetchone() if snapshot_time_value else None
    progress = build_latest_progress_payload_from_items(progress_row, today_items) if progress_row and not _v3_progress_detail_missing(progress_row) else None
    timings["latest_progress_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    overview_update_time = ""
    if progress and progress.get("capturedAt"):
        overview_update_time = str(progress["capturedAt"]).replace("T", " ").split("+")[0][-12:]

    study_status = build_study_status_from_raw(day, records, today_items, progress_row, snapshot_time_value)

    payload: dict[str, Any] = {
        "date": day,
        "summary": summary,
        "overviewUpdateTime": overview_update_time,
        "latestProgress": progress,
        "studyStatus": study_status,
        "timings": timings,
        "trace": {"overview": overview_trace, "progress": progress_trace},
        "rowCount": len(records),
        "todayItemCount": len(today_items),
    }
    if include_rows:
        t0 = time.perf_counter()
        today_by_key, today_by_spelling = _today_item_lookup(today_items)
        overview_rows = []
        for p in records:
            today = _today_item_for_record(p, today_by_key, today_by_spelling)
            overview_rows.append(overview_payload_to_csv_row(p, today))
        timings["overview_csv_rows_build"] = round((time.perf_counter() - t0) * 1000, 2)
        payload["overviewRows"] = overview_rows
    timings["total_day_ms"] = round((time.perf_counter() - day_start) * 1000, 2)
    return payload


def get_dashboard_data_page(
    conn: sqlite3.Connection,
    offset: int = 0,
    limit: int = 8,
    order: str = "asc",
    memory_thresholds_text: str | None = None,
) -> dict[str, Any]:
    return get_dashboard_data_page_light(conn, offset=offset, limit=limit, order=order, memory_thresholds_text=memory_thresholds_text)


def get_dashboard_data(conn: sqlite3.Connection, memory_thresholds_text: str | None = None) -> dict[str, Any]:
    page = get_dashboard_data_page_light(conn, offset=0, limit=200, order="asc", memory_thresholds_text=memory_thresholds_text)
    return {
        "success": True,
        "source": page.get("source"),
        "schemaVersion": page.get("schemaVersion"),
        "studyDayBoundaryHour": page.get("studyDayBoundaryHour"),
        "days": page.get("days") or [],
        "total": page.get("total") or 0,
        "timings": page.get("timings") or {},
    }


# ---------------------------------------------------------------------------
# Today workspace
# ---------------------------------------------------------------------------


def get_today_workspace(conn: sqlite3.Connection, day_key: str | None = None) -> dict[str, Any]:
    day_key = day_key or study_day_key_from_dt()
    rows = conn.execute(
        """
        SELECT p.*
        FROM snapshot_progress p
        LEFT JOIN snapshots s ON s.snapshot_time=p.snapshot_time
        WHERE p.study_day_key=?
          AND COALESCE(s.api_success, 1)=1
          AND COALESCE(s.data_complete, 1)=1
        ORDER BY p.snapshot_time ASC
        """,
        (day_key,),
    ).fetchall()

    snapshots: list[dict[str, Any]] = []
    all_progress_lines: list[str] = []
    skipped_missing_detail = 0

    for row in rows:
        if _v3_progress_detail_missing(row):
            skipped_missing_detail += 1
            continue

        all_items = reconstruct_study_day_items_at(conn, day_key, row["snapshot_time"])
        try:
            overview_records = reconstruct_overview_records_at(conn, day_key, row["snapshot_time"])
        except Exception:
            overview_records = []
        all_items = enrich_today_items_with_prediction_raw_data(all_items, overview_records, day_key, row["snapshot_time"], conn)
        active_items = [x for x in all_items if x.get("present_state") != "confirmed_absent"]
        done_items = [x for x in active_items if x.get("is_finished") is True]
        todo_items = [x for x in active_items if x.get("is_finished") is not True]

        progress = {"finished": row["finished"], "total": row["total"], "study_time": row["study_time"]}
        breakdown = progress_today_breakdown(all_items)
        study_status = build_study_status_from_raw(day_key, overview_records, all_items, row, row["snapshot_time"])
        today_status = study_status.get("today") or {}
        overall_status = study_status.get("overall") or {}
        critical_status = study_status.get("critical") or {}
        summary = {
            "finished": today_status.get("finished", row["finished"] or 0),
            "total": today_status.get("total", row["total"] or 0),
            "unfinished": today_status.get("unfinished", breakdown["unfinishedCount"]),
            "studyTimeMs": today_status.get("studyTimeMs", row["study_time"] or 0),
            "firstForgetDone": today_status.get("forget", breakdown["todayForgetCount"]),
            "firstForgetAll": today_status.get("allForget", breakdown["todayAllForgetCount"]),
            "forget": today_status.get("forget", breakdown["todayForgetCount"]),
            "allForget": today_status.get("allForget", breakdown["todayAllForgetCount"]),
            "doneVague": today_status.get("vague", breakdown["todayVagueCount"]),
            "allVague": today_status.get("allVague", breakdown["todayAllVagueCount"]),
            "vague": today_status.get("vague", breakdown["todayVagueCount"]),
            "familiar": today_status.get("known", breakdown["todayKnownCount"]),
            "allFamiliar": today_status.get("allKnown", breakdown["todayAllKnownCount"]),
            "known": today_status.get("known", breakdown["todayKnownCount"]),
            "allKnown": today_status.get("allKnown", breakdown["todayAllKnownCount"]),
            "newWords": today_status.get("newTotal", breakdown["dailyAllNewCount"]),
            "newTotal": today_status.get("newTotal", breakdown["dailyAllNewCount"]),
            "doneNewWords": today_status.get("newDone", breakdown["dailyNewLearnedCount"]),
            "newDone": today_status.get("newDone", breakdown["dailyNewLearnedCount"]),
            "pendingNew": today_status.get("newPending", breakdown["dailyPendingNewCount"]),
            "newPending": today_status.get("newPending", breakdown["dailyPendingNewCount"]),
            "reviewWords": today_status.get("reviewTotal", breakdown["dailyAllReviewCount"]),
            "reviewTotal": today_status.get("reviewTotal", breakdown["dailyAllReviewCount"]),
            "doneReviewWords": today_status.get("reviewDone", breakdown["dailyReviewedCount"]),
            "reviewDone": today_status.get("reviewDone", breakdown["dailyReviewedCount"]),
            "pendingReview": today_status.get("reviewPending", breakdown["dailyPendingReviewCount"]),
            "reviewPending": today_status.get("reviewPending", breakdown["dailyPendingReviewCount"]),
            "criticalDueToday": critical_status.get("dueToday", 0),
            "overdue": overall_status.get("overdue", 0),
            "totalWords": overall_status.get("totalWords", 0),
            "knownState": overall_status.get("knownState", 0),
            "vagueState": overall_status.get("vagueState", 0),
            "forgetState": overall_status.get("forgetState", 0),
            "allItemsCount": len(active_items),
            "doneItemsCount": len(done_items),
            "todoItemsCount": len(todo_items),
        }
        display_name = str(row["snapshot_time"]).replace("T", " ").split("+")[0]
        snap = {
            "name": row["snapshot_time"],
            "displayName": display_name,
            "timeMs": parse_snapshot_display_time(row["snapshot_time"]),
            "timeLabel": display_name[-12:][:8] if len(display_name) >= 8 else display_name,
            "progressJson": {"data": {"progress": progress}},
            "allItemsJson": {"data": {"today_items": all_items}},
            "doneItemsJson": {"data": {"today_items": done_items}},
            "todoItemsJson": {"data": {"today_items": todo_items}},
            "progress": progress,
            "allItems": all_items,
            "doneItems": done_items,
            "todoItems": todo_items,
            "summary": summary,
            "studyStatus": study_status,
        }
        snapshots.append(snap)
        all_progress_lines.append(
            f"{display_name} | 已完成={summary['finished']} | 总数={summary['total']} | "
            f"今日首次忘记数={summary['firstForgetDone']} | 今日全部首次忘记数={summary['firstForgetAll']} | "
            f"今日模糊数(已完成)={summary['doneVague']} | 今日模糊数(全部)={summary['allVague']} | "
            f"今日认识数={summary['familiar']} | 今日新学数={summary['newWords']} | "
            f"今日复习数={summary['reviewWords']} | 今日待新学数={summary['pendingNew']} | "
            f"未完成={summary['unfinished']} | 记忆临界点={summary['criticalDueToday']} | 逾期={summary['overdue']} | "
            f"学习时长={summary['studyTimeMs'] / 1000:.0f}秒"
        )

    error = "" if snapshots else f"未读取到 {day_key} 的有效数据库快照"
    if skipped_missing_detail and snapshots:
        error = f"已跳过 {skipped_missing_detail} 个缺少今日单词明细的快照"
    return {
        "date": day_key,
        "snapshots": snapshots,
        "allProgressText": "\n".join(all_progress_lines),
        "error": error,
        "skippedMissingDetailSnapshots": skipped_missing_detail,
    }


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def _diff_overview_removed_items(
    conn: sqlite3.Connection,
    previous_day_key: str,
    previous_snapshot_time: str,
    current_day_key: str,
    current_snapshot_time: str,
) -> list[dict[str, Any]]:
    try:
        previous_records = reconstruct_overview_records_at(conn, previous_day_key, previous_snapshot_time)
        current_records = reconstruct_overview_records_at(conn, current_day_key, current_snapshot_time)
    except Exception:
        return []
    current_keys = {str(x.get("record_key") or record_key(x)) for x in current_records}
    removed = []
    for payload in previous_records:
        key = str(payload.get("record_key") or record_key(payload))
        if key not in current_keys:
            removed.append(_overview_alert_item(payload))
    return sorted(removed, key=lambda x: (x.get("word") or "", x.get("nextStudyDate") or ""))


def _v3_today_missing_items_at_snapshot(conn: sqlite3.Connection, study_day_key: str, snapshot_time: str) -> list[dict[str, Any]]:
    removed_ids: set[str] = set()
    narrow_rows = conn.execute(
        """
        SELECT voc_id
        FROM compact_study_day_patch_events
        WHERE study_day_key=? AND snapshot_time=? AND event_type='record_removed'
        ORDER BY id ASC
        """,
        (study_day_key, snapshot_time),
    ).fetchall()
    removed_ids.update(str(r["voc_id"] or "").strip() for r in narrow_rows if str(r["voc_id"] or "").strip())

    if not removed_ids:
        return []

    prev = conn.execute(
        """
        SELECT snapshot_time
        FROM snapshots
        WHERE study_day_key=? AND snapshot_time<? AND COALESCE(api_success,1)=1 AND COALESCE(data_complete,1)=1
        ORDER BY snapshot_time DESC
        LIMIT 1
        """,
        (study_day_key, snapshot_time),
    ).fetchone()
    previous_items = reconstruct_study_day_items_at(conn, study_day_key, str(prev["snapshot_time"])) if prev else []
    previous_by_id = {str(x.get("voc_id") or "").strip(): x for x in previous_items if str(x.get("voc_id") or "").strip()}

    out_by_id: dict[str, dict[str, Any]] = {}
    for vid in removed_ids:
        item = previous_by_id.get(vid, {"voc_id": vid})
        out_by_id[vid] = {
            "word": item.get("voc_spelling") or "",
            "vocId": vid,
            "order": item.get("order"),
            "firstResponse": item.get("first_response") or "",
            "isNew": item.get("is_new"),
            "isFinished": item.get("is_finished"),
            "presentState": "missing_candidate",
        }

    out = list(out_by_id.values())
    out.sort(key=lambda x: (x.get("order") is None, x.get("order") if x.get("order") is not None else 10**12, str(x.get("word") or "").lower()))
    return out


def get_latest_decrease_alerts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    setup_schema(conn)
    row = conn.execute(
        """
        SELECT snapshot_time, study_day_key
        FROM snapshots
        WHERE COALESCE(data_complete, 1)=1 AND COALESCE(api_success, 1)=1
        ORDER BY snapshot_time DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return []
    snap_time = row["snapshot_time"]
    study_day_key = row["study_day_key"]
    alerts: list[dict[str, Any]] = []

    if _compact_table_exists(conn, "overview_snapshot_metrics"):
        current_overview = conn.execute(
            "SELECT total_count, record_count FROM overview_snapshot_metrics WHERE snapshot_time=?",
            (snap_time,),
        ).fetchone()
        previous_overview = conn.execute(
            """
            SELECT snapshot_time, study_day_key, total_count, record_count
            FROM overview_snapshot_metrics
            WHERE snapshot_time < ?
            ORDER BY snapshot_time DESC
            LIMIT 1
            """,
            (snap_time,),
        ).fetchone()
        if current_overview and previous_overview:
            current_count = _int_or_none(current_overview["total_count"])
            previous_count = _int_or_none(previous_overview["total_count"])
            if current_count is not None and previous_count is not None and current_count < previous_count:
                delta = previous_count - current_count
                removed_items, omitted = _limit_alert_items(
                    _diff_overview_removed_items(
                        conn,
                        str(previous_overview["study_day_key"]),
                        str(previous_overview["snapshot_time"]),
                        str(study_day_key),
                        str(snap_time),
                    )
                )
                alerts.append({
                    "type": "overview_count_decreased",
                    "snapshotTime": snap_time,
                    "studyDayKey": study_day_key,
                    "previousSnapshotTime": previous_overview["snapshot_time"],
                    "previousStudyDayKey": previous_overview["study_day_key"],
                    "previousCount": previous_count,
                    "currentCount": current_count,
                    "count": delta,
                    "items": removed_items,
                    "itemsOmitted": omitted,
                    "message": f"最新快照发现总览单词比上次减少 {delta} 个。是否是你主动删除了这些单词？",
                })

    current_progress = conn.execute("SELECT all_items_count, total FROM snapshot_progress WHERE snapshot_time=?", (snap_time,)).fetchone()
    previous_progress = conn.execute(
        """
        SELECT snapshot_time, all_items_count, total
        FROM snapshot_progress
        WHERE study_day_key=? AND snapshot_time < ?
        ORDER BY snapshot_time DESC
        LIMIT 1
        """,
        (study_day_key, snap_time),
    ).fetchone()
    if current_progress and previous_progress:
        current_items = _int_or_none(current_progress["all_items_count"])
        previous_items = _int_or_none(previous_progress["all_items_count"])
        if current_items is not None and previous_items is not None and current_items < previous_items:
            delta = previous_items - current_items
            missing_items, omitted = _limit_alert_items(_v3_today_missing_items_at_snapshot(conn, str(study_day_key), str(snap_time)))
            alerts.append({
                "type": "today_item_count_decreased",
                "snapshotTime": snap_time,
                "studyDayKey": study_day_key,
                "previousSnapshotTime": previous_progress["snapshot_time"],
                "previousCount": previous_items,
                "currentCount": current_items,
                "count": delta,
                "items": missing_items,
                "itemsOmitted": omitted,
                "message": f"最新快照发现今日学习单词比上次少 {delta} 个。是否是你主动调整/删除了今日任务？",
            })
    return alerts


# ---------------------------------------------------------------------------
# Word query APIs
# ---------------------------------------------------------------------------


def query_words(conn: sqlite3.Connection, params: dict[str, Any]) -> dict[str, Any]:
    setup_schema(conn)
    total_start = time.perf_counter()
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    day_key = str(params.get("date") or latest_day(conn) or "")
    snapshot_time_value = latest_snapshot_time_for_day(conn, day_key) if day_key else None
    timings["resolve_day_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    if not day_key or not snapshot_time_value:
        return {"success": True, "date": day_key, "total": 0, "page": 1, "pageSize": 1000, "items": [], "availableColumns": [], "timings": timings}

    q = str(params.get("q") or "").strip().lower()
    state = str(params.get("state") or "").strip()
    tag = str(params.get("tag") or "").strip()
    min_count = nullable_float(params.get("minStudyCount"))
    max_count = nullable_float(params.get("maxStudyCount"))
    next_from = str(params.get("nextFrom") or "").strip()
    next_to = str(params.get("nextTo") or "").strip()
    add_from = str(params.get("addFrom") or "").strip()
    add_to = str(params.get("addTo") or "").strip()
    due = str(params.get("due") or "").strip()
    overdue_days = nullable_int(params.get("overdueDays"))
    today_filter = str(params.get("todayFilter") or "").strip()
    reference_iso = f"{day_key}T12:00:00+08:00"
    sort = str(params.get("sort") or "next_study_date")
    reverse = str(params.get("dir") or "asc").lower() == "desc"
    page_size_raw = str(params.get("pageSize") or "1000").strip().lower()
    page = max(1, nullable_int(params.get("page")) or 1)

    where: list[str] = ["study_day_key=?"]
    args: list[Any] = [day_key]
    if q:
        where.append("lower(spelling) LIKE ?")
        args.append(f"%{q}%")
    if state:
        if state == "逾期":
            where.append("is_overdue='是'")
        else:
            where.append("current_state=?")
            args.append(state)
    if tag == "熟知":
        where.append("tag_well='是'")
    if tag == "顽固":
        where.append("tag_sticking='是'")
    if min_count is not None:
        where.append("study_count>=?")
        args.append(min_count)
    if max_count is not None:
        where.append("study_count<=?")
        args.append(max_count)
    if next_from:
        where.append("substr(COALESCE(next_study_date,''),1,10)>=?")
        args.append(next_from)
    if next_to:
        where.append("substr(COALESCE(next_study_date,''),1,10)<=?")
        args.append(next_to)
    if add_from:
        where.append("substr(COALESCE(add_date,''),1,10)>=?")
        args.append(add_from)
    if add_to:
        where.append("substr(COALESCE(add_date,''),1,10)<=?")
        args.append(add_to)
    if due:
        if due == "today":
            where.append("substr(COALESCE(next_study_date,''),1,10)=?")
            args.append(day_key)
        elif due == "tomorrow":
            tomorrow = (datetime.strptime(day_key, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
            where.append("substr(COALESCE(next_study_date,''),1,10)=?")
            args.append(tomorrow)
        elif due == "next7":
            end_day = (datetime.strptime(day_key, "%Y-%m-%d").date() + timedelta(days=7)).isoformat()
            where.append("substr(COALESCE(next_study_date,''),1,10) BETWEEN ? AND ?")
            args.extend([day_key, end_day])
        elif due == "overdue":
            where.append("substr(COALESCE(next_study_date,''),1,10)<?")
            args.append(day_key)
        elif due == "overdue_gt":
            n = max(0, int(overdue_days or 0))
            cutoff = (datetime.strptime(day_key, "%Y-%m-%d").date() - timedelta(days=n)).isoformat()
            where.append("substr(COALESCE(next_study_date,''),1,10)<=?" if n else "substr(COALESCE(next_study_date,''),1,10)<?")
            args.append(cutoff if n else day_key)

    sort_map = {
        "word": "spelling COLLATE NOCASE",
        "study_count": "study_count",
        "add_date": "add_date",
        "first_study_date": "first_study_date",
        "last_study_date": "last_study_date",
        "next_study_date": "next_study_date",
        "current_state": "current_state",
        "overdue_first": "CASE WHEN is_overdue='是' THEN 0 ELSE 1 END",
    }
    where_sql = " WHERE " + " AND ".join(where)
    sql_sort = sort_map.get(sort, "next_study_date")
    direction = "DESC" if reverse else "ASC"
    today_items = reconstruct_study_day_items_at(conn, day_key, snapshot_time_value)
    today_by_key, today_by_spelling = _today_item_lookup(today_items)

    def available_columns() -> list[str]:
        return ["word", "currentState", "isOverdue", "studyCount", "addDate", "firstStudyDate", "lastStudyDate", "nextStudyDate", "lastResponse", "tags", "reviewSpanDays", "criticalDays"]

    def item_from_payload(p: dict[str, Any], today_item: dict[str, Any] | None) -> dict[str, Any]:
        next_date = p.get("next_study_date") or ""
        return {
            "word": p.get("spelling") or "",
            "vocId": p.get("voc_id") or "",
            "recordKey": p.get("voc_id") or "",
            "currentState": p.get("current_state") or "",
            "isOverdue": p.get("is_overdue") or "",
            "studyCount": p.get("study_count"),
            "addDate": _date_only(p.get("add_date")) or (p.get("add_date") or ""),
            "firstStudyDate": _date_only(p.get("first_study_date")) or (p.get("first_study_date") or ""),
            "lastStudyDate": _date_only(p.get("last_study_date")) or (p.get("last_study_date") or ""),
            "nextStudyDate": _date_only(next_date) or next_date,
            "lastResponse": p.get("last_response_cn") or "",
            "tags": p.get("tags_text") or "",
            "reviewSpanDays": _date_diff_days(p.get("next_study_date"), p.get("last_study_date")),
            "criticalDays": _date_diff_days(p.get("next_study_date"), reference_iso),
            "todayOrder": today_item.get("order") if today_item else None,
            "todayFirstResponse": response_cn(today_item.get("first_response")) if today_item else "",
            "todayIsNew": today_item.get("is_new") if today_item else None,
            "todayIsFinished": today_item.get("is_finished") if today_item else None,
            "todayPresentState": today_item.get("present_state") if today_item else "",
        }

    if not today_filter and sort in sort_map:
        t = time.perf_counter()
        total = int(conn.execute(f"SELECT COUNT(*) AS c FROM compact_overview_day_records{where_sql}", args).fetchone()["c"] or 0)
        page_size = max(1, total or 1) if page_size_raw == "all" else max(1, min(nullable_int(page_size_raw) or 1000, 50000))
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""
            SELECT *
            FROM compact_overview_day_records
            {where_sql}
            ORDER BY {sql_sort} {direction}, spelling COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
            """,
            [*args, page_size, offset],
        ).fetchall()
        timings["compact_v3_sql_filter_page_ms"] = round((time.perf_counter() - t) * 1000, 2)
        items = []
        for r in rows:
            p = _v3_overview_payload(r)
            items.append(item_from_payload(p, _today_item_for_record(p, today_by_key, today_by_spelling)))
    else:
        t = time.perf_counter()
        rows = conn.execute(f"SELECT * FROM compact_overview_day_records{where_sql}", args).fetchall()
        records = [_v3_overview_payload(r) for r in rows]
        items = []
        for p in records:
            today_item = _today_item_for_record(p, today_by_key, today_by_spelling)
            if not _matches_today_filter(today_item, today_filter):
                continue
            items.append(item_from_payload(p, today_item))
        items.sort(key=lambda x: (_sort_value(x, sort), x.get("word") or ""), reverse=reverse)
        total = len(items)
        page_size = max(1, total or 1) if page_size_raw == "all" else max(1, min(nullable_int(page_size_raw) or 1000, 50000))
        offset = (page - 1) * page_size
        items = items[offset:offset + page_size]
        timings["compact_v3_filter_page_ms"] = round((time.perf_counter() - t) * 1000, 2)

    timings["total_db_ms"] = round((time.perf_counter() - total_start) * 1000, 2)
    return {
        "success": True,
        "date": day_key,
        "snapshotTime": snapshot_time_value,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "items": items,
        "todayItemCount": len(today_items),
        "recordCount": total,
        "timings": timings,
        "availableColumns": available_columns(),
    }


def get_local_word_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    setup_schema(conn)
    day = latest_day(conn)
    if not day:
        return {}
    rows = conn.execute(
        """
        SELECT *
        FROM compact_overview_day_records
        WHERE study_day_key=? AND spelling IS NOT NULL AND spelling<>''
        ORDER BY spelling COLLATE NOCASE
        """,
        (day,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = str(r["spelling"] or "").strip().lower()
        if not key or key in out:
            continue
        out[key] = {
            "word": r["spelling"] or "",
            "vocId": r["voc_id"] or "",
            "recordKey": r["voc_id"] or "",
            "currentState": r["current_state"] or "",
            "isOverdue": r["is_overdue"] or "",
            "studyCount": r["study_count"],
            "nextStudyDate": r["next_study_date"] or "",
            "tags": r["tags_text"] or "",
        }
    return out


def get_current_records_by_words(conn: sqlite3.Connection, words: list[str]) -> list[dict[str, Any]]:
    setup_schema(conn)
    day = latest_day(conn)
    if not day:
        return []
    wanted: list[str] = []
    seen = set()
    for w in words:
        k = str(w or "").strip().lower()
        if k and k not in seen:
            wanted.append(k)
            seen.add(k)
    if not wanted:
        return []
    placeholders = ",".join(["?"] * len(wanted))
    rows = conn.execute(
        f"""
        SELECT *
        FROM compact_overview_day_records
        WHERE study_day_key=? AND lower(spelling) IN ({placeholders})
        """,
        [day, *wanted],
    ).fetchall()
    by_word = {str(r["spelling"] or "").strip().lower(): r for r in rows}
    out = []
    for k in wanted:
        r = by_word.get(k)
        if not r:
            continue
        out.append({
            "word": r["spelling"] or "",
            "vocId": r["voc_id"] or "",
            "recordKey": r["voc_id"] or "",
            "currentState": r["current_state"] or "",
            "isOverdue": r["is_overdue"] or "",
            "studyCount": r["study_count"],
            "nextStudyDate": r["next_study_date"] or "",
            "tags": r["tags_text"] or "",
        })
    return out


# ---------------------------------------------------------------------------
# Import/write path
# ---------------------------------------------------------------------------


def _v3_upsert_word(conn: sqlite3.Connection, *, voc_id: str | None, spelling: str | None, captured_iso: str) -> None:
    vid = str(voc_id or "").strip()
    if not vid:
        return
    conn.execute(
        """
        INSERT INTO words(voc_id, spelling, first_seen_at, last_seen_at)
        VALUES(?,?,?,?)
        ON CONFLICT(voc_id) DO UPDATE SET
          spelling=COALESCE(excluded.spelling, words.spelling),
          last_seen_at=excluded.last_seen_at
        """,
        (vid, spelling, captured_iso, captured_iso),
    )


def _v3_has_detail_today_items(*item_lists: list[dict[str, Any]] | None) -> bool:
    return any(bool(x) for x in item_lists if x is not None)


def _v3_insert_snapshot_progress(
    conn: sqlite3.Connection,
    *,
    snapshot_time: str,
    study_day_key: str,
    progress: dict[str, Any],
    all_list: list[dict[str, Any]],
    done_list: list[dict[str, Any]],
    todo_list: list[dict[str, Any]],
) -> None:
    p = extract_progress(progress)
    has_detail_items = _v3_has_detail_today_items(all_list, done_list, todo_list)

    if has_detail_items:
        first_forget_done = count_response(done_list, "FORGET")
        first_forget_all = count_response(all_list, "FORGET")
        vague_done = count_response(done_list, "VAGUE")
        vague_all = count_response(all_list, "VAGUE")
        familiar_done = count_response(done_list, "FAMILIAR")
        familiar_all = count_response(all_list, "FAMILIAR")
        new_words = count_new(all_list)
        review_words = count_review(all_list)
        pending_new_words = count_pending_new(all_list)
        all_items_count = len(all_list)
        done_items_count = len(done_list)
        todo_items_count = len(todo_list)
    else:
        first_forget_done = None
        first_forget_all = None
        vague_done = None
        vague_all = None
        familiar_done = None
        familiar_all = None
        new_words = None
        review_words = None
        pending_new_words = None
        all_items_count = 0
        done_items_count = 0
        todo_items_count = 0

    values = (
        snapshot_time,
        study_day_key,
        nullable_int(p.get("finished")),
        nullable_int(p.get("total")),
        nullable_int(p.get("study_time")),
        first_forget_done,
        first_forget_all,
        vague_done,
        vague_all,
        familiar_done,
        familiar_all,
        new_words,
        review_words,
        pending_new_words,
        all_items_count,
        done_items_count,
        todo_items_count,
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO snapshot_progress(
          snapshot_time, study_day_key, finished, total, study_time,
          first_forget_done, first_forget_all, vague_done, vague_all,
          familiar_done, familiar_all, new_words, review_words, pending_new_words,
          all_items_count, done_items_count, todo_items_count
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        values,
    )


def _v3_normalized_today_payloads(all_list: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for item in all_list:
        p = item_payload(item)
        vid = str(p.get("voc_id") or "").strip()
        if not vid:
            continue
        p["state_hash"] = item_state_hash(p)
        payloads[vid] = p
    return payloads


def _v3_insert_study_snapshot(
    conn: sqlite3.Connection,
    *,
    study_day_key: str,
    snapshot_time: str,
    all_list: list[dict[str, Any]],
    captured_iso: str,
) -> int:
    payloads = _v3_normalized_today_payloads(all_list)
    for p in payloads.values():
        _v3_upsert_word(conn, voc_id=p.get("voc_id"), spelling=p.get("voc_spelling"), captured_iso=captured_iso)

    has_initial = conn.execute("SELECT 1 FROM compact_study_day_initial_items WHERE study_day_key=? LIMIT 1", (study_day_key,)).fetchone() is not None
    if not has_initial:
        for p in payloads.values():
            conn.execute(
                """
                INSERT INTO compact_study_day_initial_items(
                  study_day_key, snapshot_time, voc_id, voc_spelling, order_index,
                  first_response, is_new, is_finished, present_state, state_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    study_day_key,
                    snapshot_time,
                    p.get("voc_id"),
                    p.get("voc_spelling"),
                    p.get("order_index"),
                    p.get("first_response"),
                    p.get("is_new"),
                    p.get("is_finished"),
                    p.get("present_state"),
                    p.get("state_hash"),
                ),
            )
        return len(payloads)

    prev_snapshot = conn.execute(
        """
        SELECT snapshot_time
        FROM snapshots
        WHERE study_day_key=? AND snapshot_time<? AND COALESCE(data_complete,1)=1 AND COALESCE(api_success,1)=1
        ORDER BY snapshot_time DESC
        LIMIT 1
        """,
        (study_day_key, snapshot_time),
    ).fetchone()
    previous_ref_snapshot = prev_snapshot["snapshot_time"] if prev_snapshot else snapshot_time
    previous_items = reconstruct_study_day_items_at(conn, study_day_key, previous_ref_snapshot)
    prev_by_id: dict[str, dict[str, Any]] = {}
    for it in previous_items:
        vid = str(it.get("voc_id") or "").strip()
        if not vid:
            continue
        prev_by_id[vid] = {
            "voc_id": vid,
            "voc_spelling": it.get("voc_spelling"),
            "order_index": it.get("order"),
            "first_response": it.get("first_response"),
            "is_new": None if it.get("is_new") is None else int(bool(it.get("is_new"))),
            "is_finished": None if it.get("is_finished") is None else int(bool(it.get("is_finished"))),
            "present_state": it.get("present_state"),
        }

    changes = 0
    current_ids = set(payloads)
    for vid, p in payloads.items():
        prev = prev_by_id.get(vid)
        if prev is None:
            _insert_patch_event(
                conn,
                "compact_study_day_patch_events",
                STUDY_ITEM_EVENT_FIELDS,
                snapshot_time=snapshot_time,
                study_day_key=study_day_key,
                voc_id=vid,
                event_type="record_inserted",
                values={field: p.get(field) for field in STUDY_ITEM_EVENT_FIELDS},
                created_at=captured_iso,
            )
            changes += 1
            continue

        changed = {field: p.get(field) for field in STUDY_ITEM_EVENT_FIELDS if prev.get(field) != p.get(field)}
        if changed:
            _insert_patch_event(
                conn,
                "compact_study_day_patch_events",
                STUDY_ITEM_EVENT_FIELDS,
                snapshot_time=snapshot_time,
                study_day_key=study_day_key,
                voc_id=vid,
                event_type="patch",
                values=changed,
                created_at=captured_iso,
            )
            changes += 1

    for vid in prev_by_id:
        if vid in current_ids:
            continue
        _insert_patch_event(
            conn,
            "compact_study_day_patch_events",
            STUDY_ITEM_EVENT_FIELDS,
            snapshot_time=snapshot_time,
            study_day_key=study_day_key,
            voc_id=vid,
            event_type="record_removed",
            values={},
            created_at=captured_iso,
        )
        changes += 1
    _RECONSTRUCT_CACHE.clear()
    return changes


def _v3_insert_overview_day(
    conn: sqlite3.Connection,
    *,
    study_day_key: str,
    snapshot_time: str,
    records: list[dict[str, Any]],
    captured_iso: str,
) -> int:
    payloads: dict[str, dict[str, Any]] = {}
    for record in records:
        p = record_payload(record, study_day_key)
        vid = str(p.get("voc_id") or "").strip()
        if not vid:
            continue
        p["state_hash"] = record_state_hash(p)
        payloads[vid] = p
        _v3_upsert_word(conn, voc_id=vid, spelling=p.get("spelling"), captured_iso=captured_iso)

    previous_rows = conn.execute("SELECT * FROM compact_overview_day_records WHERE study_day_key=?", (study_day_key,)).fetchall()
    previous = {str(r["voc_id"]): r for r in previous_rows if r["voc_id"]}
    had_day_baseline = bool(previous)
    changes = 0

    if had_day_baseline:
        for vid in sorted(set(previous) - set(payloads)):
            _insert_patch_event(
                conn,
                "compact_overview_patch_events",
                OVERVIEW_EVENT_FIELDS,
                snapshot_time=snapshot_time,
                study_day_key=study_day_key,
                voc_id=vid,
                event_type="record_removed",
                values={},
                old_values={field: row_value(previous[vid], field) for field in OVERVIEW_EVENT_FIELDS},
                created_at=captured_iso,
            )
            changes += 1

        for vid, p in payloads.items():
            prev = previous.get(vid)
            if prev is None:
                _insert_patch_event(
                    conn,
                    "compact_overview_patch_events",
                    OVERVIEW_EVENT_FIELDS,
                    snapshot_time=snapshot_time,
                    study_day_key=study_day_key,
                    voc_id=vid,
                    event_type="record_inserted",
                    values={field: p.get(field) for field in OVERVIEW_EVENT_FIELDS},
                    old_values={field: None for field in OVERVIEW_EVENT_FIELDS},
                    created_at=captured_iso,
                )
                changes += 1
                continue

            changed = {field: p.get(field) for field in OVERVIEW_EVENT_FIELDS if row_value(prev, field) != p.get(field)}
            if changed:
                _insert_patch_event(
                    conn,
                    "compact_overview_patch_events",
                    OVERVIEW_EVENT_FIELDS,
                    snapshot_time=snapshot_time,
                    study_day_key=study_day_key,
                    voc_id=vid,
                    event_type="patch",
                    values=changed,
                    old_values={field: row_value(prev, field) for field in OVERVIEW_EVENT_FIELDS},
                    created_at=captured_iso,
                )
                changes += 1

    conn.execute("DELETE FROM compact_overview_day_records WHERE study_day_key=?", (study_day_key,))
    for p in payloads.values():
        conn.execute(
            """
            INSERT INTO compact_overview_day_records(
              study_day_key, snapshot_time, voc_id, spelling, add_date, first_study_date,
              last_study_date, next_study_date, last_response, last_response_cn, study_count,
              tags_text, tag_well, tag_sticking, current_state, is_overdue, state_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                study_day_key,
                snapshot_time,
                p.get("voc_id"),
                p.get("spelling"),
                p.get("add_date"),
                p.get("first_study_date"),
                p.get("last_study_date"),
                p.get("next_study_date"),
                p.get("last_response"),
                p.get("last_response_cn"),
                p.get("study_count"),
                p.get("tags_text"),
                p.get("tag_well"),
                p.get("tag_sticking"),
                p.get("current_state"),
                p.get("is_overdue"),
                p.get("state_hash"),
            ),
        )

    conn.execute(
        """
        INSERT INTO compact_overview_day_checkpoints(study_day_key, snapshot_time, record_count, created_at)
        VALUES(?,?,?,?)
        ON CONFLICT(study_day_key) DO UPDATE SET
          snapshot_time=excluded.snapshot_time,
          record_count=excluded.record_count,
          created_at=excluded.created_at
        """,
        (study_day_key, snapshot_time, len(payloads), captured_iso),
    )

    if _compact_table_exists(conn, "overview_snapshot_metrics"):
        counts = {"total": len(payloads), "known": 0, "vague": 0, "forget": 0, "overdue": 0, "well": 0, "sticking": 0}
        for p in payloads.values():
            state = str(p.get("current_state") or "")
            if state == "认识":
                counts["known"] += 1
            elif state == "模糊":
                counts["vague"] += 1
            elif state == "忘记":
                counts["forget"] += 1
            if p.get("is_overdue") == "是":
                counts["overdue"] += 1
            if p.get("tag_well") == "是":
                counts["well"] += 1
            if p.get("tag_sticking") == "是":
                counts["sticking"] += 1
        conn.execute(
            """
            INSERT OR REPLACE INTO overview_snapshot_metrics(
              snapshot_time, study_day_key, month_key, total_count, known_count, vague_count, forget_count,
              overdue_count, well_known_count, sticking_count, record_count
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot_time,
                study_day_key,
                study_day_key[:7],
                counts["total"],
                counts["known"],
                counts["vague"],
                counts["forget"],
                counts["overdue"],
                counts["well"],
                counts["sticking"],
                len(payloads),
            ),
        )
    _RECONSTRUCT_CACHE.clear()
    return changes


def import_api_snapshot(
    conn: sqlite3.Connection,
    *,
    progress: dict[str, Any],
    all_items: dict[str, Any],
    done_items: dict[str, Any] | None = None,
    todo_items: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
    expected_total: int = 0,
    leaf_ranges: int = 0,
    captured_at: datetime | None = None,
    source: str = "api",
) -> ImportResult:
    setup_schema(conn)
    captured_at = captured_at or now_bj()
    study_day_key = study_day_key_from_dt(captured_at)
    snap_time = iso_bj(captured_at, milliseconds=True)
    snap_name = snapshot_name(captured_at)
    created_iso = iso_bj()

    all_list = extract_today_items(all_items)
    done_list = extract_today_items(done_items)
    todo_list = extract_today_items(todo_items)
    records = records or []

    if not all_list and (done_list or todo_list):
        all_list = list(done_list) + list(todo_list)

    p = extract_progress(progress)
    progress_total = nullable_int(p.get("total")) or 0
    progress_finished = nullable_int(p.get("finished")) or 0
    has_detail_items = _v3_has_detail_today_items(all_list, done_list, todo_list)

    # Dangerous 04:00/rollover guard: progress can be positive while today_items
    # is empty. In that case, write only an incomplete marker and do not touch
    # snapshot_progress, today-item tables, or overview checkpoints.
    if not has_detail_items and (progress_total > 0 or progress_finished > 0):
        reason = "today_items_empty_but_progress_present"
        conn.execute(
            """
            INSERT INTO snapshots(snapshot_time, study_day_key, source, api_success, data_complete, incomplete_reason, created_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_time) DO UPDATE SET
              study_day_key=excluded.study_day_key,
              source=excluded.source,
              api_success=excluded.api_success,
              data_complete=excluded.data_complete,
              incomplete_reason=excluded.incomplete_reason
            """,
            (snap_time, study_day_key, source, 0, 0, reason, created_iso),
        )
        conn.execute("INSERT OR REPLACE INTO compact_meta(key,value) VALUES(?,?)", ("schema_version", COMPACT_SCHEMA_VERSION))
        conn.execute("INSERT OR REPLACE INTO compact_meta(key,value) VALUES(?,?)", ("last_sync_at", created_iso))
        conn.execute("PRAGMA optimize")
        _RECONSTRUCT_CACHE.clear()
        return ImportResult(
            snapshot_id=0,
            snapshot_time=snap_time,
            day_key=study_day_key,
            snapshot_name=snap_name,
            inserted_records=0,
            today_item_changes=0,
            overview_changes=0,
        )

    conn.execute(
        """
        INSERT INTO snapshots(snapshot_time, study_day_key, source, api_success, data_complete, incomplete_reason, created_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(snapshot_time) DO UPDATE SET
          study_day_key=excluded.study_day_key,
          source=excluded.source,
          api_success=excluded.api_success,
          data_complete=excluded.data_complete,
          incomplete_reason=excluded.incomplete_reason
        """,
        (snap_time, study_day_key, source, 1, 1, None, created_iso),
    )

    _v3_insert_snapshot_progress(
        conn,
        snapshot_time=snap_time,
        study_day_key=study_day_key,
        progress=progress,
        all_list=all_list,
        done_list=done_list,
        todo_list=todo_list,
    )

    today_changes = _v3_insert_study_snapshot(
        conn,
        study_day_key=study_day_key,
        snapshot_time=snap_time,
        all_list=all_list,
        captured_iso=created_iso,
    ) if has_detail_items else 0

    overview_changes = _v3_insert_overview_day(
        conn,
        study_day_key=study_day_key,
        snapshot_time=snap_time,
        records=records,
        captured_iso=created_iso,
    ) if records else 0

    conn.execute("INSERT OR REPLACE INTO compact_meta(key,value) VALUES(?,?)", ("schema_version", COMPACT_SCHEMA_VERSION))
    conn.execute("INSERT OR REPLACE INTO compact_meta(key,value) VALUES(?,?)", ("last_sync_at", created_iso))
    conn.execute("PRAGMA optimize")
    _RECONSTRUCT_CACHE.clear()

    return ImportResult(
        snapshot_id=0,
        snapshot_time=snap_time,
        day_key=study_day_key,
        snapshot_name=snap_name,
        inserted_records=len(records),
        today_item_changes=today_changes,
        overview_changes=overview_changes,
    )


# ---------------------------------------------------------------------------
# Light dashboard route
# ---------------------------------------------------------------------------


def _light_overview_records_for_day(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM compact_overview_day_records
        WHERE study_day_key=?
        ORDER BY next_study_date, add_date, spelling COLLATE NOCASE
        """,
        (day,),
    ).fetchall()
    return [_v3_overview_payload(row) for row in rows]


def _light_latest_progress_row_for_day(conn: sqlite3.Connection, day: str, snapshot_time: str | None) -> sqlite3.Row | None:
    if snapshot_time:
        row = conn.execute(
            """
            SELECT p.*
            FROM snapshot_progress p
            JOIN snapshots s ON s.snapshot_time=p.snapshot_time
            WHERE p.study_day_key=?
              AND p.snapshot_time=?
              AND COALESCE(s.data_complete, 1)=1
              AND COALESCE(s.api_success, 1)=1
            LIMIT 1
            """,
            (day, snapshot_time),
        ).fetchone()
        if row and not _v3_progress_detail_missing(row):
            return row

    rows = conn.execute(
        """
        SELECT p.*
        FROM snapshot_progress p
        JOIN snapshots s ON s.snapshot_time=p.snapshot_time
        WHERE p.study_day_key=?
          AND COALESCE(s.data_complete, 1)=1
          AND COALESCE(s.api_success, 1)=1
        ORDER BY p.snapshot_time DESC
        LIMIT 20
        """,
        (day,),
    ).fetchall()
    for row in rows:
        if not _v3_progress_detail_missing(row):
            return row
    return None


def _light_int(value: Any, fallback: int = 0) -> int:
    try:
        if value is None:
            return fallback
        return int(value)
    except Exception:
        return fallback


def _light_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        x = float(value)
        return x if x == x else None
    except Exception:
        return None


def _light_memory_int_floor(value: float) -> int:
    return int(value // 1)


def _light_memory_int_ceil(value: float) -> int:
    i = int(value // 1)
    return i if value == i else i + 1


def _light_memory_integer_bounds(spec: dict[str, Any]) -> tuple[int | None, int | None]:
    lower = spec.get("lower", float("-inf"))
    upper = spec.get("upper", float("inf"))

    if lower == float("-inf"):
        lo = None
    elif spec.get("lowerInclusive", True):
        lo = _light_memory_int_ceil(float(lower))
    else:
        lo = _light_memory_int_floor(float(lower)) + 1

    if upper == float("inf"):
        hi = None
    elif spec.get("upperInclusive", True):
        hi = _light_memory_int_floor(float(upper))
    else:
        hi = _light_memory_int_ceil(float(upper)) - 1

    return lo, hi


def _light_memory_prefix(bucket: list[int | float]) -> list[int | float]:
    prefix: list[int | float] = [0] * (len(bucket) + 1)
    running: int | float = 0
    for i, value in enumerate(bucket):
        running += value
        prefix[i + 1] = running
    return prefix


def _light_memory_prefix_range(prefix: list[int | float], offset: int, lo: int | None, hi: int | None) -> int | float:
    if not prefix:
        return 0

    n = len(prefix) - 1
    first_value = -offset
    last_value = n - offset - 1

    left_value = first_value if lo is None else max(lo, first_value)
    right_value = last_value if hi is None else min(hi, last_value)
    if right_value < left_value:
        return 0

    left_idx = left_value + offset
    right_idx_exclusive = right_value + offset + 1
    return prefix[right_idx_exclusive] - prefix[left_idx]


def _light_empty_memory_stats(specs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "counts": {str(spec["id"]): 0 for spec in specs},
        "avgs": {str(spec["id"]): None for spec in specs},
    }


def _light_sql_placeholders(items: list[Any]) -> str:
    return ",".join("?" for _ in items)


def _light_page_memory_stats(
    conn: sqlite3.Connection,
    days: list[str],
    specs: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    if not days:
        return {}

    empty = {
        day: {
            "memoryReviewSpanStats": _light_empty_memory_stats(specs),
            "memoryCriticalStats": _light_empty_memory_stats(specs),
            "memoryOverdueStats": _light_empty_memory_stats(specs),
            "criticalStatus": {"dueToday": 0, "dueByOffset": {}},
            "dailyAllReviewAvgStudyCount": None,
        }
        for day in days
    }
    if not specs:
        return empty

    ph = _light_sql_placeholders(days)
    rows = conn.execute(
        f"""
        SELECT
          study_day_key,
          study_count,
          CAST(
            julianday(date(next_study_date, '+8 hours'))
            - julianday(date(last_study_date, '+8 hours'))
            AS INTEGER
          ) AS review_span_days,
          CAST(
            julianday(date(next_study_date, '+8 hours'))
            - julianday(study_day_key)
            AS INTEGER
          ) AS critical_days
        FROM compact_overview_day_records
        WHERE study_day_key IN ({ph})
        ORDER BY study_day_key
        """,
        list(days),
    ).fetchall()
    if not rows:
        return empty

    min_daycount = 0
    max_daycount = 0
    values_seen = False
    for row in rows:
        review_days = row["review_span_days"]
        critical_days = row["critical_days"]

        if review_days is not None and int(review_days) >= 1:
            v = int(review_days)
            min_daycount = min(min_daycount, v)
            max_daycount = max(max_daycount, v)
            values_seen = True

        if critical_days is not None:
            c = int(critical_days)
            if c >= 0:
                min_daycount = min(min_daycount, c)
                max_daycount = max(max_daycount, c)
                values_seen = True
            else:
                min_daycount = min(min_daycount, c, -c)
                max_daycount = max(max_daycount, c, -c)
                values_seen = True

    if not values_seen:
        return empty

    offset = -min_daycount
    size = max_daycount - min_daycount + 1
    day_index = {day: i for i, day in enumerate(days)}

    metrics = ("review", "critical", "overdue_age", "overdue_raw")
    count_buckets = {metric: [[0] * size for _ in days] for metric in metrics}
    sum_buckets = {metric: [[0.0] * size for _ in days] for metric in metrics}
    avg_count_buckets = {metric: [[0] * size for _ in days] for metric in metrics}
    critical_due_by_offset: list[dict[str, int]] = [{} for _ in days]
    review_study_count_sums = [0.0 for _ in days]
    review_study_count_ns = [0 for _ in days]

    def add(metric: str, day_i: int, value: int, study_count: float | None) -> None:
        idx = value + offset
        if idx < 0 or idx >= size:
            return
        count_buckets[metric][day_i][idx] += 1
        if study_count is not None:
            sum_buckets[metric][day_i][idx] += study_count
            avg_count_buckets[metric][day_i][idx] += 1

    for row in rows:
        day = str(row["study_day_key"])
        day_i = day_index.get(day)
        if day_i is None:
            continue

        study_count = _light_float_or_none(row["study_count"])
        review_days = row["review_span_days"]
        critical_days = row["critical_days"]

        if review_days is not None:
            r = int(review_days)
            if r >= 1:
                add("review", day_i, r, study_count)

        if critical_days is not None:
            c = int(critical_days)
            due_key = str(c)
            critical_due_by_offset[day_i][due_key] = critical_due_by_offset[day_i].get(due_key, 0) + 1
            if c <= 0 and study_count is not None:
                review_study_count_sums[day_i] += study_count
                review_study_count_ns[day_i] += 1

            if c >= 0:
                add("critical", day_i, c, study_count)
            else:
                add("overdue_raw", day_i, c, study_count)
                add("overdue_age", day_i, max(1, -c), study_count)

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for day_i, day in enumerate(days):
        out[day] = {
            "memoryReviewSpanStats": _light_empty_memory_stats(specs),
            "memoryCriticalStats": _light_empty_memory_stats(specs),
            "memoryOverdueStats": _light_empty_memory_stats(specs),
            "criticalStatus": {
                "dueToday": sum(v for k, v in critical_due_by_offset[day_i].items() if int(k) <= 0),
                "dueByOffset": critical_due_by_offset[day_i],
            },
            "dailyAllReviewAvgStudyCount": (
                review_study_count_sums[day_i] / review_study_count_ns[day_i]
                if review_study_count_ns[day_i]
                else None
            ),
        }

        prefixes: dict[str, tuple[list[int | float], list[int | float], list[int | float]]] = {}
        for metric in metrics:
            prefixes[metric] = (
                _light_memory_prefix(count_buckets[metric][day_i]),
                _light_memory_prefix(sum_buckets[metric][day_i]),
                _light_memory_prefix(avg_count_buckets[metric][day_i]),
            )

        for spec in specs:
            spec_id = str(spec["id"])
            lo, hi = _light_memory_integer_bounds(spec)
            specs_to_metrics = [
                ("memoryReviewSpanStats", "review"),
                ("memoryCriticalStats", "critical"),
            ]
            upper = spec.get("upper", float("inf"))
            overdue_metric = "overdue_raw" if upper != float("inf") and upper <= 0 else "overdue_age"
            specs_to_metrics.append(("memoryOverdueStats", overdue_metric))

            for stat_key, metric in specs_to_metrics:
                count_prefix, sum_prefix, avg_count_prefix = prefixes[metric]
                count = int(_light_memory_prefix_range(count_prefix, offset, lo, hi))
                total = float(_light_memory_prefix_range(sum_prefix, offset, lo, hi))
                avg_n = int(_light_memory_prefix_range(avg_count_prefix, offset, lo, hi))
                out[day][stat_key]["counts"][spec_id] = count
                out[day][stat_key]["avgs"][spec_id] = (total / avg_n) if avg_n else None

    return out


def _light_progress_numbers(
    row: sqlite3.Row | dict[str, Any] | None,
    records: list[dict[str, Any]] | None = None,
    day: str | None = None,
    critical_due_today: int | None = None,
) -> dict[str, Any]:
    raw_finished = _light_int(row_value(row, "finished"))
    raw_total = _light_int(row_value(row, "total"))
    study_time_ms = _light_int(row_value(row, "study_time"))

    first_forget_done = _light_int(row_value(row, "first_forget_done"))
    first_forget_all = _light_int(row_value(row, "first_forget_all"))
    vague_done = _light_int(row_value(row, "vague_done"))
    vague_all = _light_int(row_value(row, "vague_all"))
    familiar_done = _light_int(row_value(row, "familiar_done"))
    familiar_all = _light_int(row_value(row, "familiar_all"))

    new_total_raw = _light_int(row_value(row, "new_words"))
    pending_new_raw = _light_int(row_value(row, "pending_new_words"))
    all_items_count = _light_int(row_value(row, "all_items_count"))
    done_items_count = _light_int(row_value(row, "done_items_count"))
    todo_items_count = _light_int(row_value(row, "todo_items_count"))

    has_precomputed_critical_due_today = critical_due_today is not None
    if critical_due_today is None:
        critical_due_today = 0
    if not has_precomputed_critical_due_today and records is not None and day:
        try:
            critical_due_today = int((_study_status_critical_from_records(records, day) or {}).get("dueToday") or 0)
        except Exception:
            critical_due_today = 0

    # Canonical dashboard rule:
    # 今日总数 = 记忆临界点 + 今日新学总数
    # The raw Maimemo progress.total is retained only as diagnostic data.
    new_total = max(0, new_total_raw)
    new_pending = min(max(0, pending_new_raw), new_total)
    new_done = max(0, new_total - new_pending)
    review_total = max(0, critical_due_today)

    response_done_total = first_forget_done + vague_done + familiar_done
    finished_source = max(
        0,
        done_items_count,
        response_done_total,
        raw_finished if raw_finished <= review_total + new_total else 0,
    )

    review_done = max(0, finished_source - new_done)
    review_done = min(review_done, review_total)
    finished = new_done + review_done
    total = review_total + new_total
    unfinished = max(0, total - finished)

    has_item_detail = any(x > 0 for x in (
        all_items_count,
        done_items_count,
        todo_items_count,
        new_total_raw,
        pending_new_raw,
        first_forget_done,
        first_forget_all,
        vague_done,
        vague_all,
        familiar_done,
        familiar_all,
    ))

    return {
        "snapshot": row_value(row, "snapshot_time") or "",
        "rawProgressFinished": raw_finished,
        "rawProgressTotal": raw_total,
        "studyTimeMs": study_time_ms,
        "known": familiar_done,
        "vague": vague_done,
        "forget": first_forget_done,
        "allKnown": familiar_all,
        "allVague": vague_all,
        "allForget": first_forget_all,
        "newDone": new_done,
        "newPending": new_pending,
        "newTotal": new_total,
        "reviewDone": review_done,
        "reviewPending": max(0, review_total - review_done),
        "reviewTotal": review_total,
        "finished": finished,
        "total": total,
        "unfinished": unfinished,
        "criticalDueToday": critical_due_today,
        "allItemsCount": all_items_count,
        "doneItemsCount": done_items_count,
        "todoItemsCount": todo_items_count,
        "hasItemDetail": has_item_detail,
    }


def _light_summary_from_records_and_progress(
    records: list[dict[str, Any]],
    progress_row: sqlite3.Row | dict[str, Any] | None,
    day: str,
    memory_thresholds_text: str | None = None,
    memory_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    specs = parse_memory_threshold_specs_from_text(memory_thresholds_text) or parse_memory_threshold_specs_from_text(FALLBACK_MEMORY_THRESHOLDS)
    review_span_stats = _new_memory_stats(specs) if memory_stats is None else None
    critical_stats = _new_memory_stats(specs) if memory_stats is None else None
    overdue_stats = _new_memory_stats(specs) if memory_stats is None else None
    critical_status = memory_stats.get("criticalStatus") if isinstance(memory_stats, dict) else None
    progress_numbers = _light_progress_numbers(
        progress_row,
        records=records,
        day=day,
        critical_due_today=int((critical_status or {}).get("dueToday") or 0) if critical_status is not None else None,
    )

    total_study_count = 0.0
    total_study_count_n = 0
    review_study_counts: list[float] = []

    out: dict[str, Any] = {
        "totalWords": len(records),
        "熟知": 0,
        "顽固": 0,
        "认识": 0,
        "模糊": 0,
        "忘记": 0,
        "逾期": 0,
        "avgStudyCount": 0,
        "dailyNewLearnedCount": progress_numbers["newDone"],
        "dailyPendingNewCount": progress_numbers["newPending"],
        "dailyReviewedCount": progress_numbers["reviewDone"],
        "dailyAllReviewAvgStudyCount": None,
        "newLearnedResponseCounts": response_counts_empty(),
        "newLearnedResponseSampleCount": 0,
        "newLearnedResponseExcludedDate": "2026-05-11",
        "memoryReferenceNowUtc": day,
    }

    for record in records:
        tag_text = str(record.get("tags_text") or "")
        tags = [x.strip() for x in tag_text.replace("；", ";").replace("，", ",").replace(",", ";").split(";") if x.strip()]
        if record.get("tag_well") == "是" or "熟知" in tags:
            out["熟知"] += 1
        if record.get("tag_sticking") == "是" or "顽固" in tags:
            out["顽固"] += 1

        state = str(record.get("current_state") or "").strip()
        if not state and record.get("is_overdue") == "是":
            state = "逾期"
        if state in ("认识", "模糊", "忘记", "逾期"):
            out[state] += 1

        study_count = _light_float_or_none(record.get("study_count"))
        if study_count is not None:
            total_study_count += study_count
            total_study_count_n += 1

        if review_span_stats is not None:
            span_days = _date_diff_days(record.get("next_study_date"), record.get("last_study_date"))
            if span_days is not None:
                _add_memory_stat(review_span_stats, specs, span_days, study_count, "review_span")

        if critical_stats is not None or overdue_stats is not None or memory_stats is None:
            critical_days = _date_diff_days(record.get("next_study_date"), f"{day}T12:00:00+08:00")
        else:
            critical_days = None

        if critical_days is not None:
            if critical_stats is not None:
                _add_memory_stat(critical_stats, specs, critical_days, study_count, "critical_point")
            if overdue_stats is not None:
                _add_memory_stat(overdue_stats, specs, critical_days, study_count, "overdue")
            if critical_days <= 0 and study_count is not None:
                review_study_counts.append(study_count)

    out["avgStudyCount"] = (total_study_count / total_study_count_n) if total_study_count_n else 0
    if memory_stats is not None:
        out["dailyAllReviewAvgStudyCount"] = memory_stats.get("dailyAllReviewAvgStudyCount")
        out["memoryReviewSpanStats"] = memory_stats.get("memoryReviewSpanStats") or _light_empty_memory_stats(specs)
        out["memoryCriticalStats"] = memory_stats.get("memoryCriticalStats") or _light_empty_memory_stats(specs)
        out["memoryOverdueStats"] = memory_stats.get("memoryOverdueStats") or _light_empty_memory_stats(specs)
    else:
        out["dailyAllReviewAvgStudyCount"] = (sum(review_study_counts) / len(review_study_counts)) if review_study_counts else None
        out["memoryReviewSpanStats"] = _finalize_memory_stats(review_span_stats or _new_memory_stats(specs))
        out["memoryCriticalStats"] = _finalize_memory_stats(critical_stats or _new_memory_stats(specs))
        out["memoryOverdueStats"] = _finalize_memory_stats(overdue_stats or _new_memory_stats(specs))
    return out


def _light_progress_payload(
    row: sqlite3.Row | dict[str, Any] | None,
    records: list[dict[str, Any]] | None = None,
    day: str | None = None,
    critical_due_today: int | None = None,
) -> dict[str, Any] | None:
    if not row:
        return None
    n = _light_progress_numbers(row, records=records, day=day, critical_due_today=critical_due_today)
    snapshot = n["snapshot"]
    return {
        "snapshot": snapshot,
        "capturedAt": snapshot,
        "progress": {
            "finished": n["finished"],
            "total": n["total"],
            "study_time": n["studyTimeMs"],
        },
        "finished": n["finished"],
        "total": n["total"],
        "studyTimeMs": n["studyTimeMs"],
        "todayFirstForgetCount": n["forget"],
        "todayKnownCount": n["known"],
        "todayVagueCount": n["vague"],
        "todayAllFirstForgetCount": n["allForget"],
        "todayAllKnownCount": n["allKnown"],
        "todayAllVagueCount": n["allVague"],
        "dailyNewLearnedCount": n["newDone"],
        "dailyPendingNewCount": n["newPending"],
        "dailyAllNewCount": n["newTotal"],
        "dailyReviewedCount": n["reviewDone"],
        "dailyPendingReviewCount": n["reviewPending"],
        "dailyAllReviewCount": n["reviewTotal"],
        "unfinishedCount": n["unfinished"],
        "criticalDueToday": n["criticalDueToday"],
        "rawProgressFinished": n["rawProgressFinished"],
        "rawProgressTotal": n["rawProgressTotal"],
        "hasItemDetail": n["hasItemDetail"],
    }


def _light_study_status(
    day: str,
    records: list[dict[str, Any]],
    progress_row: sqlite3.Row | dict[str, Any] | None,
    snapshot_time: str | None,
    overall_status: dict[str, Any] | None = None,
    critical_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    n = _light_progress_numbers(
        progress_row,
        records=records,
        day=day,
        critical_due_today=int((critical_status or {}).get("dueToday") or 0) if critical_status is not None else None,
    )
    snapshot = snapshot_time or n["snapshot"] or ""
    today = {
        "known": n["known"],
        "vague": n["vague"],
        "forget": n["forget"],
        "allKnown": n["allKnown"],
        "allVague": n["allVague"],
        "allForget": n["allForget"],
        "finished": n["finished"],
        "total": n["total"],
        "unfinished": n["unfinished"],
        "newDone": n["newDone"],
        "newPending": n["newPending"],
        "newTotal": n["newTotal"],
        "reviewDone": n["reviewDone"],
        "reviewPending": n["reviewPending"],
        "reviewTotal": n["reviewTotal"],
        "criticalDueToday": n["criticalDueToday"],
        "studyTimeMs": n["studyTimeMs"],
        "studyTimeSeconds": n["studyTimeMs"] / 1000,
    }

    response_done_total = n["known"] + n["vague"] + n["forget"]
    task_done_total = n["reviewDone"] + n["newDone"]
    task_total = n["reviewTotal"] + n["newTotal"]

    return {
        "version": 3,
        "date": day,
        "snapshot": snapshot,
        "capturedAt": snapshot,
        "today": today,
        "overall": overall_status or _study_status_overall_from_records(records, day),
        "critical": critical_status or _study_status_critical_from_records(records, day),
        "checks": {
            "responseDoneEqualsTaskDone": response_done_total == task_done_total,
            "taskDoneEqualsFinished": task_done_total == n["finished"],
            "taskTotalEqualsDbTotal": task_total == n["total"],
            "unfinishedEqualsTotalMinusFinished": n["unfinished"] == max(n["total"] - n["finished"], 0),
            "responseDoneTotal": response_done_total,
            "taskDoneTotal": task_done_total,
            "dbFinished": n["finished"],
            "taskTotal": task_total,
            "dbTotal": n["total"],
            "unfinishedTotal": n["unfinished"],
            "rawProgressFinished": n["rawProgressFinished"],
            "rawProgressTotal": n["rawProgressTotal"],
            "hasItemDetail": n["hasItemDetail"],
        },
        "dbSnapshot": {
            "finished": n["finished"],
            "total": n["total"],
            "studyTimeMs": n["studyTimeMs"],
            "rawProgressFinished": n["rawProgressFinished"],
            "rawProgressTotal": n["rawProgressTotal"],
            "firstForgetDone": _light_int(row_value(progress_row, "first_forget_done")),
            "firstForgetAll": _light_int(row_value(progress_row, "first_forget_all")),
            "vagueDone": _light_int(row_value(progress_row, "vague_done")),
            "vagueAll": _light_int(row_value(progress_row, "vague_all")),
            "familiarDone": _light_int(row_value(progress_row, "familiar_done")),
            "familiarAll": _light_int(row_value(progress_row, "familiar_all")),
            "newWords": _light_int(row_value(progress_row, "new_words")),
            "reviewWords": n["reviewTotal"],
            "pendingNewWords": _light_int(row_value(progress_row, "pending_new_words")),
            "allItemsCount": _light_int(row_value(progress_row, "all_items_count")),
            "doneItemsCount": _light_int(row_value(progress_row, "done_items_count")),
            "todoItemsCount": _light_int(row_value(progress_row, "todo_items_count")),
        },
    }


def get_dashboard_data_page_light(
    conn: sqlite3.Connection,
    offset: int = 0,
    limit: int = 30,
    order: str = "asc",
    memory_thresholds_text: str | None = None,
) -> dict[str, Any]:
    """Return dashboard page using daily latest materialized state only.

    This light route avoids build_dashboard_day_payload() and
    reconstruct_study_day_items_at(). Dashboard total uses the canonical rule:
    今日总数 = 记忆临界点 + 今日新学总数。
    """
    total_start = time.perf_counter()
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    days = list_days(conn)
    timings["list_days_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    if str(order or "asc").lower() == "desc":
        days = list(reversed(days))

    total = len(days)
    safe_offset = max(0, int(offset or 0))
    safe_limit = max(1, min(200, int(limit or 30)))
    page_days = days[safe_offset:safe_offset + safe_limit]

    day_payloads: list[dict[str, Any]] = []
    day_timings: list[dict[str, Any]] = []

    t0 = time.perf_counter()
    memory_specs = parse_memory_threshold_specs_from_text(memory_thresholds_text) or parse_memory_threshold_specs_from_text(FALLBACK_MEMORY_THRESHOLDS)
    page_memory_stats = _light_page_memory_stats(conn, page_days, memory_specs)
    timings["memory_stats_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    build_start = time.perf_counter()
    for index, day in enumerate(page_days, start=1):
        day_start = time.perf_counter()

        snap = latest_snapshot_time_for_day(conn, day)
        records = _light_overview_records_for_day(conn, day) if snap else []
        progress_row = _light_latest_progress_row_for_day(conn, day, snap)
        memory_stats = page_memory_stats.get(day)

        summary = _light_summary_from_records_and_progress(
            records,
            progress_row,
            day,
            memory_thresholds_text,
            memory_stats=memory_stats,
        )
        critical_status = (memory_stats or {}).get("criticalStatus") if isinstance(memory_stats, dict) else None
        overall_status = {
            "totalWords": summary.get("totalWords", len(records)),
            "wellKnown": summary.get("熟知", 0),
            "sticking": summary.get("顽固", 0),
            "knownState": summary.get("认识", 0),
            "vagueState": summary.get("模糊", 0),
            "forgetState": summary.get("忘记", 0),
            "overdue": summary.get("逾期", 0),
            "avgStudyCount": summary.get("avgStudyCount", 0),
        }
        progress = _light_progress_payload(
            progress_row,
            records=records,
            day=day,
            critical_due_today=int((critical_status or {}).get("dueToday") or 0) if critical_status is not None else None,
        )
        study_status = _light_study_status(
            day,
            records,
            progress_row,
            snap,
            overall_status=overall_status,
            critical_status=critical_status,
        )

        overview_update_time = ""
        if progress and progress.get("capturedAt"):
            overview_update_time = str(progress["capturedAt"]).replace("T", " ").split("+")[0][-12:]

        elapsed_ms = round((time.perf_counter() - day_start) * 1000, 2)
        today_item_count = _light_int(row_value(progress_row, "all_items_count"))

        day_timings.append({
            "date": day,
            "index": safe_offset + index,
            "elapsed_ms": elapsed_ms,
            "row_count": len(records),
            "today_item_count": today_item_count,
        })
        day_payloads.append({
            "date": day,
            "summary": summary,
            "overviewUpdateTime": overview_update_time,
            "latestProgress": progress,
            "studyStatus": study_status,
            "rowCount": len(records),
            "todayItemCount": today_item_count,
        })

    timings["build_days_ms"] = round((time.perf_counter() - build_start) * 1000, 2)
    timings["total_db_ms"] = round((time.perf_counter() - total_start) * 1000, 2)

    return {
        "success": True,
        "source": "sqlite-v28-light-critical-plus-new",
        "schemaVersion": SCHEMA_VERSION,
        "studyDayBoundaryHour": STUDY_DAY_BOUNDARY_HOUR,
        "offset": safe_offset,
        "limit": safe_limit,
        "order": "desc" if str(order or "asc").lower() == "desc" else "asc",
        "total": total,
        "loaded": len(page_days),
        "nextOffset": safe_offset + len(page_days),
        "hasMore": safe_offset + len(page_days) < total,
        "days": day_payloads,
        "dayTimings": day_timings,
        "timings": timings,
    }


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def get_latest_snapshot_rows_as_overview_csv_dicts(
    conn: sqlite3.Connection,
    day_key: str,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    t = _trace_step_start()
    snapshot_time_value = latest_snapshot_time_for_day(conn, day_key)
    _trace_step_done(trace, "overview_day_snapshot_lookup", t, snapshot=snapshot_time_value)
    if not snapshot_time_value:
        return []

    t = _trace_step_start()
    records = reconstruct_overview_records_at(conn, day_key, snapshot_time_value, trace=trace)
    _trace_step_done(trace, "overview_reconstruct_total", t, rows=len(records))

    today_trace: list[dict[str, Any]] = []
    t = _trace_step_start()
    today_items = reconstruct_study_day_items_at(conn, day_key, snapshot_time_value, trace=today_trace)
    _trace_step_done(trace, "overview_today_items_total", t, rows=len(today_items))
    if trace is not None:
        trace.extend(today_trace)

    t = _trace_step_start()
    today_by_id: dict[str, dict[str, Any]] = {}
    today_by_spell: dict[str, dict[str, Any]] = {}
    for item in today_items:
        if item.get("voc_id"):
            today_by_id[str(item["voc_id"])] = item
        if item.get("voc_spelling"):
            today_by_spell[str(item["voc_spelling"])] = item
    _trace_step_done(trace, "overview_today_index_build", t, by_id=len(today_by_id), by_spell=len(today_by_spell))

    t = _trace_step_start()
    out = []
    for p in records:
        today = None
        if p.get("voc_id"):
            today = today_by_id.get(str(p["voc_id"]))
        if today is None and p.get("spelling"):
            today = today_by_spell.get(str(p["spelling"]))
        out.append(overview_payload_to_csv_row(p, today))
    _trace_step_done(trace, "overview_csv_rows_build", t, rows=len(out))
    return out


__all__ = [name for name in globals() if not name.startswith('__')]
