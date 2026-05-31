#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))

import momo_db_compact as compact  # noqa: E402


OLD_TABLES = {
    "compact_meta": "old_compact_meta",
    "compact_overview_day_checkpoints": "old_days",
    "compact_overview_day_records": "old_records",
    "compact_overview_patch_events": "old_record_events",
    "compact_study_day_initial_items": "old_study_items",
    "compact_study_day_patch_events": "old_study_events",
    "snapshots": "old_snaps",
    "snapshot_progress": "old_progress",
    "words": "old_words",
}


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def word_key_from(spelling: Any, voc_id: Any = None) -> str:
    text = str(spelling or "").strip()
    if text:
        return text
    vid = str(voc_id or "").strip()
    return f"voc:{vid}" if vid else ""


def rename_old_tables(conn: sqlite3.Connection) -> None:
    for old, tmp in OLD_TABLES.items():
        if table_exists(conn, tmp):
            conn.execute(f"DROP TABLE {tmp}")
        if table_exists(conn, old):
            conn.execute(f"ALTER TABLE {old} RENAME TO {tmp}")


def insert_word(conn: sqlite3.Connection, *, word_key: str, spelling: str, voc_id: Any, first_seen: Any, last_seen: Any) -> None:
    if not word_key:
        return
    conn.execute(
        """
        INSERT INTO words(word_key, spelling, voc_id, first_seen_at, last_seen_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(word_key) DO UPDATE SET
          voc_id=COALESCE(words.voc_id, excluded.voc_id),
          spelling=COALESCE(NULLIF(words.spelling, ''), excluded.spelling),
          first_seen_at=MIN(words.first_seen_at, excluded.first_seen_at),
          last_seen_at=MAX(words.last_seen_at, excluded.last_seen_at)
        """,
        (
            word_key,
            spelling or word_key,
            str(voc_id).strip() if voc_id not in (None, "") else None,
            str(first_seen or last_seen or ""),
            str(last_seen or first_seen or ""),
        ),
    )


def migrate_words(conn: sqlite3.Connection) -> None:
    if table_exists(conn, "old_words"):
        for row in conn.execute("SELECT * FROM old_words"):
            spelling = str(value(row, "spelling") or "").strip()
            voc_id = value(row, "voc_id")
            insert_word(
                conn,
                word_key=word_key_from(spelling, voc_id),
                spelling=spelling,
                voc_id=voc_id,
                first_seen=value(row, "first_seen_at"),
                last_seen=value(row, "last_seen_at"),
            )

    for table, spelling_col in (("old_records", "spelling"), ("old_study_items", "voc_spelling")):
        if not table_exists(conn, table):
            continue
        for row in conn.execute(f"SELECT * FROM {table}"):
            spelling = str(value(row, spelling_col) or "").strip()
            voc_id = value(row, "voc_id")
            seen = value(row, "snapshot_time") or value(row, "created_at")
            insert_word(
                conn,
                word_key=word_key_from(spelling, voc_id),
                spelling=spelling,
                voc_id=voc_id,
                first_seen=seen,
                last_seen=seen,
            )


def migrate_simple_tables(conn: sqlite3.Connection) -> None:
    if table_exists(conn, "old_snaps"):
        conn.execute(
            """
            INSERT OR REPLACE INTO snaps(snapshot_time, study_day_key, source, api_success, data_complete, incomplete_reason, created_at)
            SELECT snapshot_time, study_day_key, source, COALESCE(api_success,1), COALESCE(data_complete,1), incomplete_reason, created_at
            FROM old_snaps
            """
        )

    if table_exists(conn, "old_days"):
        conn.execute(
            """
            INSERT OR REPLACE INTO days(study_day_key, snapshot_time, record_count, created_at)
            SELECT study_day_key, snapshot_time, record_count, created_at
            FROM old_days
            """
        )

    if table_exists(conn, "old_progress"):
        conn.execute(
            """
            INSERT OR REPLACE INTO progress(
              snapshot_time, study_day_key, finished, total, study_time,
              first_forget_done, first_forget_all, vague_done, vague_all,
              familiar_done, familiar_all, new_words, review_words, pending_new_words,
              all_items_count, done_items_count, todo_items_count
            )
            SELECT
              snapshot_time, study_day_key, finished, total, study_time,
              first_forget_done, first_forget_all, vague_done, vague_all,
              familiar_done, familiar_all, new_words, review_words, pending_new_words,
              COALESCE(all_items_count,0), COALESCE(done_items_count,0), COALESCE(todo_items_count,0)
            FROM old_progress
            """
        )


def migrate_records(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "old_records"):
        return
    for row in conn.execute("SELECT * FROM old_records"):
        word_key = word_key_from(value(row, "spelling"), value(row, "voc_id"))
        if not word_key:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO records(
              study_day_key, word_key, snapshot_time, voc_id, spelling, add_date, first_study_date,
              last_study_date, next_study_date, last_response, last_response_cn, study_count,
              tags_text, tag_well, tag_sticking, current_state, is_overdue, state_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value(row, "study_day_key"),
                word_key,
                value(row, "snapshot_time"),
                value(row, "voc_id"),
                value(row, "spelling") or word_key,
                value(row, "add_date"),
                value(row, "first_study_date"),
                value(row, "last_study_date"),
                value(row, "next_study_date"),
                value(row, "last_response"),
                value(row, "last_response_cn"),
                value(row, "study_count"),
                value(row, "tags_text"),
                value(row, "tag_well"),
                value(row, "tag_sticking"),
                value(row, "current_state"),
                value(row, "is_overdue"),
                value(row, "state_hash"),
            ),
        )


def migrate_study_items(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "old_study_items"):
        return
    for row in conn.execute("SELECT * FROM old_study_items"):
        word_key = word_key_from(value(row, "voc_spelling"), value(row, "voc_id"))
        if not word_key:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO study_items(
              study_day_key, snapshot_time, word_key, voc_id, voc_spelling, order_index,
              first_response, is_new, is_finished, present_state, state_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value(row, "study_day_key"),
                value(row, "snapshot_time"),
                word_key,
                value(row, "voc_id"),
                value(row, "voc_spelling") or word_key,
                value(row, "order_index"),
                value(row, "first_response"),
                value(row, "is_new"),
                value(row, "is_finished"),
                value(row, "present_state"),
                value(row, "state_hash"),
            ),
        )


def voc_to_word_map(conn: sqlite3.Connection) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in conn.execute("SELECT voc_id, word_key FROM words WHERE voc_id IS NOT NULL AND voc_id<>''"):
        out[str(row["voc_id"])] = str(row["word_key"])
    return out


def migrate_events(conn: sqlite3.Connection) -> None:
    mapping = voc_to_word_map(conn)
    for old_table, new_table, fields in (
        ("old_record_events", "record_events", compact.OVERVIEW_EVENT_FIELDS),
        ("old_study_events", "study_events", compact.STUDY_ITEM_EVENT_FIELDS),
    ):
        if not table_exists(conn, old_table):
            continue
        new_cols = columns(conn, new_table)
        for row in conn.execute(f"SELECT * FROM {old_table}"):
            voc_id = str(value(row, "voc_id") or "").strip()
            word_key = mapping.get(voc_id) or word_key_from(None, voc_id)
            if not word_key:
                continue
            payload = {
                "snapshot_time": value(row, "snapshot_time"),
                "study_day_key": value(row, "study_day_key"),
                "word_key": word_key,
                "voc_id": voc_id or None,
                "event_type": value(row, "event_type"),
                "created_at": value(row, "created_at"),
            }
            for field in fields:
                if field in row.keys() and field in new_cols:
                    payload[field] = value(row, field)
                old_field = f"old_{field}"
                if old_field in row.keys() and old_field in new_cols:
                    payload[old_field] = value(row, old_field)
            cols = list(payload)
            conn.execute(
                f"INSERT OR REPLACE INTO {new_table}({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
                [payload[col] for col in cols],
            )


def drop_old_tables(conn: sqlite3.Connection) -> None:
    for tmp in OLD_TABLES.values():
        if table_exists(conn, tmp):
            conn.execute(f"DROP TABLE {tmp}")


def migrate(db_path: Path) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_suffix(db_path.suffix + f".pre_v4_{stamp}.bak")
    shutil.copy2(db_path, backup)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        rename_old_tables(conn)
        compact._ensure_schema(conn)
        migrate_words(conn)
        migrate_simple_tables(conn)
        migrate_records(conn)
        migrate_study_items(conn)
        migrate_events(conn)
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("schema_version", compact.COMPACT_SCHEMA_VERSION))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,datetime('now'))", ("migrated_at",))
        review_count = compact.rebuild_review_index(conn)
        drop_old_tables(conn)
        conn.execute("PRAGMA optimize")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"migrated {db_path}")
    print(f"backup {backup}")
    print(f"reviews {review_count}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "momo.sqlite"))
    args = parser.parse_args()
    migrate(Path(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
