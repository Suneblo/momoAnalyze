#!/usr/bin/env python3
"""Compact word-key SQLite storage implementation for MomoAnalyze.

Only the latest compact_v4_word_key schema is supported by runtime code.
"""

from __future__ import annotations

import math
import threading

from momo_db_common import *


# ---------------------------------------------------------------------------
# Snapshot safety helpers
# ---------------------------------------------------------------------------


def delete_latest_incomplete_snapshot(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Delete only the latest snapshot when it is explicitly incomplete.

    Complete historical snaps are never touched. Older incomplete markers are
    ignored unless they are the latest snapshot in the database.
    """
    setup_schema(conn)
    row = conn.execute(
        """
        SELECT snapshot_time, study_day_key, incomplete_reason
        FROM snaps
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
        FROM snaps
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
    conn.execute("DELETE FROM snaps WHERE snapshot_time=?", (row["snapshot_time"],))
    return info


def record_incomplete_snapshot(conn: sqlite3.Connection, *, captured_at: datetime, source: str, reason: str) -> str:
    """Record a failed/incomplete pull as a lightweight marker only."""
    setup_schema(conn)
    study_day_key = study_day_key_from_dt(captured_at)
    snap_time = iso_bj(captured_at, milliseconds=True)
    conn.execute(
        """
        INSERT INTO snaps(snapshot_time, study_day_key, source, api_success, data_complete, incomplete_reason, created_at)
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


COMPACT_SCHEMA_VERSION = "compact_v4_word_key"
COMPACT_REQUIRED_TABLES = {
    "meta",
    "days",
    "records",
    "record_events",
    "study_items",
    "study_events",
    "snaps",
    "progress",
    "words",
    "reviews",
    "custom_words",
}
_SCHEMA_READY_DATABASES: set[str] = set()
_SCHEMA_SETUP_LOCK = threading.Lock()

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


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the current word-key compact schema."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS words(
          word_key TEXT PRIMARY KEY,
          spelling TEXT NOT NULL,
          voc_id TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_words_voc_id ON words(voc_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_words(
          voc_id TEXT PRIMARY KEY,
          spelling TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snaps(
          snapshot_time TEXT PRIMARY KEY,
          study_day_key TEXT NOT NULL,
          source TEXT NOT NULL,
          api_success INTEGER NOT NULL DEFAULT 1,
          data_complete INTEGER NOT NULL DEFAULT 1,
          incomplete_reason TEXT,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snaps_day_time ON snaps(study_day_key, snapshot_time)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS days(
          study_day_key TEXT PRIMARY KEY,
          snapshot_time TEXT NOT NULL,
          record_count INTEGER NOT NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS records(
          study_day_key TEXT NOT NULL,
          word_key TEXT NOT NULL,
          snapshot_time TEXT NOT NULL,
          voc_id TEXT,
          spelling TEXT NOT NULL,
          add_date TEXT,
          first_study_date TEXT,
          last_study_date TEXT,
          next_study_date TEXT,
          last_response TEXT,
          last_response_cn TEXT,
          study_count REAL,
          tags_text TEXT,
          tag_well TEXT,
          tag_sticking TEXT,
          current_state TEXT,
          is_overdue TEXT,
          state_hash TEXT,
          PRIMARY KEY(study_day_key, word_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_word_day ON records(word_key, study_day_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_day_next ON records(study_day_key, next_study_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_day_state ON records(study_day_key, current_state, is_overdue)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_day_count ON records(study_day_key, study_count)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_day_spelling ON records(study_day_key, spelling)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_voc_id ON records(voc_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS record_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          snapshot_time TEXT NOT NULL,
          study_day_key TEXT NOT NULL,
          word_key TEXT NOT NULL,
          voc_id TEXT,
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
          UNIQUE(snapshot_time, word_key, event_type)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_record_events_day_time ON record_events(study_day_key, snapshot_time, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_record_events_word_time ON record_events(word_key, snapshot_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_record_events_voc_id ON record_events(voc_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS study_items(
          study_day_key TEXT NOT NULL,
          snapshot_time TEXT NOT NULL,
          word_key TEXT NOT NULL,
          voc_id TEXT,
          voc_spelling TEXT NOT NULL,
          order_index INTEGER,
          first_response TEXT,
          is_new INTEGER,
          is_finished INTEGER,
          present_state TEXT,
          state_hash TEXT,
          PRIMARY KEY(study_day_key, snapshot_time, word_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_items_day_time ON study_items(study_day_key, snapshot_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_items_word_day ON study_items(word_key, study_day_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_items_voc_id ON study_items(voc_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS study_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          snapshot_time TEXT NOT NULL,
          study_day_key TEXT NOT NULL,
          word_key TEXT NOT NULL,
          voc_id TEXT,
          event_type TEXT NOT NULL,
          voc_spelling TEXT,
          order_index INTEGER,
          first_response TEXT,
          is_new INTEGER,
          is_finished INTEGER,
          present_state TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(snapshot_time, word_key, event_type)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_events_day_time ON study_events(study_day_key, snapshot_time, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_events_word_time ON study_events(word_key, snapshot_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_events_voc_id ON study_events(voc_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS progress(
          snapshot_time TEXT NOT NULL,
          study_day_key TEXT NOT NULL,
          finished INTEGER,
          total INTEGER,
          study_time INTEGER,
          first_forget_done INTEGER,
          first_forget_all INTEGER,
          vague_done INTEGER,
          vague_all INTEGER,
          familiar_done INTEGER,
          familiar_all INTEGER,
          new_words INTEGER,
          review_words INTEGER,
          pending_new_words INTEGER,
          all_items_count INTEGER NOT NULL DEFAULT 0,
          done_items_count INTEGER NOT NULL DEFAULT 0,
          todo_items_count INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(snapshot_time, study_day_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_progress_day_time ON progress(study_day_key, snapshot_time)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews(
          word_key TEXT NOT NULL,
          review_index INTEGER NOT NULL,
          review_day_key TEXT NOT NULL,
          source_day_key TEXT NOT NULL,
          snapshot_time TEXT NOT NULL,
          voc_id TEXT,
          spelling TEXT NOT NULL,
          study_count REAL,
          last_response TEXT,
          last_response_cn TEXT,
          previous_review_day_key TEXT,
          next_study_day_key TEXT,
          interval_days INTEGER,
          review_span_days INTEGER,
          current_state TEXT,
          is_overdue TEXT,
          tags_text TEXT,
          PRIMARY KEY(word_key, review_index),
          UNIQUE(word_key, study_count, review_day_key, last_response)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_word_day ON reviews(word_key, review_day_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_source_day ON reviews(source_day_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_voc_id ON reviews(voc_id)")


def _schema_database_key(conn: sqlite3.Connection) -> str:
    for row in conn.execute("PRAGMA database_list").fetchall():
        if str(row_value(row, "name") or "") != "main":
            continue
        filename = str(row_value(row, "file") or "").strip()
        return str(Path(filename).resolve()) if filename else ""
    return ""


def setup_schema(conn: sqlite3.Connection) -> None:
    database_key = _schema_database_key(conn)
    if database_key and database_key in _SCHEMA_READY_DATABASES:
        return

    with _SCHEMA_SETUP_LOCK:
        if database_key and database_key in _SCHEMA_READY_DATABASES:
            return

        _setup_schema_uncached(conn)
        if database_key:
            _SCHEMA_READY_DATABASES.add(database_key)


def _setup_schema_uncached(conn: sqlite3.Connection) -> None:
    legacy_tables = {"compact_meta", "compact_overview_day_records", "compact_overview_day_checkpoints", "snapshot_progress"}
    if not _compact_table_exists(conn, "meta") and any(_compact_table_exists(conn, table) for table in legacy_tables):
        raise RuntimeError("检测到旧版 compact_v3 数据库，请先运行新版迁移到 compact_v4_word_key。")
    _ensure_schema(conn)
    missing = sorted(t for t in COMPACT_REQUIRED_TABLES if not _compact_table_exists(conn, t))
    if missing:
        raise RuntimeError(
            "SQLite 数据库不是 compact_v4_word_key 版，缺少表: "
            + ", ".join(missing)
            + "。请重新同步生成最新版数据库。"
        )

    required_columns = {
        "words": {"word_key", "spelling", "voc_id", "first_seen_at", "last_seen_at"},
        "custom_words": {"voc_id", "spelling"},
        "records": {"study_day_key", "word_key", "snapshot_time", "voc_id", "spelling", "next_study_date", "study_count"},
        "days": {"study_day_key", "snapshot_time", "record_count", "created_at"},
        "record_events": {"study_day_key", "snapshot_time", "word_key", "voc_id", "event_type", "created_at"},
        "study_items": {"study_day_key", "snapshot_time", "word_key", "voc_id", "voc_spelling", "order_index", "first_response", "is_new", "is_finished", "present_state"},
        "study_events": {"study_day_key", "snapshot_time", "word_key", "voc_id", "event_type", "created_at"},
        "progress": {"snapshot_time", "study_day_key", "finished", "total", "study_time"},
        "snaps": {"snapshot_time", "study_day_key", "source", "api_success", "data_complete", "created_at"},
        "reviews": {"word_key", "review_index", "review_day_key", "source_day_key", "study_count", "review_span_days", "interval_days"},
    }
    bad: list[str] = []
    for table, req in required_columns.items():
        cols = _compact_columns(conn, table)
        miss = sorted(req - cols)
        if miss:
            bad.append(f"{table} 缺少列 {miss}")

    forbidden_columns = {"records": {"record_key", "raw_json", "tags_json"}, "study_items": {"item_key", "raw_json"}, "progress": {"raw_json"}}
    for table, forbidden in forbidden_columns.items():
        cols = _compact_columns(conn, table)
        present = sorted(cols & forbidden)
        if present:
            bad.append(f"{table} 仍包含废弃列 {present}")

    if bad:
        raise RuntimeError("SQLite compact_v4_word_key schema 不完整: " + "; ".join(bad))

    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if not row:
        conn.execute("INSERT INTO meta(key,value) VALUES(?,?)", ("schema_version", COMPACT_SCHEMA_VERSION))
    elif str(row["value"] if isinstance(row, sqlite3.Row) else row[0]) != COMPACT_SCHEMA_VERSION:
        raise RuntimeError("SQLite schema_version 不是 compact_v4_word_key，请运行新版迁移。")

    conn.execute("PRAGMA optimize")


# ---------------------------------------------------------------------------
# Custom word spelling repair
# ---------------------------------------------------------------------------


def custom_word_placeholder(voc_id: str | None) -> str:
    text = str(voc_id or "").strip()
    tail = text[-12:] if text else "unknown"
    return f"[custom:{tail}]"


def _custom_word_key(voc_id: str | None) -> str:
    text = str(voc_id or "").strip()
    return f"voc:{text}" if text else ""


def _custom_words_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT voc_id, spelling FROM custom_words").fetchall()
    out: dict[str, str] = {}
    for row in rows:
        voc_id = str(row["voc_id"] or "").strip()
        spelling = str(row["spelling"] or "").strip()
        if voc_id and spelling:
            out[voc_id] = spelling
    return out


def _is_custom_placeholder(value: Any) -> bool:
    text = str(value or "").strip()
    return text.startswith("[custom:") and text.endswith("]")


def _looks_like_voc_fallback(value: Any, voc_id: str | None = None) -> bool:
    text = str(value or "").strip()
    if not text.startswith("voc:"):
        return False
    return not voc_id or text == _custom_word_key(voc_id)


def _apply_custom_word_spellings(conn: sqlite3.Connection, *collections: list[dict[str, Any]] | None) -> dict[str, int]:
    custom_map = _custom_words_map(conn)
    stats = {"empty": 0, "mapped": 0, "placeholder": 0}

    for collection in collections:
        for item in collection or []:
            if not isinstance(item, dict):
                continue
            voc_id = canonical_voc_id(item)
            if not voc_id:
                continue
            spelling = canonical_spelling(item)
            if spelling:
                continue

            stats["empty"] += 1
            resolved = custom_map.get(voc_id)
            repaired = resolved or custom_word_placeholder(voc_id)
            item["word_key"] = _custom_word_key(voc_id)
            item["voc_spelling"] = repaired
            item["spelling"] = repaired
            if resolved:
                stats["mapped"] += 1
            else:
                stats["placeholder"] += 1

    return stats


def _backfill_custom_word_spelling(conn: sqlite3.Connection, voc_id: str, spelling: str) -> dict[str, int]:
    updates: dict[str, int] = {}
    statements = [
        ("words", "UPDATE words SET spelling=? WHERE voc_id=?", (spelling, voc_id)),
        ("records", "UPDATE records SET spelling=? WHERE voc_id=?", (spelling, voc_id)),
        ("study_items", "UPDATE study_items SET voc_spelling=? WHERE voc_id=?", (spelling, voc_id)),
        ("study_events", "UPDATE study_events SET voc_spelling=? WHERE voc_id=?", (spelling, voc_id)),
        ("reviews", "UPDATE reviews SET spelling=? WHERE voc_id=?", (spelling, voc_id)),
    ]
    for table, sql, params in statements:
        cursor = conn.execute(sql, params)
        updates[table] = int(cursor.rowcount or 0)
    _RECONSTRUCT_CACHE.clear()
    return updates


def _assert_custom_word_spelling_unique(conn: sqlite3.Connection, voc_id: str, spelling: str) -> None:
    custom_key = _custom_word_key(voc_id)
    word_row = conn.execute(
        """
        SELECT word_key, voc_id, spelling
        FROM words
        WHERE TRIM(spelling)=? COLLATE NOCASE
          AND word_key<>?
          AND COALESCE(voc_id, '')<>?
        LIMIT 1
        """,
        (spelling, custom_key, voc_id),
    ).fetchone()
    if word_row:
        raise ValueError(f"拼写已存在于词库：{word_row['spelling']}")

    custom_row = conn.execute(
        """
        SELECT voc_id, spelling
        FROM custom_words
        WHERE TRIM(spelling)=? COLLATE NOCASE
          AND voc_id<>?
        LIMIT 1
        """,
        (spelling, voc_id),
    ).fetchone()
    if custom_row:
        raise ValueError(f"拼写已被另一个自定义词使用：{custom_row['spelling']}")


def save_custom_word(conn: sqlite3.Connection, voc_id: str, spelling: str) -> dict[str, Any]:
    setup_schema(conn)
    vid = str(voc_id or "").strip()
    word = str(spelling or "").strip()
    if not vid:
        raise ValueError("缺少 voc_id")
    if not word:
        raise ValueError("拼写不能为空")
    if _is_custom_placeholder(word) or _looks_like_voc_fallback(word):
        raise ValueError("请填写真实自定义词拼写")
    _assert_custom_word_spelling_unique(conn, vid, word)

    conn.execute(
        """
        INSERT INTO custom_words(voc_id, spelling)
        VALUES(?,?)
        ON CONFLICT(voc_id) DO UPDATE SET spelling=excluded.spelling
        """,
        (vid, word),
    )
    updates = _backfill_custom_word_spelling(conn, vid, word)
    return {"vocId": vid, "spelling": word, "updated": updates}


def _latest_progress_snapshot_for_day(conn: sqlite3.Connection, day_key: str) -> str | None:
    row = conn.execute(
        """
        SELECT p.snapshot_time
        FROM progress p
        LEFT JOIN snaps s ON s.snapshot_time=p.snapshot_time
        WHERE p.study_day_key=?
          AND COALESCE(s.api_success, 1)=1
          AND COALESCE(s.data_complete, 1)=1
        ORDER BY p.snapshot_time DESC
        LIMIT 1
        """,
        (day_key,),
    ).fetchone()
    return str(row["snapshot_time"]) if row else None


def _custom_word_context_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "order": item.get("order"),
        "word": item.get("voc_spelling") or item.get("word") or item.get("spelling") or "",
        "vocId": item.get("voc_id") or item.get("vocId") or "",
    }


def _custom_word_due_state(next_study_date: Any, day_key: str) -> tuple[str, int | None]:
    next_day = _date_only(next_study_date)
    if not next_day:
        return "", None
    days = _days_between_date_text(day_key, next_day)
    if days is None:
        return "", None
    if days > 0:
        return f"逾期 {days} 天", days
    if days == 0:
        return "今日到期", 0
    return f"{-days} 天后", days


def get_today_custom_words(conn: sqlite3.Connection, day_key: str | None = None) -> dict[str, Any]:
    setup_schema(conn)
    day = day_key or study_day_key_from_dt()
    snapshot_time = _latest_progress_snapshot_for_day(conn, day)
    if not snapshot_time:
        return {"date": day, "snapshot": "", "items": [], "total": 0, "resolvedCount": 0, "unresolvedCount": 0}

    custom_map = _custom_words_map(conn)
    items = reconstruct_study_day_items_at(conn, day, snapshot_time)
    ordered = sorted(items, key=lambda x: (x.get("order") is None, x.get("order") if x.get("order") is not None else 10**12))
    record_rows = conn.execute(
        """
        SELECT word_key, voc_id, study_count, next_study_date, last_response, last_response_cn, current_state
        FROM records
        WHERE study_day_key=?
        """,
        (day,),
    ).fetchall()
    records_by_key = {str(r["word_key"] or ""): r for r in record_rows if r["word_key"]}

    out: list[dict[str, Any]] = []
    resolved_count = 0
    for index, item in enumerate(ordered):
        voc_id = str(item.get("voc_id") or "").strip()
        if not voc_id:
            continue
        word = str(item.get("voc_spelling") or "").strip()
        is_custom = _is_custom_placeholder(word) or _looks_like_voc_fallback(word, voc_id)
        if not is_custom and voc_id not in custom_map:
            continue

        resolved = custom_map.get(voc_id, "")
        if resolved:
            resolved_count += 1
            continue

        record = records_by_key.get(str(item.get("word_key") or ""))
        due_state, overdue_days = _custom_word_due_state(row_value(record, "next_study_date"), day) if record else ("", None)
        out.append({
            "vocId": voc_id,
            "vocIdTail": voc_id[-12:],
            "order": item.get("order"),
            "display": word or custom_word_placeholder(voc_id),
            "isNew": item.get("is_new"),
            "isFinished": item.get("is_finished"),
            "firstResponse": item.get("first_response") or "",
            "studyCount": row_value(record, "study_count"),
            "lastResponse": row_value(record, "last_response_cn") or row_value(record, "last_response") or "",
            "nextStudyDate": _date_only(row_value(record, "next_study_date")),
            "dueState": due_state,
            "overdueDays": overdue_days,
            "before": [_custom_word_context_item(x) for x in ordered[max(0, index - 5):index]],
            "after": [_custom_word_context_item(x) for x in ordered[index + 1:index + 6]],
        })

    return {
        "date": day,
        "snapshot": snapshot_time,
        "items": out,
        "total": len(out),
        "resolvedCount": resolved_count,
        "unresolvedCount": len(out),
    }


# ---------------------------------------------------------------------------
# Day / snapshot lookup
# ---------------------------------------------------------------------------


def _compact_latest_day(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT study_day_key FROM days ORDER BY study_day_key DESC LIMIT 1").fetchone()
    if row:
        return str(row["study_day_key"])
    row = conn.execute(
        """
        SELECT study_day_key
        FROM snaps
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
        FROM days c
        JOIN snaps s ON s.snapshot_time=c.snapshot_time
        WHERE COALESCE(s.data_complete,1)=1 AND COALESCE(s.api_success,1)=1
        ORDER BY c.study_day_key
        """
    ).fetchall()
    if rows:
        return [str(r["study_day_key"]) for r in rows]
    rows = conn.execute(
        """
        SELECT DISTINCT study_day_key
        FROM snaps
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
        FROM days c
        JOIN snaps s ON s.snapshot_time=c.snapshot_time
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
        FROM snaps
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
        FROM days c
        JOIN snaps s ON s.snapshot_time=c.snapshot_time
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
        FROM snaps
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
    word_key = row_value(row, "word_key") or row_value(row, "spelling") or ""
    return {
        "record_key": word_key,
        "word_key": word_key,
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
        "word_key": row_value(row, "word_key") or row_value(row, "voc_spelling") or "",
        "voc_id": row_value(row, "voc_id"),
        "voc_spelling": row_value(row, "voc_spelling"),
        "order": row_value(row, "order_index"),
        "first_response": row_value(row, "first_response"),
        "is_new": None if row_value(row, "is_new") is None else bool(row_value(row, "is_new")),
        "is_finished": None if row_value(row, "is_finished") is None else bool(row_value(row, "is_finished")),
        "present_state": row_value(row, "present_state") or "observed",
    }


def _overview_state_row_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    word_key = payload.get("word_key") or payload.get("spelling") or ""
    return {
        "record_key": word_key,
        "word_key": word_key,
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
    word_key: str,
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
        f"DELETE FROM {table} WHERE snapshot_time=? AND word_key=? AND event_type=?",
        (snapshot_time, word_key, event_type),
    )
    columns = ["snapshot_time", "study_day_key", "word_key", "voc_id", "event_type", *safe_values.keys(), "created_at"]
    placeholders = ",".join("?" for _ in columns)
    params = [snapshot_time, study_day_key, word_key, voc_id, event_type, *safe_values.values(), created_at]
    conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})", params)


def _apply_patch_study_events(state: dict[str, dict[str, Any]], rows: list[sqlite3.Row]) -> int:
    applied = 0
    for ev in rows:
        key = str(ev["word_key"] or "").strip()
        if not key:
            continue
        event_type = str(ev["event_type"] or "")
        if event_type == "record_removed":
            state.pop(key, None)
            applied += 1
            continue
        cur = state.get(key, {"word_key": key, "voc_id": row_value(ev, "voc_id")})
        if row_value(ev, "voc_id") is not None:
            cur["voc_id"] = row_value(ev, "voc_id")
        if event_type not in {"patch", "record_inserted"}:
            continue
        for field_name in STUDY_ITEM_EVENT_FIELDS:
            value = ev[field_name]
            if value is not None:
                cur[field_name] = _patch_event_cast(field_name, value)
        cur.setdefault("present_state", "observed")
        state[key] = cur
        applied += 1
    return applied


def _reverse_record_events(conn: sqlite3.Connection, state: dict[str, dict[str, Any]], rows: list[sqlite3.Row]) -> None:
    if not rows:
        return
    word_rows = conn.execute("SELECT word_key, spelling, voc_id FROM words").fetchall()
    word_by_key = {str(r["word_key"]): r for r in word_rows if r["word_key"]}
    for ev in reversed(rows):
        key = str(ev["word_key"] or "").strip()
        if not key:
            continue
        event_type = str(ev["event_type"] or "")
        old_values = {field: _patch_event_cast(field, row_value(ev, _overview_old_field(field))) for field in OVERVIEW_EVENT_FIELDS}
        has_old = any(row_value(ev, _overview_old_field(field)) is not None for field in OVERVIEW_EVENT_FIELDS)
        if event_type == "record_inserted":
            state.pop(key, None)
            continue
        if not has_old:
            continue
        if event_type == "record_removed":
            word_row = word_by_key.get(key)
            state[key] = _overview_state_row_from_payload({
                "word_key": key,
                "voc_id": row_value(word_row, "voc_id") or row_value(ev, "voc_id"),
                "spelling": row_value(word_row, "spelling") or key,
                **old_values,
            })
            continue
        if event_type == "patch":
            word_row = word_by_key.get(key)
            cur = state.get(key) or _overview_state_row_from_payload({
                "word_key": key,
                "voc_id": row_value(word_row, "voc_id") or row_value(ev, "voc_id"),
                "spelling": row_value(word_row, "spelling") or key,
            })
            for field, value in old_values.items():
                cur[field] = value
            state[key] = cur


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
    cached = _cache_get("study_items", study_day_key, snapshot_time)
    if cached is not None:
        _trace_step_done(trace, "study_items_cache_hit", t, rows=len(cached))
        return cached

    base = conn.execute(
        """
        SELECT snapshot_time
        FROM study_items
        WHERE study_day_key=? AND snapshot_time<=?
        ORDER BY snapshot_time DESC
        LIMIT 1
        """,
        (study_day_key, snapshot_time),
    ).fetchone()

    if not base:
        _trace_step_done(trace, "study_reconstruct_no_baseline", t, rows=0)
        _cache_put("study_items", study_day_key, snapshot_time, [])
        return []

    base_snapshot = str(base["snapshot_time"])
    rows = conn.execute(
        """
        SELECT *
        FROM study_items
        WHERE study_day_key=? AND snapshot_time=?
        ORDER BY order_index IS NULL, order_index, voc_spelling COLLATE NOCASE
        """,
        (study_day_key, base_snapshot),
    ).fetchall()

    state: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = str(r["word_key"] or "").strip()
        if not key:
            continue
        state[key] = {
            "word_key": key,
            "voc_id": r["voc_id"],
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
        FROM study_events
        WHERE study_day_key=? AND snapshot_time>? AND snapshot_time<=?
        ORDER BY snapshot_time ASC, id ASC
        """,
        (study_day_key, base_snapshot, snapshot_time),
    ).fetchall()
    _apply_patch_study_events(state, patch_changes)

    out = [_v3_study_item_to_api(x) for x in state.values()]
    out.sort(key=lambda x: (x.get("order") is None, x.get("order") if x.get("order") is not None else 10**12, (x.get("voc_spelling") or "").lower()))
    _trace_step_done(trace, "study_reconstruct", t, baseline=base_snapshot, initial=initial_count, patchEvents=len(patch_changes), rows=len(out))
    _cache_put("study_items", study_day_key, snapshot_time, out)
    return out


def reconstruct_overview_records_at(
    conn: sqlite3.Connection,
    study_day_key: str,
    snapshot_time: str,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    setup_schema(conn)
    t = _trace_step_start()
    cached = _cache_get("records", study_day_key, snapshot_time)
    if cached is not None:
        _trace_step_done(trace, "records_cache_hit", t, rows=len(cached))
        return cached

    rows = conn.execute(
        """
        SELECT *
        FROM records
        WHERE study_day_key=?
        ORDER BY next_study_date, add_date, spelling COLLATE NOCASE
        """,
        (study_day_key,),
    ).fetchall()
    state = {str(r["word_key"]): _v3_overview_payload(r) for r in rows if r["word_key"]}

    checkpoint = conn.execute(
        "SELECT snapshot_time FROM days WHERE study_day_key=?",
        (study_day_key,),
    ).fetchone()
    latest_snapshot = str(checkpoint["snapshot_time"]) if checkpoint else None
    reversed_events = 0
    if latest_snapshot and snapshot_time < latest_snapshot:
        patch_rows = conn.execute(
            """
            SELECT *
            FROM record_events
            WHERE study_day_key=? AND snapshot_time>? AND snapshot_time<=?
            ORDER BY snapshot_time ASC, id ASC
            """,
            (study_day_key, snapshot_time, latest_snapshot),
        ).fetchall()
        _reverse_record_events(conn, state, patch_rows)
        reversed_events = len(patch_rows)

    out = sorted(state.values(), key=lambda x: (x.get("next_study_date") or "", x.get("add_date") or "", (x.get("spelling") or "").lower()))
    _trace_step_done(trace, "records_day_records_query", t, rows=len(out), latest=latest_snapshot, reversedEvents=reversed_events)
    _cache_put("records", study_day_key, snapshot_time, out)
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
        FROM progress p
        LEFT JOIN snaps s ON s.snapshot_time=p.snapshot_time
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
        "SELECT * FROM progress WHERE study_day_key=? AND snapshot_time=?",
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
        FROM progress p
        LEFT JOIN snaps s ON s.snapshot_time=p.snapshot_time
        WHERE p.study_day_key=?
          AND COALESCE(s.api_success, 1)=1
          AND COALESCE(s.data_complete, 1)=1
        ORDER BY p.snapshot_time ASC
        """,
        (day_key,),
    ).fetchall()

    snaps: list[dict[str, Any]] = []
    all_progress_lines: list[str] = []
    missing_detail_count = 0
    history_index_cache: dict[str, list[dict[str, Any]]] = {}
    history_loaded_keys: set[str] = set()

    for row in rows:
        detail_missing = _v3_progress_detail_missing(row)
        if detail_missing:
            missing_detail_count += 1
            all_items = []
        else:
            all_items = reconstruct_study_day_items_at(conn, day_key, row["snapshot_time"])
        try:
            overview_records = reconstruct_overview_records_at(conn, day_key, row["snapshot_time"])
        except Exception:
            overview_records = []
        if all_items:
            word_keys = {item_key(item) for item in all_items if item_key(item)}
            missing_history_keys = word_keys - history_loaded_keys
            if missing_history_keys:
                try:
                    from momo_fsrs import build_review_history_state_index
                    history_index_cache.update(build_review_history_state_index(conn, word_keys=missing_history_keys, max_words=100000))
                except Exception:
                    pass
                history_loaded_keys.update(missing_history_keys)
            all_items = enrich_today_items_with_prediction_raw_data(
                all_items,
                overview_records,
                day_key,
                row["snapshot_time"],
                history_index=history_index_cache,
            )
        active_items = [x for x in all_items if x.get("present_state") != "confirmed_absent"]
        done_items = [x for x in active_items if x.get("is_finished") is True]
        todo_items = [x for x in active_items if x.get("is_finished") is not True]

        progress = {"finished": row["finished"], "total": row["total"], "study_time": row["study_time"]}
        breakdown = progress_today_breakdown(all_items)
        study_status = build_study_status_from_raw(day_key, overview_records, all_items, row, row["snapshot_time"])
        if detail_missing:
            row_finished = nullable_int(row["finished"]) or 0
            row_total = nullable_int(row["total"]) or 0
            row_study_time = nullable_int(row["study_time"]) or 0
            study_status["today"].update({
                "finished": row_finished,
                "total": row_total,
                "unfinished": max(row_total - row_finished, 0),
                "studyTimeMs": row_study_time,
                "studyTimeSeconds": row_study_time / 1000,
            })
            study_status["checks"]["detailMissing"] = True
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
            "detailMissing": detail_missing,
        }
        snaps.append(snap)
        all_progress_lines.append(
            f"{display_name} | 已完成={summary['finished']} | 总数={summary['total']} | "
            f"今日首次忘记数={summary['firstForgetDone']} | 今日全部首次忘记数={summary['firstForgetAll']} | "
            f"今日模糊数(已完成)={summary['doneVague']} | 今日模糊数(全部)={summary['allVague']} | "
            f"今日认识数={summary['familiar']} | 今日新学数={summary['newWords']} | "
            f"今日复习数={summary['reviewWords']} | 今日待新学数={summary['pendingNew']} | "
            f"未完成={summary['unfinished']} | 记忆临界点={summary['criticalDueToday']} | 逾期={summary['overdue']} | "
            f"学习时长={summary['studyTimeMs'] / 1000:.0f}秒"
        )

    error = "" if snaps else f"未读取到 {day_key} 的有效数据库快照"
    if missing_detail_count and snaps:
        error = f"包含 {missing_detail_count} 个仅有进度、缺少今日单词明细的快照"
    return {
        "date": day_key,
        "snapshots": snaps,
        "allProgressText": "\n".join(all_progress_lines),
        "error": error,
        "skippedMissingDetailSnapshots": 0,
        "missingDetailSnapshots": missing_detail_count,
    }


# ---------------------------------------------------------------------------
# Time-point frontend diff
# ---------------------------------------------------------------------------


def _timepoint_latest_day_with_two_snapshots(conn: sqlite3.Connection) -> str | None:
    days = conn.execute(
        """
        SELECT DISTINCT p.study_day_key
        FROM progress p
        LEFT JOIN snaps s ON s.snapshot_time=p.snapshot_time
        WHERE COALESCE(s.api_success, 1)=1
          AND COALESCE(s.data_complete, 1)=1
        ORDER BY p.study_day_key DESC
        """
    ).fetchall()
    for row in days:
        day = str(row["study_day_key"])
        if len(_timepoint_candidate_rows(conn, day)) >= 2:
            return day
    return None


def _timepoint_candidate_rows(conn: sqlite3.Connection, day_key: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT p.*
        FROM progress p
        LEFT JOIN snaps s ON s.snapshot_time=p.snapshot_time
        WHERE p.study_day_key=?
          AND COALESCE(s.api_success, 1)=1
          AND COALESCE(s.data_complete, 1)=1
        ORDER BY p.snapshot_time ASC
        """,
        (day_key,),
    ).fetchall()
    return [row for row in rows if not _v3_progress_detail_missing(row)]


def _timepoint_progress_row(conn: sqlite3.Connection, day_key: str, snapshot_time: str) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT p.*
        FROM progress p
        LEFT JOIN snaps s ON s.snapshot_time=p.snapshot_time
        WHERE p.study_day_key=?
          AND p.snapshot_time=?
          AND COALESCE(s.api_success, 1)=1
          AND COALESCE(s.data_complete, 1)=1
        LIMIT 1
        """,
        (day_key, snapshot_time),
    ).fetchone()
    return row if row and not _v3_progress_detail_missing(row) else None


def _timepoint_display_name(snapshot_time: str) -> str:
    return str(snapshot_time or "").replace("T", " ").split("+")[0]


def _timepoint_snapshot_payload(
    conn: sqlite3.Connection,
    day_key: str,
    row: sqlite3.Row,
    memory_thresholds_text: str | None,
) -> dict[str, Any]:
    snapshot_time = str(row["snapshot_time"])
    records = reconstruct_overview_records_at(conn, day_key, snapshot_time)
    items = reconstruct_study_day_items_at(conn, day_key, snapshot_time)
    if items:
        items = enrich_today_items_with_prediction_raw_data(items, records, day_key, snapshot_time, conn)
    summary = build_dashboard_summary_from_records(
        records,
        items,
        day_key,
        snapshot_time,
        memory_thresholds_text=memory_thresholds_text,
    )
    study_status = build_study_status_from_raw(day_key, records, items, row, snapshot_time)
    return {
        "snapshot": snapshot_time,
        "displayName": _timepoint_display_name(snapshot_time),
        "records": records,
        "items": items,
        "summary": summary,
        "studyStatus": study_status,
        "rowCount": len(records),
        "todayItemCount": len(items),
    }


def _timepoint_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
        return out if out == out else None
    except Exception:
        return None


def _timepoint_changed(before: Any, after: Any, digits: int = 2) -> bool:
    a = _timepoint_float(before)
    b = _timepoint_float(after)
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return round(a, digits) != round(b, digits)


def _timepoint_format_number(value: Any, digits: int = 2) -> str:
    n = _timepoint_float(value)
    if n is None:
        return ""
    rounded = round(n, digits)
    if float(rounded).is_integer():
        return str(int(rounded))
    return f"{rounded:.{digits}f}".rstrip("0").rstrip(".")


def _timepoint_markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")

    if not rows:
        return ""
    header = "| " + " | ".join(cell(x) for x in headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(cell(x) for x in row) + " |" for row in rows]
    return "\n".join([header, sep, *body])


def _timepoint_new_number(before: Any, after: Any, digits: int = 2) -> str:
    if not _timepoint_changed(before, after, digits):
        return "-"
    return _timepoint_format_number(after, digits) or "-"


def _timepoint_snapshot_row(
    identity: list[Any],
    before: dict[str, Any],
    after: dict[str, Any],
    fields: list[tuple[str, str, int]],
) -> list[str]:
    values = [_timepoint_new_number(before.get(key), after.get(key), digits) for _label, key, digits in fields]
    return [str(value) for value in identity] + values if any(value != "-" for value in values) else []


def _timepoint_snapshot_headers(identity_headers: list[str], fields: list[tuple[str, str, int]]) -> list[str]:
    return [*identity_headers, *(label for label, _key, _digits in fields)]


def _timepoint_nested(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    value: Any = source
    for key in keys:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def _timepoint_threshold_intersects_integer_range(
    spec: dict[str, Any],
    minimum: float,
    maximum: float = float("inf"),
) -> bool:
    lower = spec.get("lower", float("-inf"))
    upper = spec.get("upper", float("inf"))
    lower_bound = math.ceil(lower) if lower != float("-inf") and spec.get("lowerInclusive", True) else (
        math.floor(lower) + 1 if lower != float("-inf") else float("-inf")
    )
    upper_bound = math.floor(upper) if upper != float("inf") and spec.get("upperInclusive", True) else (
        math.ceil(upper) - 1 if upper != float("inf") else float("inf")
    )
    range_minimum = math.ceil(minimum) if minimum != float("-inf") else float("-inf")
    range_maximum = math.floor(maximum) if maximum != float("inf") else float("inf")
    return max(lower_bound, range_minimum) <= min(upper_bound, range_maximum)


def _timepoint_threshold_intersects_critical(spec: dict[str, Any]) -> bool:
    return _timepoint_threshold_intersects_integer_range(spec, 0)


def _timepoint_threshold_intersects_review_span(spec: dict[str, Any]) -> bool:
    return (
        _timepoint_threshold_intersects_integer_range(spec, 1)
        and not _timepoint_threshold_intersects_integer_range(spec, float("-inf"), 0)
    )


def _timepoint_threshold_header_label(spec: dict[str, Any]) -> str:
    spec_id = str(spec.get("id") or "")
    if spec_id.startswith("exact:"):
        return f"{spec_id.split(':', 1)[1]}天"
    if spec_id.startswith("gte:"):
        return f"≥{spec_id.split(':', 1)[1]}天"
    if spec_id.startswith("gt:"):
        return f">{spec_id.split(':', 1)[1]}天"
    if spec_id.startswith("lte:"):
        return f"≤{spec_id.split(':', 1)[1]}天"
    if spec_id.startswith("lt:"):
        value = spec_id.split(":", 1)[1]
        return "逾期" if value == "0" else f"<{value}天"
    if spec_id.startswith("range:"):
        return f"{spec_id.split(':', 1)[1]}天"
    return spec_id


def _timepoint_memory_column_label(spec: dict[str, Any], mode: str) -> str:
    label = _timepoint_threshold_header_label(spec)
    if mode == "critical_point":
        exact_zero = (
            spec.get("lower") == 0
            and spec.get("upper") == 0
            and spec.get("lowerInclusive", True)
            and spec.get("upperInclusive", True)
        )
        return f"遗忘临界点{label}{'（当日待复习）' if exact_zero else ''}（数量/平均学习次数）"
    return f"记忆持久度{label}（数量/平均学习次数）"


def _timepoint_memory_cell(
    before_summary: dict[str, Any],
    after_summary: dict[str, Any],
    stat_key: str,
    spec: dict[str, Any],
) -> str:
    spec_id = str(spec.get("id") or "")
    before_stat = before_summary.get(stat_key) or {}
    after_stat = after_summary.get(stat_key) or {}
    before_count = (before_stat.get("counts") or {}).get(spec_id)
    after_count = (after_stat.get("counts") or {}).get(spec_id)
    before_avg = (before_stat.get("avgs") or {}).get(spec_id)
    after_avg = (after_stat.get("avgs") or {}).get(spec_id)
    if not _timepoint_changed(before_count, after_count, 0) and not _timepoint_changed(before_avg, after_avg, 2):
        return "-"
    count_text = _timepoint_format_number(after_count, 0) or "0"
    avg_text = _timepoint_format_number(after_avg, 2) or "-"
    return f"{count_text}/{avg_text}"


def _timepoint_summary_increment_table(
    day_key: str,
    before: dict[str, Any],
    after: dict[str, Any],
    specs: list[dict[str, Any]],
) -> tuple[list[str], list[list[str]]]:
    before_summary = before["summary"]
    after_summary = after["summary"]
    headers = ["日期", "更新时间（总览更新时分秒）", "当日复习平均学习次数（全部复习词累计平均）"]
    update_time = after["displayName"].split(" ", 1)[-1] if before["displayName"] != after["displayName"] else "-"
    row = [
        day_key,
        update_time,
        _timepoint_new_number(
            before_summary.get("dailyAllReviewAvgStudyCount"),
            after_summary.get("dailyAllReviewAvgStudyCount"),
            2,
        ),
    ]

    for spec in specs:
        if _timepoint_threshold_intersects_critical(spec):
            headers.append(_timepoint_memory_column_label(spec, "critical_point"))
            row.append(_timepoint_memory_cell(before_summary, after_summary, "memoryCriticalStats", spec))
        if _timepoint_threshold_intersects_review_span(spec):
            headers.append(_timepoint_memory_column_label(spec, "review_span"))
            row.append(_timepoint_memory_cell(before_summary, after_summary, "memoryReviewSpanStats", spec))

    return headers, [row] if any(value != "-" for value in row[1:]) else []


def _timepoint_memory_increment_table(
    day_key: str,
    before_summary: dict[str, Any],
    after_summary: dict[str, Any],
    specs: list[dict[str, Any]],
) -> tuple[list[str], list[list[str]]]:
    headers = ["日期"]
    row = [day_key]
    for spec in specs:
        if not _timepoint_threshold_intersects_review_span(spec):
            continue
        headers.append(f"记忆持久度{_timepoint_threshold_header_label(spec)}")
        before_count = ((before_summary.get("memoryReviewSpanStats") or {}).get("counts") or {}).get(str(spec.get("id") or ""))
        after_count = ((after_summary.get("memoryReviewSpanStats") or {}).get("counts") or {}).get(str(spec.get("id") or ""))
        row.append(_timepoint_new_number(before_count, after_count, 0))
    return headers, [row] if any(value != "-" for value in row[1:]) else []


def _timepoint_review_span_hist(records: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for record in records:
        days = _date_diff_days(record.get("next_study_date"), record.get("last_study_date"))
        if days is None or days < 1:
            continue
        key = str(int(days))
        out[key] = out.get(key, 0) + 1
    return out


def _timepoint_study_count_hist(records: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for record in records:
        count = _timepoint_float(record.get("study_count"))
        if count is None:
            continue
        key = _timepoint_format_number(count, 1)
        out[key] = out.get(key, 0) + 1
    return out


def _timepoint_hist_rows(
    before_hist: dict[str, int],
    after_hist: dict[str, int],
    *,
    suffix: str = "",
    limit: int = 120,
) -> list[list[str]]:
    def sort_key(value: str) -> tuple[int, float | str]:
        try:
            return (0, float(value))
        except Exception:
            return (1, value)

    rows: list[list[str]] = []
    keys = sorted(set(before_hist) | set(after_hist), key=sort_key)
    for key in keys:
        av = before_hist.get(key, 0)
        bv = after_hist.get(key, 0)
        if av == bv:
            continue
        rows.append([f"{key}{suffix}", str(bv)])
        if len(rows) >= limit:
            break
    return rows


def _timepoint_item_key(item: dict[str, Any]) -> str:
    for key in ("voc_id", "vocId", "word_key", "wordKey", "voc_spelling", "word", "spelling"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _timepoint_item_finished(item: dict[str, Any] | None) -> bool:
    return bool((item or {}).get("is_finished") or (item or {}).get("isFinished"))


def _timepoint_item_response(item: dict[str, Any] | None) -> str:
    return str((item or {}).get("first_response") or (item or {}).get("firstResponse") or "").strip()


def _timepoint_real_response(value: str) -> bool:
    return value in {"FAMILIAR", "VAGUE", "FORGET", "认识", "模糊", "忘记"}


def _timepoint_item_memory_days(item: dict[str, Any] | None, previous: bool = False) -> float | None:
    if not item:
        return None
    keys = (
        ("previous_memory_durability_days", "previous_review_span_days", "previousMemoryReviewSpanDays", "previous_memory_review_span_days", "previousDays")
        if previous
        else ("memory_durability_days", "review_span_days", "memoryReviewSpanDays", "memory_review_span_days", "days")
    )
    for key in keys:
        value = _timepoint_float(item.get(key))
        if value is not None:
            return value
    return None


def _timepoint_item_study_count(item: dict[str, Any] | None, previous: bool = False) -> float | None:
    if not item:
        return None
    keys = ("previous_study_count", "previousStudyCount") if previous else ("study_count", "studyCount")
    for key in keys:
        value = _timepoint_float(item.get(key))
        if value is not None:
            return value
    return None


def _timepoint_items_reviewed_between(before_item: dict[str, Any] | None, after_item: dict[str, Any] | None) -> bool:
    if not after_item:
        return False
    if not _timepoint_item_finished(before_item) and _timepoint_item_finished(after_item):
        return True
    before_response = _timepoint_item_response(before_item)
    after_response = _timepoint_item_response(after_item)
    return _timepoint_real_response(after_response) and before_response != after_response


def _timepoint_reviewed_item_hist_rows(
    before_items: list[dict[str, Any]],
    after_items: list[dict[str, Any]],
    *,
    kind: str,
) -> tuple[list[list[str]], int]:
    before_map = {key: item for item in before_items if (key := _timepoint_item_key(item))}
    after_map = {key: item for item in after_items if (key := _timepoint_item_key(item))}
    before_hist: dict[str, int] = {}
    after_hist: dict[str, int] = {}
    reviewed_count = 0

    for key, after_item in after_map.items():
        before_item = before_map.get(key)
        if not _timepoint_items_reviewed_between(before_item, after_item):
            continue
        reviewed_count += 1

        if kind == "memory":
            before_value = _timepoint_item_memory_days(before_item)
            if before_value is None:
                before_value = _timepoint_item_memory_days(after_item, previous=True)
            after_value = _timepoint_item_memory_days(after_item)
            suffix = "天"
            digits = 1
        else:
            before_value = _timepoint_item_study_count(before_item)
            if before_value is None:
                before_value = _timepoint_item_study_count(after_item, previous=True)
            after_value = _timepoint_item_study_count(after_item)
            suffix = "次"
            digits = 1

        before_key = _timepoint_format_number(before_value, digits)
        after_key = _timepoint_format_number(after_value, digits)
        if before_key:
            before_hist[before_key] = before_hist.get(before_key, 0) + 1
        if after_key:
            after_hist[after_key] = after_hist.get(after_key, 0) + 1

    return _timepoint_hist_rows(before_hist, after_hist, suffix=suffix), reviewed_count


TIMEPOINT_DIFF_SUPPORTED_COPY_KEYS = (
    "overviewChart",
    "todayWordStats",
    "todayWorkspace",
    "summary",
    "memory",
    "studyTime",
)


def _timepoint_normalize_copy_keys(copy_keys: list[str] | None) -> list[str]:
    if not copy_keys:
        return list(TIMEPOINT_DIFF_SUPPORTED_COPY_KEYS)
    supported = set(TIMEPOINT_DIFF_SUPPORTED_COPY_KEYS)
    out: list[str] = []
    for key in copy_keys:
        text = str(key or "").strip()
        if text in supported and text not in out:
            out.append(text)
    return out


def _timepoint_detail_enabled(copy_details: dict[str, Any] | None, copy_key: str, detail_key: str) -> bool:
    details = (copy_details or {}).get(copy_key)
    if not isinstance(details, dict) or detail_key not in details:
        return True
    return details.get(detail_key) is not False


def _timepoint_diff_copy_text(
    *,
    day_key: str,
    before: dict[str, Any],
    after: dict[str, Any],
    specs: list[dict[str, Any]],
    critical_future_days: int,
    selected_copy_keys: list[str] | None = None,
    copy_details: dict[str, Any] | None = None,
) -> tuple[str, dict[str, int]]:
    before_summary = before["summary"]
    after_summary = after["summary"]
    before_today = _timepoint_nested(before, "studyStatus", "today")
    after_today = _timepoint_nested(after, "studyStatus", "today")
    before_overall = _timepoint_nested(before, "studyStatus", "overall")
    after_overall = _timepoint_nested(after, "studyStatus", "overall")
    before_critical = _timepoint_nested(before, "studyStatus", "critical")
    after_critical = _timepoint_nested(after, "studyStatus", "critical")

    if "studyTimeMs" in before_today or "studyTimeMs" in after_today:
        before_today["studyTimeMinutes"] = (_timepoint_float(before_today.get("studyTimeMs")) or 0) / 60000
        after_today["studyTimeMinutes"] = (_timepoint_float(after_today.get("studyTimeMs")) or 0) / 60000

    copy_keys = _timepoint_normalize_copy_keys(selected_copy_keys)
    blocks_by_key: dict[str, list[tuple[str, str]]] = {key: [] for key in copy_keys}
    counts: dict[str, int] = {}

    sections = [
        "# 最新时间点前端变化",
        f"日期：{day_key}",
        f"对比：{before['displayName']} -> {after['displayName']}",
    ]

    def add_block(copy_key: str, count_key: str, title: str, headers: list[str], rows: list[list[Any]]) -> None:
        if copy_key not in blocks_by_key:
            return
        counts[count_key] = len(rows)
        if rows:
            blocks_by_key[copy_key].append((title, _timepoint_markdown_table(headers, rows)))

    overall_fields = [
        ("当前总词数", "totalWords", 0),
        ("熟知", "wellKnown", 0),
        ("顽固", "sticking", 0),
        ("认识状态", "knownState", 0),
        ("模糊状态", "vagueState", 0),
        ("忘记状态", "forgetState", 0),
        ("逾期", "overdue", 0),
        ("平均学习次数", "avgStudyCount", 2),
    ]
    overall_row = _timepoint_snapshot_row([day_key], before_overall, after_overall, overall_fields)
    if _timepoint_detail_enabled(copy_details, "overviewChart", "status"):
        add_block(
            "overviewChart",
            "overallRows",
            "复习情况 / 当前状态数据",
            _timepoint_snapshot_headers(["日期"], overall_fields),
            [overall_row] if overall_row else [],
        )

    today_fields = [
        ("今日认识(已完成)", "known", 0),
        ("今日模糊(已完成)", "vague", 0),
        ("今日忘记(已完成)", "forget", 0),
        ("已完成", "finished", 0),
        ("未完成", "unfinished", 0),
        ("总数", "total", 0),
        ("今日已复习", "reviewDone", 0),
        ("今日待复习", "reviewPending", 0),
        ("今日复习总任务", "reviewTotal", 0),
        ("今日已新学", "newDone", 0),
        ("今日待新学", "newPending", 0),
        ("今日新学总任务", "newTotal", 0),
        ("今日全部认识", "allKnown", 0),
        ("今日全部模糊", "allVague", 0),
        ("今日全部忘记", "allForget", 0),
        ("学习时长(分钟)", "studyTimeMinutes", 1),
    ]
    today_row = _timepoint_snapshot_row([day_key, after["displayName"]], before_today, after_today, today_fields)
    if _timepoint_detail_enabled(copy_details, "overviewChart", "today"):
        add_block(
            "overviewChart",
            "todayRows",
            "复习情况 / 今日学习进度",
            _timepoint_snapshot_headers(["日期", "快照时间"], today_fields),
            [today_row] if today_row else [],
        )

    before_due = before_critical.get("dueByOffset") or {}
    after_due = after_critical.get("dueByOffset") or {}
    critical_fields = [("待复习(<=0天)", "dueToday")]
    for offset in range(1, max(0, int(critical_future_days)) + 1):
        critical_fields.append((f"{offset}天后临界", str(offset)))
    critical_source_before = {"dueToday": before_critical.get("dueToday")}
    critical_source_after = {"dueToday": after_critical.get("dueToday")}
    for offset in range(1, max(0, int(critical_future_days)) + 1):
        critical_source_before[str(offset)] = before_due.get(str(offset), 0)
        critical_source_after[str(offset)] = after_due.get(str(offset), 0)
    critical_value_fields = [(label, key, 0) for label, key in critical_fields]
    critical_row = _timepoint_snapshot_row([day_key], critical_source_before, critical_source_after, critical_value_fields)
    if _timepoint_detail_enabled(copy_details, "overviewChart", "critical"):
        add_block(
            "overviewChart",
            "criticalRows",
            "复习情况 / 记忆临界点统计",
            _timepoint_snapshot_headers(["日期"], critical_value_fields),
            [critical_row] if critical_row else [],
        )

    today_word_source_before = {
        **before_today,
        "criticalDueToday": before_critical.get("dueToday"),
        "overdue": before_overall.get("overdue"),
        "totalWords": before_overall.get("totalWords"),
        "knownState": before_overall.get("knownState"),
        "vagueState": before_overall.get("vagueState"),
        "forgetState": before_overall.get("forgetState"),
    }
    today_word_source_after = {
        **after_today,
        "criticalDueToday": after_critical.get("dueToday"),
        "overdue": after_overall.get("overdue"),
        "totalWords": after_overall.get("totalWords"),
        "knownState": after_overall.get("knownState"),
        "vagueState": after_overall.get("vagueState"),
        "forgetState": after_overall.get("forgetState"),
    }
    today_word_fields = [
        ("已完成", "finished", 0),
        ("总数", "total", 0),
        ("未完成", "unfinished", 0),
        ("记忆临界点(<=0天)", "criticalDueToday", 0),
        ("逾期", "overdue", 0),
        ("今日认识(已完成)", "known", 0),
        ("今日模糊(已完成)", "vague", 0),
        ("今日忘记(已完成)", "forget", 0),
        ("今日认识(全部)", "allKnown", 0),
        ("今日模糊(全部)", "allVague", 0),
        ("今日忘记(全部)", "allForget", 0),
        ("今日已复习", "reviewDone", 0),
        ("今日待复习", "reviewPending", 0),
        ("今日复习总任务", "reviewTotal", 0),
        ("今日已新学", "newDone", 0),
        ("今日待新学", "newPending", 0),
        ("今日新学总任务", "newTotal", 0),
        ("当前总词数", "totalWords", 0),
        ("认识状态", "knownState", 0),
        ("模糊状态", "vagueState", 0),
        ("忘记状态", "forgetState", 0),
        ("学习时长(分钟)", "studyTimeMinutes", 1),
    ]
    today_word_row = _timepoint_snapshot_row(
        [after["displayName"], after["snapshot"]],
        today_word_source_before,
        today_word_source_after,
        today_word_fields,
    )
    if _timepoint_detail_enabled(copy_details, "todayWordStats", "snapshots"):
        add_block(
            "todayWordStats",
            "todayWordRows",
            "当日单词统计 / 快照实时数据",
            _timepoint_snapshot_headers(["快照时间", "原始快照"], today_word_fields),
            [today_word_row] if today_word_row else [],
        )

    summary_headers, summary_rows = _timepoint_summary_increment_table(day_key, before, after, specs)
    if _timepoint_detail_enabled(copy_details, "summary", "table"):
        add_block("summary", "summaryRows", "按日期统计表 / 表格数据", summary_headers, summary_rows)

    memory_headers, memory_rows = _timepoint_memory_increment_table(day_key, before_summary, after_summary, specs)
    if _timepoint_detail_enabled(copy_details, "memory", "chart"):
        add_block(
            "memory",
            "memoryRows",
            "记忆持久度统计 / 图表数据",
            memory_headers,
            memory_rows,
        )

    study_time_fields = [("学习时长(分钟)", "studyTimeMinutes", 1)]
    study_time_row = _timepoint_snapshot_row([day_key], before_today, after_today, study_time_fields)
    if (
        _timepoint_detail_enabled(copy_details, "studyTime", "chart")
        or _timepoint_detail_enabled(copy_details, "studyTime", "summary")
    ):
        add_block(
            "studyTime",
            "studyTimeRows",
            "每日学习时长统计 / 学习时长变化",
            _timepoint_snapshot_headers(["日期"], study_time_fields),
            [study_time_row] if study_time_row else [],
        )

    before_summary["studyTimeMinutes"] = (_timepoint_float(before_today.get("studyTimeMs")) or 0) / 60000
    after_summary["studyTimeMinutes"] = (_timepoint_float(after_today.get("studyTimeMs")) or 0) / 60000
    workspace_fields = [
        ("已完成", "finished", 0),
        ("总数", "total", 0),
        ("今日首次忘记数", "firstForgetDone", 0),
        ("今日全部首次忘记数", "firstForgetAll", 0),
        ("今日模糊数(已完成)", "doneVague", 0),
        ("今日模糊数(全部)", "allVague", 0),
        ("今日认识数", "familiar", 0),
        ("今日新学数", "newWords", 0),
        ("今日复习数", "reviewWords", 0),
        ("今日待新学数", "pendingNew", 0),
        ("学习时长(分钟)", "studyTimeMinutes", 1),
    ]
    workspace_row = _timepoint_snapshot_row([after["displayName"]], before_summary, after_summary, workspace_fields)
    if _timepoint_detail_enabled(copy_details, "todayWorkspace", "compare"):
        add_block(
            "todayWorkspace",
            "workspaceRows",
            "当日时间点查看 / 最近两个时间点对比",
            _timepoint_snapshot_headers(["时间点"], workspace_fields),
            [workspace_row] if workspace_row else [],
        )

    reviewed_memory_rows, reviewed_item_count = _timepoint_reviewed_item_hist_rows(before["items"], after["items"], kind="memory")
    counts["reviewedItemCount"] = reviewed_item_count
    if _timepoint_detail_enabled(copy_details, "todayWorkspace", "compare"):
        add_block(
            "todayWorkspace",
            "reviewedItemMemoryRows",
            f"当日时间点查看 / 本次新增完成复习词记忆持久度分布（{reviewed_item_count} 词）",
            ["记忆持久度", "最新数量"],
            reviewed_memory_rows,
        )

    reviewed_count_rows, _ = _timepoint_reviewed_item_hist_rows(before["items"], after["items"], kind="study_count")
    if _timepoint_detail_enabled(copy_details, "todayWorkspace", "compare"):
        add_block(
            "todayWorkspace",
            "reviewedItemStudyCountRows",
            f"当日时间点查看 / 本次新增完成复习词学习次数分布（{reviewed_item_count} 词）",
            ["学习次数", "最新数量"],
            reviewed_count_rows,
        )

    emitted = 0
    for copy_key in copy_keys:
        for title, table in blocks_by_key.get(copy_key, []):
            sections.extend(["", f"## {title}", table])
            emitted += 1

    if emitted <= 0:
        return "", counts

    return "\n".join(part for part in sections if part is not None), counts


def get_timepoint_frontend_diff(
    conn: sqlite3.Connection,
    day_key: str | None = None,
    snapshot_a: str | None = None,
    snapshot_b: str | None = None,
    memory_thresholds_text: str | None = None,
    critical_future_days: int = 7,
    selected_copy_keys: list[str] | None = None,
    copy_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    setup_schema(conn)
    started = time.perf_counter()
    day = str(day_key or "").strip() or _timepoint_latest_day_with_two_snapshots(conn)
    if not day:
        return {"date": "", "available": False, "error": "没有找到可对比的时间点"}

    row_a = _timepoint_progress_row(conn, day, snapshot_a) if snapshot_a else None
    row_b = _timepoint_progress_row(conn, day, snapshot_b) if snapshot_b else None
    if row_a is None or row_b is None:
        candidates = _timepoint_candidate_rows(conn, day)
        if len(candidates) < 2:
            return {"date": day, "available": False, "error": "该日期少于 2 个可对比时间点"}
        row_a = row_a or candidates[-2]
        row_b = row_b or candidates[-1]

    specs = parse_memory_threshold_specs_from_text(memory_thresholds_text) or parse_memory_threshold_specs_from_text(FALLBACK_MEMORY_THRESHOLDS)
    build_started = time.perf_counter()
    before = _timepoint_snapshot_payload(conn, day, row_a, memory_thresholds_text)
    after = _timepoint_snapshot_payload(conn, day, row_b, memory_thresholds_text)
    copy_text, changed_counts = _timepoint_diff_copy_text(
        day_key=day,
        before=before,
        after=after,
        specs=specs,
        critical_future_days=max(0, min(365, int(critical_future_days or 0))),
        selected_copy_keys=selected_copy_keys,
        copy_details=copy_details,
    )

    return {
        "date": day,
        "available": True,
        "snapshotA": before["snapshot"],
        "snapshotB": after["snapshot"],
        "displayA": before["displayName"],
        "displayB": after["displayName"],
        "copyText": copy_text,
        "changedCounts": changed_counts,
        "rowCountA": before["rowCount"],
        "rowCountB": after["rowCount"],
        "todayItemCountA": before["todayItemCount"],
        "todayItemCountB": after["todayItemCount"],
        "timings": {
            "buildSnapshotsMs": round((time.perf_counter() - build_started) * 1000, 2),
            "totalMs": round((time.perf_counter() - started) * 1000, 2),
        },
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
    removed_keys: set[str] = set()
    narrow_rows = conn.execute(
        """
        SELECT word_key
        FROM study_events
        WHERE study_day_key=? AND snapshot_time=? AND event_type='record_removed'
        ORDER BY id ASC
        """,
        (study_day_key, snapshot_time),
    ).fetchall()
    removed_keys.update(str(r["word_key"] or "").strip() for r in narrow_rows if str(r["word_key"] or "").strip())

    if not removed_keys:
        return []

    prev = conn.execute(
        """
        SELECT snapshot_time
        FROM snaps
        WHERE study_day_key=? AND snapshot_time<? AND COALESCE(api_success,1)=1 AND COALESCE(data_complete,1)=1
        ORDER BY snapshot_time DESC
        LIMIT 1
        """,
        (study_day_key, snapshot_time),
    ).fetchone()
    previous_items = reconstruct_study_day_items_at(conn, study_day_key, str(prev["snapshot_time"])) if prev else []
    previous_by_key = {str(x.get("word_key") or x.get("voc_spelling") or "").strip(): x for x in previous_items if str(x.get("word_key") or x.get("voc_spelling") or "").strip()}

    out_by_key: dict[str, dict[str, Any]] = {}
    for key in removed_keys:
        item = previous_by_key.get(key, {"word_key": key})
        out_by_key[key] = {
            "word": item.get("voc_spelling") or "",
            "vocId": item.get("voc_id") or "",
            "order": item.get("order"),
            "firstResponse": item.get("first_response") or "",
            "isNew": item.get("is_new"),
            "isFinished": item.get("is_finished"),
            "presentState": "missing_candidate",
        }

    out = list(out_by_key.values())
    out.sort(key=lambda x: (x.get("order") is None, x.get("order") if x.get("order") is not None else 10**12, str(x.get("word") or "").lower()))
    return out


def get_latest_decrease_alerts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    setup_schema(conn)
    row = conn.execute(
        """
        SELECT snapshot_time, study_day_key
        FROM snaps
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

    current_progress = conn.execute("SELECT all_items_count, total FROM progress WHERE snapshot_time=?", (snap_time,)).fetchone()
    previous_progress = conn.execute(
        """
        SELECT snapshot_time, all_items_count, total
        FROM progress
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
            "recordKey": p.get("word_key") or p.get("spelling") or "",
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
        total = int(conn.execute(f"SELECT COUNT(*) AS c FROM records{where_sql}", args).fetchone()["c"] or 0)
        page_size = max(1, total or 1) if page_size_raw == "all" else max(1, min(nullable_int(page_size_raw) or 1000, 50000))
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""
            SELECT *
            FROM records
            {where_sql}
            ORDER BY {sql_sort} {direction}, spelling COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
            """,
            [*args, page_size, offset],
        ).fetchall()
        timings["sql_filter_page_ms"] = round((time.perf_counter() - t) * 1000, 2)
        items = []
        for r in rows:
            p = _v3_overview_payload(r)
            items.append(item_from_payload(p, _today_item_for_record(p, today_by_key, today_by_spelling)))
    else:
        t = time.perf_counter()
        rows = conn.execute(f"SELECT * FROM records{where_sql}", args).fetchall()
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
        timings["filter_page_ms"] = round((time.perf_counter() - t) * 1000, 2)

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
        FROM records
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
            "recordKey": r["word_key"] or r["spelling"] or "",
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
        FROM records
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
            "recordKey": r["word_key"] or r["spelling"] or "",
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


def _v3_upsert_word(
    conn: sqlite3.Connection,
    *,
    word_key: str | None = None,
    voc_id: str | None,
    spelling: str | None,
    captured_iso: str,
) -> None:
    key = str(word_key or "").strip()
    if not key:
        key = str(spelling or "").strip() or (f"voc:{str(voc_id).strip()}" if str(voc_id or "").strip() else "")
    if not key:
        return
    vid = str(voc_id or "").strip() or None
    conn.execute(
        """
        INSERT INTO words(word_key, spelling, voc_id, first_seen_at, last_seen_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(word_key) DO UPDATE SET
          voc_id=COALESCE(excluded.voc_id, words.voc_id),
          spelling=COALESCE(excluded.spelling, words.spelling),
          last_seen_at=excluded.last_seen_at
        """,
        (key, spelling or key, vid, captured_iso, captured_iso),
    )


def _v3_has_detail_today_items(*item_lists: list[dict[str, Any]] | None) -> bool:
    return any(bool(x) for x in item_lists if x is not None)


def _v3_insert_progress(
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
        INSERT OR REPLACE INTO progress(
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
        key = str(p.get("word_key") or "").strip()
        if not key:
            continue
        p["state_hash"] = item_state_hash(p)
        payloads[key] = p
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
        _v3_upsert_word(
            conn,
            word_key=p.get("word_key"),
            voc_id=p.get("voc_id"),
            spelling=p.get("voc_spelling"),
            captured_iso=captured_iso,
        )

    has_initial = conn.execute("SELECT 1 FROM study_items WHERE study_day_key=? LIMIT 1", (study_day_key,)).fetchone() is not None
    if not has_initial:
        for p in payloads.values():
            conn.execute(
                """
                INSERT INTO study_items(
                  study_day_key, snapshot_time, word_key, voc_id, voc_spelling, order_index,
                  first_response, is_new, is_finished, present_state, state_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    study_day_key,
                    snapshot_time,
                    p.get("word_key"),
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
        FROM snaps
        WHERE study_day_key=? AND snapshot_time<? AND COALESCE(data_complete,1)=1 AND COALESCE(api_success,1)=1
        ORDER BY snapshot_time DESC
        LIMIT 1
        """,
        (study_day_key, snapshot_time),
    ).fetchone()
    previous_ref_snapshot = prev_snapshot["snapshot_time"] if prev_snapshot else snapshot_time
    previous_items = reconstruct_study_day_items_at(conn, study_day_key, previous_ref_snapshot)
    prev_by_key: dict[str, dict[str, Any]] = {}
    for it in previous_items:
        key = str(it.get("word_key") or it.get("voc_spelling") or "").strip()
        if not key:
            continue
        prev_by_key[key] = {
            "word_key": key,
            "voc_id": it.get("voc_id"),
            "voc_spelling": it.get("voc_spelling"),
            "order_index": it.get("order"),
            "first_response": it.get("first_response"),
            "is_new": None if it.get("is_new") is None else int(bool(it.get("is_new"))),
            "is_finished": None if it.get("is_finished") is None else int(bool(it.get("is_finished"))),
            "present_state": it.get("present_state"),
        }

    changes = 0
    current_keys = set(payloads)
    for key, p in payloads.items():
        prev = prev_by_key.get(key)
        if prev is None:
            _insert_patch_event(
                conn,
                "study_events",
                STUDY_ITEM_EVENT_FIELDS,
                snapshot_time=snapshot_time,
                study_day_key=study_day_key,
                word_key=key,
                voc_id=str(p.get("voc_id") or ""),
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
                "study_events",
                STUDY_ITEM_EVENT_FIELDS,
                snapshot_time=snapshot_time,
                study_day_key=study_day_key,
                word_key=key,
                voc_id=str(p.get("voc_id") or ""),
                event_type="patch",
                values=changed,
                created_at=captured_iso,
            )
            changes += 1

    for key, prev in prev_by_key.items():
        if key in current_keys:
            continue
        _insert_patch_event(
            conn,
            "study_events",
            STUDY_ITEM_EVENT_FIELDS,
            snapshot_time=snapshot_time,
            study_day_key=study_day_key,
            word_key=key,
            voc_id=str(prev.get("voc_id") or ""),
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
        key = str(p.get("word_key") or "").strip()
        if not key:
            continue
        p["state_hash"] = record_state_hash(p)
        payloads[key] = p
        _v3_upsert_word(
            conn,
            word_key=p.get("word_key"),
            voc_id=p.get("voc_id"),
            spelling=p.get("spelling"),
            captured_iso=captured_iso,
        )

    previous_rows = conn.execute("SELECT * FROM records WHERE study_day_key=?", (study_day_key,)).fetchall()
    previous = {str(r["word_key"]): r for r in previous_rows if r["word_key"]}
    had_day_baseline = bool(previous)
    changes = 0

    if had_day_baseline:
        for key in sorted(set(previous) - set(payloads)):
            _insert_patch_event(
                conn,
                "record_events",
                OVERVIEW_EVENT_FIELDS,
                snapshot_time=snapshot_time,
                study_day_key=study_day_key,
                word_key=key,
                voc_id=str(row_value(previous[key], "voc_id") or ""),
                event_type="record_removed",
                values={},
                old_values={field: row_value(previous[key], field) for field in OVERVIEW_EVENT_FIELDS},
                created_at=captured_iso,
            )
            changes += 1

        for key, p in payloads.items():
            prev = previous.get(key)
            if prev is None:
                _insert_patch_event(
                    conn,
                    "record_events",
                    OVERVIEW_EVENT_FIELDS,
                    snapshot_time=snapshot_time,
                    study_day_key=study_day_key,
                    word_key=key,
                    voc_id=str(p.get("voc_id") or ""),
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
                    "record_events",
                    OVERVIEW_EVENT_FIELDS,
                    snapshot_time=snapshot_time,
                    study_day_key=study_day_key,
                    word_key=key,
                    voc_id=str(p.get("voc_id") or ""),
                    event_type="patch",
                    values=changed,
                    old_values={field: row_value(prev, field) for field in OVERVIEW_EVENT_FIELDS},
                    created_at=captured_iso,
                )
                changes += 1

    conn.execute("DELETE FROM records WHERE study_day_key=?", (study_day_key,))
    for p in payloads.values():
        conn.execute(
            """
            INSERT INTO records(
              study_day_key, word_key, snapshot_time, voc_id, spelling, add_date, first_study_date,
              last_study_date, next_study_date, last_response, last_response_cn, study_count,
              tags_text, tag_well, tag_sticking, current_state, is_overdue, state_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                study_day_key,
                p.get("word_key"),
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
        INSERT INTO days(study_day_key, snapshot_time, record_count, created_at)
        VALUES(?,?,?,?)
        ON CONFLICT(study_day_key) DO UPDATE SET
          snapshot_time=excluded.snapshot_time,
          record_count=excluded.record_count,
          created_at=excluded.created_at
        """,
        (study_day_key, snapshot_time, len(payloads), captured_iso),
    )

    _RECONSTRUCT_CACHE.clear()
    return changes


def rebuild_review_index(conn: sqlite3.Connection) -> int:
    """Rebuild per-word review events from records."""
    rows = conn.execute(
        """
        SELECT
          study_day_key, snapshot_time, word_key, voc_id, spelling,
          last_study_date, next_study_date, last_response, last_response_cn,
          study_count, current_state, is_overdue, tags_text
        FROM records
        WHERE word_key IS NOT NULL AND word_key<>''
          AND last_study_date IS NOT NULL AND last_study_date<>''
        ORDER BY word_key, study_day_key
        """
    ).fetchall()

    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        word_key = str(row["word_key"] or "").strip()
        review_day = _date_only(row["last_study_date"])
        if not word_key or not review_day:
            continue
        study_count_text = "" if row["study_count"] is None else str(row["study_count"])
        response = str(row["last_response"] or "")
        event_key = (word_key, study_count_text, review_day, response)
        if event_key in seen:
            continue
        seen.add(event_key)
        next_day = _date_only(row["next_study_date"])
        grouped.setdefault(word_key, []).append({
            "review_day_key": review_day,
            "source_day_key": row["study_day_key"],
            "snapshot_time": row["snapshot_time"],
            "voc_id": row["voc_id"],
            "spelling": row["spelling"] or word_key,
            "study_count": row["study_count"],
            "last_response": row["last_response"],
            "last_response_cn": row["last_response_cn"],
            "next_study_day_key": next_day,
            "review_span_days": _days_between_date_text(next_day, review_day),
            "current_state": row["current_state"],
            "is_overdue": row["is_overdue"],
            "tags_text": row["tags_text"],
        })

    conn.execute("DELETE FROM reviews")
    inserted = 0
    for word_key, events in grouped.items():
        events.sort(key=lambda e: (e["review_day_key"], e["study_count"] if e["study_count"] is not None else -1, e["source_day_key"]))
        previous_day: str | None = None
        for idx, event in enumerate(events, start=1):
            interval_days = _days_between_date_text(event["review_day_key"], previous_day) if previous_day else None
            conn.execute(
                """
                INSERT OR REPLACE INTO reviews(
                  word_key, review_index, review_day_key, source_day_key, snapshot_time,
                  voc_id, spelling, study_count, last_response, last_response_cn,
                  previous_review_day_key, next_study_day_key, interval_days, review_span_days,
                  current_state, is_overdue, tags_text
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    word_key,
                    idx,
                    event["review_day_key"],
                    event["source_day_key"],
                    event["snapshot_time"],
                    event["voc_id"],
                    event["spelling"],
                    event["study_count"],
                    event["last_response"],
                    event["last_response_cn"],
                    previous_day,
                    event["next_study_day_key"],
                    interval_days,
                    event["review_span_days"],
                    event["current_state"],
                    event["is_overdue"],
                    event["tags_text"],
                ),
            )
            previous_day = event["review_day_key"]
            inserted += 1
    return inserted


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

    custom_stats = _apply_custom_word_spellings(conn, all_list, done_list, todo_list, records)

    p = extract_progress(progress)
    progress_total = nullable_int(p.get("total")) or 0
    progress_finished = nullable_int(p.get("finished")) or 0
    has_detail_items = _v3_has_detail_today_items(all_list, done_list, todo_list)

    # Dangerous 04:00/rollover guard: progress can be positive while today_items
    # is empty. In that case, write only an incomplete marker and do not touch
    # progress, today-item tables, or overview checkpoints.
    if not has_detail_items and (progress_total > 0 or progress_finished > 0):
        reason = "today_items_empty_but_progress_present"
        conn.execute(
            """
            INSERT INTO snaps(snapshot_time, study_day_key, source, api_success, data_complete, incomplete_reason, created_at)
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
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("schema_version", COMPACT_SCHEMA_VERSION))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("last_sync_at", created_iso))
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
        INSERT INTO snaps(snapshot_time, study_day_key, source, api_success, data_complete, incomplete_reason, created_at)
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

    _v3_insert_progress(
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
    if records:
        rebuild_review_index(conn)

    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("schema_version", COMPACT_SCHEMA_VERSION))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("last_sync_at", created_iso))
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
        custom_word_stats=custom_stats,
    )


# ---------------------------------------------------------------------------
# Light dashboard route
# ---------------------------------------------------------------------------


def _light_overview_records_for_day(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM records
        WHERE study_day_key=?
        ORDER BY next_study_date, add_date, spelling COLLATE NOCASE
        """,
        (day,),
    ).fetchall()
    return [_v3_overview_payload(row) for row in rows]


def _light_date_ordinal(value: Any, cache: dict[str, int | None]) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text in cache:
        return cache[text]
    day = _date_only(text)
    if not day:
        cache[text] = None
        return None
    try:
        out = datetime.strptime(day, "%Y-%m-%d").date().toordinal()
    except Exception:
        out = None
    cache[text] = out
    return out


def _light_page_record_rows(conn: sqlite3.Connection, days: list[str]) -> list[dict[str, Any]]:
    if not days:
        return []
    ph = _light_sql_placeholders(days)
    rows = conn.execute(
        f"""
        SELECT
          study_day_key,
          study_count,
          tags_text,
          tag_well,
          tag_sticking,
          current_state,
          is_overdue,
          last_study_date,
          next_study_date
        FROM records
        WHERE study_day_key IN ({ph})
        """,
        list(days),
    ).fetchall()

    date_cache: dict[str, int | None] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        next_ord = _light_date_ordinal(row["next_study_date"], date_cache)
        last_ord = _light_date_ordinal(row["last_study_date"], date_cache)
        day_ord = _light_date_ordinal(row["study_day_key"], date_cache)
        out.append({
            "study_day_key": row["study_day_key"],
            "study_count": row["study_count"],
            "tags_text": row["tags_text"],
            "tag_well": row["tag_well"],
            "tag_sticking": row["tag_sticking"],
            "current_state": row["current_state"],
            "is_overdue": row["is_overdue"],
            "review_span_days": (next_ord - last_ord) if next_ord is not None and last_ord is not None else None,
            "critical_days": (next_ord - day_ord) if next_ord is not None and day_ord is not None else None,
        })
    return out


def _light_page_records_by_day(rows: list[dict[str, Any]], days: list[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {day: [] for day in days}
    for row in rows:
        day = str(row["study_day_key"])
        if day in out:
            out[day].append({
                "study_count": row["study_count"],
                "tags_text": row["tags_text"],
                "tag_well": row["tag_well"],
                "tag_sticking": row["tag_sticking"],
                "current_state": row["current_state"],
                "is_overdue": row["is_overdue"],
            })
    return out


def _light_page_latest_snapshots(conn: sqlite3.Connection, days: list[str]) -> dict[str, str]:
    if not days:
        return {}
    ph = _light_sql_placeholders(days)
    rows = conn.execute(
        f"""
        SELECT c.study_day_key, c.snapshot_time
        FROM days c
        JOIN snaps s ON s.snapshot_time=c.snapshot_time
        WHERE c.study_day_key IN ({ph})
          AND COALESCE(s.data_complete, 1)=1
          AND COALESCE(s.api_success, 1)=1
        """,
        list(days),
    ).fetchall()
    out = {str(row["study_day_key"]): str(row["snapshot_time"]) for row in rows}
    missing = [day for day in days if day not in out]
    if not missing:
        return out

    missing_ph = _light_sql_placeholders(missing)
    fallback_rows = conn.execute(
        f"""
        SELECT study_day_key, MAX(snapshot_time) AS snapshot_time
        FROM snaps
        WHERE study_day_key IN ({missing_ph})
          AND COALESCE(data_complete, 1)=1
          AND COALESCE(api_success, 1)=1
        GROUP BY study_day_key
        """,
        missing,
    ).fetchall()
    for row in fallback_rows:
        if row["snapshot_time"]:
            out[str(row["study_day_key"])] = str(row["snapshot_time"])
    return out


def _light_page_progress_rows(
    conn: sqlite3.Connection,
    days: list[str],
    snapshots: dict[str, str],
) -> dict[str, sqlite3.Row]:
    if not days:
        return {}
    ph = _light_sql_placeholders(days)
    rows = conn.execute(
        f"""
        SELECT p.*
        FROM progress p
        JOIN snaps s ON s.snapshot_time=p.snapshot_time
        WHERE p.study_day_key IN ({ph})
          AND COALESCE(s.data_complete, 1)=1
          AND COALESCE(s.api_success, 1)=1
        ORDER BY p.study_day_key, p.snapshot_time DESC
        """,
        list(days),
    ).fetchall()
    candidates: dict[str, list[sqlite3.Row]] = {day: [] for day in days}
    for row in rows:
        day = str(row["study_day_key"])
        if day in candidates and len(candidates[day]) < 20:
            candidates[day].append(row)

    out: dict[str, sqlite3.Row] = {}
    for day, day_rows in candidates.items():
        snapshot = snapshots.get(day)
        exact = next(
            (
                row for row in day_rows
                if str(row["snapshot_time"]) == snapshot and not _v3_progress_detail_missing(row)
            ),
            None,
        )
        fallback = next((row for row in day_rows if not _v3_progress_detail_missing(row)), None)
        if exact or fallback:
            out[day] = exact or fallback
    return out


def _light_latest_progress_row_for_day(conn: sqlite3.Connection, day: str, snapshot_time: str | None) -> sqlite3.Row | None:
    if snapshot_time:
        row = conn.execute(
            """
            SELECT p.*
            FROM progress p
            JOIN snaps s ON s.snapshot_time=p.snapshot_time
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
        FROM progress p
        JOIN snaps s ON s.snapshot_time=p.snapshot_time
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
    rows: list[dict[str, Any]] | None = None,
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

    if rows is None:
        rows = _light_page_record_rows(conn, days)
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
    page_record_rows = _light_page_record_rows(conn, page_days)
    records_by_day = _light_page_records_by_day(page_record_rows, page_days)
    timings["records_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    snapshots_by_day = _light_page_latest_snapshots(conn, page_days)
    progress_by_day = _light_page_progress_rows(conn, page_days, snapshots_by_day)
    timings["snapshots_progress_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    memory_specs = parse_memory_threshold_specs_from_text(memory_thresholds_text) or parse_memory_threshold_specs_from_text(FALLBACK_MEMORY_THRESHOLDS)
    page_memory_stats = _light_page_memory_stats(conn, page_days, memory_specs, rows=page_record_rows)
    timings["memory_stats_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    build_start = time.perf_counter()
    for index, day in enumerate(page_days, start=1):
        day_start = time.perf_counter()

        snap = snapshots_by_day.get(day)
        records = records_by_day.get(day) or []
        progress_row = progress_by_day.get(day)
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
    today_by_key, today_by_spelling = _today_item_lookup(today_items)
    _trace_step_done(trace, "overview_today_index_build", t, by_key=len(today_by_key), by_spell=len(today_by_spelling))

    t = _trace_step_start()
    out = []
    for p in records:
        today = _today_item_for_record(p, today_by_key, today_by_spelling)
        out.append(overview_payload_to_csv_row(p, today))
    _trace_step_done(trace, "overview_csv_rows_build", t, rows=len(out))
    return out


__all__ = [name for name in globals() if not name.startswith('__')]
