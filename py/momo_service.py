#!/usr/bin/env python3
"""Local SQLite-backed HTTP service for the Maimemo dashboard.

新版轻量路由版：
- /api/dashboard-data-page 只走 momo_db.get_dashboard_data_page_light()
- 不保留 /api/dashboard-data
- 不保留 /api/dashboard-data-page?workspaceOnly=1 / includeMeta / date 兼容链路
- /api/today-workspace 独立返回当日时间点数据
- /api/alerts 独立返回减少提醒
- /api/fsrs-prediction 独立返回后端 FSRS 预测数据

注意：
momo_db.py 必须提供 get_dashboard_data_page_light(conn, offset, limit, order, memory_thresholds_text)。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from momo_api import (
    add_words_to_study,
    advance_study,
    create_notepad,
    delete_notepad,
    get_notepad,
    list_notepads,
    query_vocabulary,
    read_token as read_api_token,
    update_notepad,
)
from momo_api_client import (
    assert_success,
    build_study_count_lookup,
    count_first_response,
    count_new_words,
    count_pending_new_words,
    count_review_words,
    enrich_items_with_study_count,
    fetch_all_records,
    post_json,
    read_token,
    today_items,
)
from momo_config import backend_host, backend_port, sync_cooldown_minutes, token_path
from momo_db import (
    db_connect,
    delete_latest_incomplete_snapshot,
    get_current_records_by_words,
    get_dashboard_data_page_light,
    get_latest_decrease_alerts,
    get_local_word_index,
    get_today_workspace,
    import_api_snapshot,
    now_bj,
    query_words,
    record_incomplete_snapshot,
    setup_schema,
)

try:
    from momo_fsrs import build_fsrs_dashboard_payload
except Exception:
    build_fsrs_dashboard_payload = None

try:
    from lemminflect import getAllLemmas
except Exception:
    getAllLemmas = None


CLOUD_WRITE_CONFIRM_HEADER = "X-Momo-Cloud-Write-Confirm"
CLOUD_WRITE_CONFIRM_VALUE = "waited-5s"

CLOUD_WRITE_PATHS = {
    "/api/notepads/create",
    "/api/notepads/update",
    "/api/notepads/delete",
    "/api/notepads/add-words",
    "/api/study/advance",
    "/api/study/add-words",
}


def api_voc_id(obj):
    if not isinstance(obj, dict):
        return ""
    value = obj.get("voc_id")
    if value in (None, ""):
        return ""
    return str(value).strip()


class ClientDisconnected(Exception):
    """Raised when the browser cancels a request while the service is writing."""


def service_log(message: str) -> None:
    ts = now_bj().strftime("%H时%M分%S秒")
    print(f"[{ts}] {message}", file=sys.stderr, flush=True)


def sync_api_to_sqlite_browser(*, token_file: Path, db_path: Path, log_func=print) -> dict:
    """Browser-triggered network sync: API -> SQLite only."""
    if not token_file.exists():
        raise RuntimeError(f"未找到 token 文件: {token_file}")

    token = read_token(token_file)
    captured_at = now_bj()

    with db_connect(db_path) as conn:
        deleted = delete_latest_incomplete_snapshot(conn)

    if deleted:
        log_func(f"发现上次未完整快照，已删除后重新拉取: {deleted.get('snapshotTime')}；原因: {deleted.get('reason') or '-'}")

    log_func("浏览器联网同步：正在获取今日数据和全量学习记录...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_progress = executor.submit(post_json, token, "get_study_progress")
        future_all = executor.submit(post_json, token, "get_today_items", {"limit": 1000})
        future_done = executor.submit(post_json, token, "get_today_items", {"is_finished": True, "limit": 1000})
        future_todo = executor.submit(post_json, token, "get_today_items", {"is_finished": False, "limit": 1000})
        future_records = executor.submit(fetch_all_records, token)

        progress = future_progress.result()
        all_items = future_all.result()
        done_items = future_done.result()
        todo_items = future_todo.result()
        records, expected_total, leaf_ranges = future_records.result()

    for name, data in [
        ("今日学习进度", progress),
        ("今日单词明细", all_items),
        ("今日已完成单词", done_items),
        ("今日未完成单词", todo_items),
    ]:
        assert_success(name, data)

    if expected_total and len(records) != expected_total:
        reason = f"全量学习记录未拉满：接口总数={expected_total}，实际={len(records)}"
        with db_connect(db_path) as conn:
            marker = record_incomplete_snapshot(conn, captured_at=captured_at, source="browser", reason=reason)
        log_func(f"{reason}。本次不写入统计数据，仅记录未完整标记: {marker}")
        return {
            "success": False,
            "incomplete": True,
            "error": reason,
            "snapshotTime": marker,
            "expectedTotal": expected_total,
            "actualTotal": len(records),
            "deletedPreviousIncomplete": deleted,
        }

    log_func(f"联网完成：全量学习记录 {len(records)} 条。")
    log_func("正在整理今日快照并补齐学习次数...")
    study_count_lookup = build_study_count_lookup(records)
    all_matched = enrich_items_with_study_count(all_items, study_count_lookup)
    done_matched = enrich_items_with_study_count(done_items, study_count_lookup)
    todo_matched = enrich_items_with_study_count(todo_items, study_count_lookup)

    log_func(f"正在写入 SQLite: {db_path}")
    with db_connect(db_path) as conn:
        result = import_api_snapshot(
            conn,
            progress=progress,
            all_items=all_items,
            done_items=done_items,
            todo_items=todo_items,
            records=records,
            expected_total=expected_total,
            leaf_ranges=leaf_ranges,
            captured_at=captured_at,
            source="browser",
        )
        alerts = get_latest_decrease_alerts(conn)

    progress_data = progress.get("data", {}).get("progress", {}) or {}
    today_first_forget = count_first_response(done_items, "FORGET")
    today_all_first_forget = count_first_response(all_items, "FORGET")
    today_done_vague = count_first_response(done_items, "VAGUE")
    today_all_vague = count_first_response(all_items, "VAGUE")
    today_familiar = count_first_response(done_items, "FAMILIAR")
    today_new_words = count_new_words(all_items)
    today_review_words = count_review_words(all_items)
    today_pending_new_words = count_pending_new_words(all_items)

    log_func("------------------------------------------------")
    log_func("SQLite 写入完成（浏览器联网同步）")
    log_func(f"数据库: {db_path}")
    log_func(f"快照: {result.day_key} / {result.snapshot_time}")
    log_func(f"当日任务变化: {result.today_item_changes} 条；总览变化: {result.overview_changes} 条。")
    log_func(f"入库单词状态: {result.inserted_records}")
    log_func(f"接口总数: {expected_total}")
    log_func(f"自动拆分范围数: {leaf_ranges}")
    log_func(f"今日进度：已完成={progress_data.get('finished', 0)}，总数={progress_data.get('total', 0)}。")
    log_func(
        "今日统计："
        f"忘记={today_first_forget}，"
        f"全部忘记={today_all_first_forget}，"
        f"模糊={today_done_vague}/{today_all_vague}，"
        f"认识={today_familiar}，"
        f"新学={today_new_words}，"
        f"复习={today_review_words}，"
        f"待新学={today_pending_new_words}。"
    )
    log_func(
        "学习次数匹配："
        f"全部={all_matched}/{len(today_items(all_items))}，"
        f"已完成={done_matched}/{len(today_items(done_items))}，"
        f"未完成={todo_matched}/{len(today_items(todo_items))}。"
    )

    if alerts:
        for alert in alerts:
            log_func("提醒：" + str(alert.get("message") or alert))

    return {
        "success": True,
        "snapshotTime": result.snapshot_time,
        "studyDayKey": result.day_key,
        "todayItemChanges": result.today_item_changes,
        "overviewChanges": result.overview_changes,
        "insertedRecords": result.inserted_records,
        "expectedTotal": expected_total,
        "actualTotal": len(records),
        "leafRanges": leaf_ranges,
        "alerts": alerts,
        "deletedPreviousIncomplete": deleted,
        "source": "browser",
    }


def read_sync_cooldown_minutes(root_dir: Path) -> int:
    return sync_cooldown_minutes(root_dir)


def latest_complete_snapshot(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT snapshot_time
        FROM snapshots
        WHERE COALESCE(data_complete, 1)=1 AND COALESCE(api_success, 1)=1
        ORDER BY snapshot_time DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["snapshot_time"]) if row else None


def latest_snapshot_status(conn: sqlite3.Connection) -> dict[str, object] | None:
    row = conn.execute(
        """
        SELECT snapshot_time,
               COALESCE(data_complete, 1) AS data_complete,
               COALESCE(api_success, 1) AS api_success
        FROM snapshots
        ORDER BY snapshot_time DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return {
        "snapshot_time": str(row["snapshot_time"]),
        "complete": int(row["data_complete"] or 0) == 1 and int(row["api_success"] or 0) == 1,
    }


def minutes_since_snapshot(snapshot_time: str) -> int | None:
    try:
        dt = __import__("datetime").datetime.fromisoformat(str(snapshot_time))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=now_bj().tzinfo)
        return int((now_bj() - dt.astimezone(now_bj().tzinfo)).total_seconds() // 60)
    except Exception:
        return None


COMMON_EN_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "while", "for", "to", "of", "in", "on", "at", "by", "from", "with", "without", "about", "into", "over", "after", "before", "between", "through", "during", "under", "again", "further", "once",
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "done", "have", "has", "had", "having", "can", "could", "should", "would", "may", "might", "must", "shall", "will",
    "i", "me", "my", "mine", "we", "us", "our", "ours", "you", "your", "yours", "he", "him", "his", "she", "her", "hers", "it", "its", "they", "them", "their", "theirs", "this", "that", "these", "those", "there", "here", "which", "who", "whom", "whose", "what", "why", "how",
    "not", "no", "nor", "so", "too", "very", "just", "than", "as", "also", "such", "only", "own", "same", "more", "most", "some", "any", "each", "few", "all", "both", "other", "another",
}


def normalize_article_word(raw: str) -> str:
    word = str(raw or "").strip().lower().strip("'\u2019-")
    word = word.replace("\u2019", "'")
    if not word:
        return ""
    if word.endswith("'s"):
        word = word[:-2]
    word = word.replace("'", "")
    return word


ARTICLE_FORM_CACHE: dict[str, set[str]] = {}


def article_word_forms(word: str) -> set[str]:
    base = normalize_article_word(word)
    if not base:
        return set()

    cached = ARTICLE_FORM_CACHE.get(base)
    if cached is not None:
        return set(cached)

    forms = {base}
    if getAllLemmas is not None:
        try:
            lemmas = getAllLemmas(base)
            pools = lemmas.values() if isinstance(lemmas, dict) else [lemmas]
            for pool in pools:
                if isinstance(pool, (list, tuple, set)):
                    for item in pool:
                        normalized = normalize_article_word(str(item))
                        if normalized:
                            forms.add(normalized)
                else:
                    normalized = normalize_article_word(str(pool))
                    if normalized:
                        forms.add(normalized)
        except Exception:
            pass

    ARTICLE_FORM_CACHE[base] = set(forms)
    return forms


def build_article_form_index(words: set[str] | list[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for word in words:
        normalized = normalize_article_word(word)
        if not normalized:
            continue
        for form in article_word_forms(normalized):
            index.setdefault(form, normalized)
    return index


def find_form_match(word: str, exact_words: set[str] | dict[str, object], form_index: dict[str, str], *, use_forms: bool) -> tuple[bool, str]:
    normalized = normalize_article_word(word)
    if not normalized:
        return False, ""
    if normalized in exact_words:
        return True, normalized
    if not use_forms:
        return False, ""
    for form in article_word_forms(normalized):
        matched = form_index.get(form)
        if matched:
            return True, matched
    return False, ""


def extract_article_words(text: str, *, min_len: int = 2, skip_stopwords: bool = True) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    first_order: dict[str, int] = {}
    for idx, match in enumerate(re.finditer(r"[A-Za-z][A-Za-z'\u2019-]*", text or "")):
        token = normalize_article_word(match.group(0))
        if not token or len(token) < min_len:
            continue
        if skip_stopwords and token in COMMON_EN_STOPWORDS:
            continue
        counts[token] = counts.get(token, 0) + 1
        first_order.setdefault(token, idx)
    return [
        {"word": word, "count": counts[word], "firstOrder": first_order[word]}
        for word in sorted(counts, key=lambda item: first_order[item])
    ]


def parse_notepad_words(notepad: dict[str, object]) -> set[str]:
    words: set[str] = set()
    parsed = notepad.get("list") if isinstance(notepad, dict) else None

    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            word = normalize_article_word(data.get("word") or item.get("word") or "")
            if word:
                words.add(word)

    content = str(notepad.get("content") or "") if isinstance(notepad, dict) else ""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        word = normalize_article_word(stripped.split()[0])
        if word:
            words.add(word)

    return words


def merge_notepad_content(content: str, words: list[str], chapter: str = "") -> tuple[str, list[str]]:
    existing = set()
    for line in (content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        word = normalize_article_word(stripped.split()[0])
        if word:
            existing.add(word)

    cleaned = []
    seen = set()
    for item in words:
        word = normalize_article_word(item)
        if not word or word in existing or word in seen:
            continue
        cleaned.append(word)
        seen.add(word)

    if not cleaned:
        return content or "", []

    base = (content or "").rstrip()
    lines = []
    if base:
        lines.append(base)

    if chapter:
        heading = chapter.strip()
        if heading and not heading.startswith("#"):
            heading = "# " + heading
        if heading:
            lines.append(heading)

    lines.extend(cleaned)
    return "\n".join(lines) + "\n", cleaned


def chunked(seq, size=1000):
    for index in range(0, len(seq), size):
        yield seq[index:index + size]


class MomoRequestHandler(SimpleHTTPRequestHandler):
    server_version = "MomoSQLiteHTTP/2.0"

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        rel = unquote(parsed.path).lstrip("/") or "history_dashboard.html"
        safe = Path(self.server.root_dir, *[part for part in rel.split("/") if part and part not in (".", "..")])
        return str(safe)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        dump_start = time.perf_counter()
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        dump_ms = (time.perf_counter() - dump_start) * 1000

        write_start = time.perf_counter()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError) as exc:
            self.close_connection = True
            service_log(f"客户端已断开，停止写响应 status={int(status)} size={len(data) / 1024:.1f}KB，原因={exc.__class__.__name__}")
            raise ClientDisconnected() from exc

        write_ms = (time.perf_counter() - write_start) * 1000
        if len(data) >= 200_000 or dump_ms >= 100 or write_ms >= 100:
            service_log(f"JSON 响应 status={int(status)} size={len(data) / 1024:.1f}KB dump={dump_ms:.1f}ms write={write_ms:.1f}ms")

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"success": False, "error": message}, status)

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def api_token(self) -> str:
        token_file = token_path(Path(self.server.root_dir))
        if not token_file.exists():
            raise RuntimeError(f"未找到 token 文件: {token_file}")
        return read_api_token(token_file)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if parsed.path.startswith("/api/"):
            self.handle_api(parsed)
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/sync":
            self.handle_sync()
            return
        if parsed.path == "/api/fsrs-prediction":
            self.handle_fsrs_prediction()
            return
        if parsed.path.startswith("/api/notepads") or parsed.path in (
            "/api/article/analyze",
            "/api/study/advance",
            "/api/study/add-words",
        ):
            self.handle_write_api(parsed)
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, f"未知接口: {parsed.path}")

    def handle_fsrs_prediction(self) -> None:
        request_start = time.perf_counter()
        self.server.begin_api_request(True)
        try:
            if build_fsrs_dashboard_payload is None:
                raise RuntimeError("未找到 momo_fsrs.build_fsrs_dashboard_payload")
            _body = self.read_json_body()
            with db_connect(self.server.db_path) as conn:
                result = build_fsrs_dashboard_payload(conn, Path(self.server.root_dir))
            service_log(f"API /api/fsrs-prediction 完成 itemCount={result.get('itemCount')} 耗时 {(time.perf_counter() - request_start) * 1000:.1f}ms")
            self.send_json({"success": True, "prediction": result})
        except ClientDisconnected:
            service_log(f"API /api/fsrs-prediction：浏览器已取消请求，总耗时 {(time.perf_counter() - request_start) * 1000:.1f}ms")
        except Exception as exc:
            service_log(f"API /api/fsrs-prediction 异常: {exc}\n{traceback.format_exc()}")
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"FSRS 预测错误: {exc}")
        finally:
            self.server.end_api_request(True)

    def handle_write_api(self, parsed) -> None:
        try:
            body = self.read_json_body()
            path = parsed.path

            if path in CLOUD_WRITE_PATHS and self.headers.get(CLOUD_WRITE_CONFIRM_HEADER) != CLOUD_WRITE_CONFIRM_VALUE:
                self.send_error_json(HTTPStatus.PRECONDITION_REQUIRED, "危险云端写入需要先在页面确认，并等待 5 秒后再执行")
                return

            if path == "/api/notepads/create":
                token = self.api_token()
                title = str(body.get("title") or "").strip()
                if not title:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "云词本标题不能为空")
                    return
                tags = body.get("tags") if isinstance(body.get("tags"), list) else []
                notepad = create_notepad(
                    token,
                    title=title,
                    brief=str(body.get("brief") or ""),
                    content=str(body.get("content") or ""),
                    tags=[str(item) for item in tags],
                    status=str(body.get("status") or "PUBLISHED"),
                )
                self.send_json({"success": True, "notepad": notepad})
                return

            if path == "/api/notepads/update":
                token = self.api_token()
                notepad_id = str(body.get("id") or body.get("notepadId") or "").strip()
                if not notepad_id:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少云词本 ID")
                    return
                existing = get_notepad(token, notepad_id)
                tags = body.get("tags") if isinstance(body.get("tags"), list) else existing.get("tags") or []
                notepad = update_notepad(
                    token,
                    notepad_id,
                    title=str(body.get("title") or existing.get("title") or "未命名云词本"),
                    brief=str(body.get("brief") if body.get("brief") is not None else existing.get("brief") or ""),
                    content=str(body.get("content") if body.get("content") is not None else existing.get("content") or ""),
                    tags=[str(item) for item in tags],
                    status=str(body.get("status") or existing.get("status") or "PUBLISHED"),
                )
                self.send_json({"success": True, "notepad": notepad})
                return

            if path == "/api/notepads/delete":
                token = self.api_token()
                notepad_id = str(body.get("id") or body.get("notepadId") or "").strip()
                if not notepad_id:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少云词本 ID")
                    return
                notepad = delete_notepad(token, notepad_id)
                self.send_json({"success": True, "notepad": notepad})
                return

            if path == "/api/notepads/add-words":
                token = self.api_token()
                words = body.get("words") if isinstance(body.get("words"), list) else []
                words = [normalize_article_word(str(item)) for item in words]
                words = [item for item in words if item]
                if not words:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "没有可写入的单词")
                    return

                notepad_id = str(body.get("id") or body.get("notepadId") or "").strip()
                chapter = str(body.get("chapter") or "")
                if notepad_id:
                    existing = get_notepad(token, notepad_id)
                    content, added_words = merge_notepad_content(str(existing.get("content") or ""), words, chapter)
                    notepad = update_notepad(
                        token,
                        notepad_id,
                        title=str(existing.get("title") or body.get("title") or "文章生词"),
                        brief=str(existing.get("brief") or ""),
                        content=content,
                        tags=existing.get("tags") or [],
                        status=str(existing.get("status") or "PUBLISHED"),
                    )
                else:
                    title = str(body.get("title") or "文章生词").strip()
                    content, added_words = merge_notepad_content("", words, chapter)
                    notepad = create_notepad(
                        token,
                        title=title,
                        brief=str(body.get("brief") or "从总览页文章分析生成"),
                        content=content,
                        tags=[],
                        status="PUBLISHED",
                    )

                self.send_json({"success": True, "notepad": notepad, "addedWords": added_words, "addedCount": len(added_words)})
                return

            if path == "/api/article/analyze":
                text = str(body.get("text") or "")
                min_len = int(body.get("minLength") or 2)
                skip_stopwords = bool(body.get("skipStopwords", True))
                exclude_inflections = bool(body.get("excludeInflections", False))

                if exclude_inflections and getAllLemmas is None:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "排除变形功能需要安装 Python 包 lemminflect，请执行 pip install lemminflect")
                    return

                words = extract_article_words(text, min_len=min_len, skip_stopwords=skip_stopwords)
                with db_connect(self.server.db_path) as conn:
                    local_index = get_local_word_index(conn)

                notepad_words = set()
                notepad_id = str(body.get("notepadId") or "").strip()
                if notepad_id:
                    token = self.api_token()
                    notepad_words = parse_notepad_words(get_notepad(token, notepad_id))

                mode = str(body.get("mode") or "local").strip()
                local_form_index = build_article_form_index(set(local_index.keys())) if exclude_inflections else {}
                notepad_form_index = build_article_form_index(notepad_words) if exclude_inflections and notepad_id else {}

                results = []
                excluded_by_inflection = 0
                for item in words:
                    word = str(item["word"])
                    in_local, local_match = find_form_match(word, local_index, local_form_index, use_forms=exclude_inflections)
                    in_notepad, notepad_match = find_form_match(word, notepad_words, notepad_form_index, use_forms=exclude_inflections) if notepad_id else (False, "")

                    if exclude_inflections and (local_match or notepad_match) and word not in (local_match, notepad_match):
                        excluded_by_inflection += 1

                    if mode == "notepad":
                        missing = not in_notepad
                    elif mode == "both":
                        missing = (not in_local) and (not in_notepad)
                    else:
                        missing = not in_local

                    if missing:
                        results.append({
                            "word": word,
                            "count": item["count"],
                            "inLocalPlan": in_local,
                            "inNotepad": in_notepad,
                            "localMatchedWord": local_match,
                            "notepadMatchedWord": notepad_match,
                            "forms": sorted(article_word_forms(word)) if exclude_inflections else [word],
                        })

                self.send_json({
                    "success": True,
                    "uniqueCount": len(words),
                    "missingCount": len(results),
                    "missingWords": results,
                    "mode": mode,
                    "excludeInflections": exclude_inflections,
                    "excludedByInflectionCount": excluded_by_inflection,
                })
                return

            if path == "/api/study/advance":
                token = self.api_token()
                voc_ids = body.get("vocIds") if isinstance(body.get("vocIds"), list) else []
                words = body.get("words") if isinstance(body.get("words"), list) else []
                if not voc_ids and words:
                    for part in chunked([normalize_article_word(str(item)) for item in words if str(item).strip()], 1000):
                        voc_ids.extend([vid for vid in (api_voc_id(v) for v in query_vocabulary(token, spellings=part)) if vid])
                voc_ids = [str(item) for item in voc_ids if str(item).strip()]
                if not voc_ids:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "没有可提前复习的 voc_id")
                    return

                total_advanced = 0
                details = []
                for part in chunked(voc_ids, 1000):
                    data = advance_study(token, part)
                    details.append(data)
                    total_advanced += int(data.get("advanced_count") or data.get("advancedCount") or 0)

                self.send_json({"success": True, "advancedCount": total_advanced, "details": details, "requestedCount": len(set(voc_ids))})
                return

            if path == "/api/study/add-words":
                token = self.api_token()
                voc_ids = body.get("vocIds") if isinstance(body.get("vocIds"), list) else []
                words = body.get("words") if isinstance(body.get("words"), list) else []
                if not voc_ids and words:
                    for part in chunked([normalize_article_word(str(item)) for item in words if str(item).strip()], 1000):
                        voc_ids.extend([vid for vid in (api_voc_id(v) for v in query_vocabulary(token, spellings=part)) if vid])
                voc_ids = [str(item) for item in voc_ids if str(item).strip()]
                if not voc_ids:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "没有可添加的 voc_id")
                    return

                total_added = 0
                details = []
                advance = bool(body.get("advance", False))
                for part in chunked(voc_ids, 1000):
                    data = add_words_to_study(token, part, advance=advance)
                    details.append(data)
                    total_added += int(data.get("added_count") or data.get("addedCount") or 0)

                self.send_json({"success": True, "addedCount": total_added, "details": details, "requestedCount": len(set(voc_ids))})
                return

            self.send_error_json(HTTPStatus.NOT_FOUND, f"未知接口: {path}")

        except Exception as exc:
            service_log(f"POST 接口异常 {parsed.path}: {exc}\n{traceback.format_exc()}")
            self.send_json({"success": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_sync(self) -> None:
        parsed = urlparse(self.path)
        query = {key: value[-1] if value else "" for key, value in parse_qs(parsed.query).items()}
        token_file = token_path(Path(self.server.root_dir))

        cooldown_min = read_sync_cooldown_minutes(Path(self.server.root_dir))
        if cooldown_min > 0:
            try:
                with db_connect(self.server.db_path) as conn:
                    latest_status = latest_snapshot_status(conn)
                    latest = latest_complete_snapshot(conn)

                if latest_status and not latest_status.get("complete"):
                    pass
                elif latest:
                    diff_min = minutes_since_snapshot(latest)
                    if diff_min is not None and diff_min < cooldown_min:
                        force = str(query.get("force") or "").lower() in ("1", "true", "yes")
                        if not force:
                            self.send_json({
                                "success": True,
                                "needConfirm": True,
                                "reason": "cooldown",
                                "message": f"距离上次快照仅 {diff_min} 分钟（阈值: {cooldown_min} 分钟），是否仍要联网同步？",
                                "latestSnapshotTime": latest,
                                "minutesSinceLatest": diff_min,
                                "cooldownMinutes": cooldown_min,
                            })
                            return
            except Exception as exc:
                service_log(f"读取上次快照时间失败: {exc}")

        if not token_file.exists():
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"未找到 token 文件: {token_file}")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        log_queue: queue.Queue[str | None] = queue.Queue()
        result_holder: dict = {}

        def logger(msg):
            text = str(msg)
            log_queue.put(text)
            print(text, flush=True)

        def stream(line):
            data = (line + "\n").encode("utf-8")
            try:
                self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                raise ClientDisconnected()

        def do_sync():
            try:
                result_holder["result"] = sync_api_to_sqlite_browser(
                    token_file=token_file,
                    db_path=self.server.db_path,
                    log_func=logger,
                )
            except Exception as exc:
                logger(f"同步失败: {exc}")
                result_holder["error"] = str(exc)
            finally:
                log_queue.put(None)

        sync_thread = threading.Thread(target=do_sync, daemon=True)
        sync_thread.start()

        try:
            while True:
                try:
                    msg = log_queue.get(timeout=30)
                except queue.Empty:
                    stream(json.dumps({"ping": True}, ensure_ascii=False))
                    continue

                if msg is None:
                    break
                stream(json.dumps({"log": msg}, ensure_ascii=False))

            sync_thread.join(timeout=10)
            if "result" in result_holder:
                stream(json.dumps({"result": result_holder["result"]}, ensure_ascii=False))
            else:
                stream(json.dumps({"error": result_holder.get("error", "未知错误")}, ensure_ascii=False))

        except ClientDisconnected:
            pass
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass

    def handle_api(self, parsed) -> None:
        request_start = time.perf_counter()
        query = {key: value[-1] if value else "" for key, value in parse_qs(parsed.query).items()}
        service_log(f"API 开始 {parsed.path}?{parsed.query or ''}")

        heavy_paths = {"/api/dashboard-data-page", "/api/words", "/api/today-workspace"}
        track_request = parsed.path != "/api/health"
        is_heavy_request = parsed.path in heavy_paths

        if track_request:
            self.server.begin_api_request(is_heavy_request)

        try:
            open_start = time.perf_counter()
            with db_connect(self.server.db_path) as conn:
                service_log(f"API 数据库连接+打开 {parsed.path} 耗时 {(time.perf_counter() - open_start) * 1000:.1f}ms")

                if parsed.path == "/api/health":
                    row = conn.execute("SELECT value FROM compact_meta WHERE key='schema_version'").fetchone()
                    payload = {
                        "success": True,
                        "database": str(self.server.db_path),
                        "schemaVersion": row["value"] if row else "unknown",
                    }
                    payload.update(self.server.runtime_health())
                    self.send_json(payload)
                    return

                if parsed.path == "/api/dashboard-data-page":
                    offset = int(query.get("offset") or 0)
                    limit = int(query.get("limit") or 30)
                    order = str(query.get("order") or "asc")
                    memory_thresholds = str(query.get("memoryThresholds") or "").strip()

                    build_start = time.perf_counter()
                    payload = get_dashboard_data_page_light(
                        conn,
                        offset=offset,
                        limit=limit,
                        order=order,
                        memory_thresholds_text=memory_thresholds,
                    )
                    service_log(
                        f"API /api/dashboard-data-page light offset={offset} limit={limit} "
                        f"loaded={payload.get('loaded')} total={payload.get('total')} "
                        f"db={payload.get('timings', {}).get('total_db_ms')}ms "
                        f"构建耗时={(time.perf_counter() - build_start) * 1000:.1f}ms "
                        f"总耗时={(time.perf_counter() - request_start) * 1000:.1f}ms"
                    )
                    self.send_json(payload)
                    return

                if parsed.path == "/api/alerts":
                    build_start = time.perf_counter()
                    alerts = get_latest_decrease_alerts(conn)
                    service_log(f"API /api/alerts count={len(alerts or [])} 耗时 {(time.perf_counter() - build_start) * 1000:.1f}ms")
                    self.send_json({"success": True, "alerts": alerts})
                    return

                if parsed.path == "/api/today-workspace":
                    workspace_date = str(query.get("date") or "").strip() or None
                    build_start = time.perf_counter()
                    payload = {"success": True, "todayWorkspace": get_today_workspace(conn, day_key=workspace_date)}
                    service_log(f"API /api/today-workspace date={workspace_date} 耗时 {(time.perf_counter() - build_start) * 1000:.1f}ms")
                    self.send_json(payload)
                    return

                if parsed.path == "/api/words":
                    build_start = time.perf_counter()
                    payload = query_words(conn, query)
                    service_log(
                        f"API /api/words date={payload.get('date')} total={payload.get('total')} "
                        f"page={payload.get('page')} pageSize={payload.get('pageSize')} "
                        f"items={len(payload.get('items') or [])} 耗时={(time.perf_counter() - build_start) * 1000:.1f}ms "
                        f"明细={payload.get('timings', {})}"
                    )
                    self.send_json(payload)
                    return

                if parsed.path == "/api/records-by-words":
                    words = [item.strip() for item in str(query.get("words") or "").split(",") if item.strip()]
                    self.send_json({"success": True, "items": get_current_records_by_words(conn, words)})
                    return

                if parsed.path == "/api/notepads":
                    token = self.api_token()
                    self.send_json({"success": True, **list_notepads(token, int(query.get("limit") or 10), int(query.get("offset") or 0))})
                    return

                if parsed.path.startswith("/api/notepads/"):
                    token = self.api_token()
                    notepad_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json({"success": True, "notepad": get_notepad(token, notepad_id)})
                    return

                if parsed.path == "/api/days":
                    rows = conn.execute(
                        """
                        SELECT DISTINCT study_day_key
                        FROM snapshots
                        WHERE COALESCE(data_complete, 1)=1
                          AND COALESCE(api_success, 1)=1
                        ORDER BY study_day_key
                        """
                    ).fetchall()
                    self.send_json({"success": True, "days": [row["study_day_key"] for row in rows]})
                    return

                self.send_error_json(HTTPStatus.NOT_FOUND, f"未知接口: {parsed.path}")

        except ClientDisconnected:
            service_log(f"API 结束 {parsed.path}：浏览器已取消请求，总耗时 {(time.perf_counter() - request_start) * 1000:.1f}ms")
            return
        except sqlite3.Error as exc:
            service_log(f"API 数据库异常 {parsed.path}: {exc}\n{traceback.format_exc()}")
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"数据库错误: {exc}")
        except Exception as exc:
            service_log(f"API 服务异常 {parsed.path}: {exc}\n{traceback.format_exc()}")
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务错误: {exc}")
        finally:
            if track_request:
                self.server.end_api_request(is_heavy_request)


class MomoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler_cls, root_dir: Path, db_path: Path):
        super().__init__(server_address, handler_cls)
        self.root_dir = root_dir
        self.db_path = db_path
        self.started_at = time.time()
        self._request_lock = threading.Lock()
        self.active_api_requests = 0
        self.active_heavy_requests = 0
        self.max_active_api_requests = 0
        self.max_active_heavy_requests = 0
        self.last_heavy_finished_at = 0.0

    def begin_api_request(self, heavy: bool = False) -> None:
        with self._request_lock:
            self.active_api_requests += 1
            self.max_active_api_requests = max(self.max_active_api_requests, self.active_api_requests)
            if heavy:
                self.active_heavy_requests += 1
                self.max_active_heavy_requests = max(self.max_active_heavy_requests, self.active_heavy_requests)

    def end_api_request(self, heavy: bool = False) -> None:
        with self._request_lock:
            self.active_api_requests = max(0, self.active_api_requests - 1)
            if heavy:
                self.active_heavy_requests = max(0, self.active_heavy_requests - 1)
                self.last_heavy_finished_at = time.time()

    def runtime_health(self) -> dict[str, object]:
        with self._request_lock:
            active_api = self.active_api_requests
            active_heavy = self.active_heavy_requests
            max_api = self.max_active_api_requests
            max_heavy = self.max_active_heavy_requests
            last_heavy = self.last_heavy_finished_at
        return {
            "serviceKind": "momo_sqlite_dashboard",
            "serviceVersion": "20260528_light_routes_no_legacy",
            "uptimeSeconds": round(max(0.0, time.time() - self.started_at), 3),
            "activeApiRequests": active_api,
            "activeHeavyRequests": active_heavy,
            "maxActiveApiRequests": max_api,
            "maxActiveHeavyRequests": max_heavy,
            "lastHeavyFinishedAgoSeconds": None if not last_heavy else round(max(0.0, time.time() - last_heavy), 3),
            "idle": active_api == 0 and active_heavy == 0,
        }


def project_root_from_service() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    root_default = project_root_from_service()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="项目根目录，用于服务静态网页文件")
    parser.add_argument("--db", type=Path, required=True, help="SQLite 数据库路径")
    parser.add_argument("--host", default=backend_host(root_default))
    parser.add_argument("--port", type=int, default=backend_port(root_default))
    args = parser.parse_args()

    root = args.root.resolve()
    db_path = args.db.resolve()
    root.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    service_log("服务启动：开始打开 SQLite 并检查 schema")
    startup_db_start = time.perf_counter()
    with db_connect(db_path) as conn:
        setup_schema(conn)
    service_log(f"服务启动：SQLite schema 检查完成，耗时 {(time.perf_counter() - startup_db_start) * 1000:.1f}ms")

    os.chdir(root)
    server = MomoHTTPServer((args.host, args.port), MomoRequestHandler, root, db_path)
    service_log("------------------------------------------------")
    service_log("墨墨 SQLite 本地服务已启动")
    service_log(f"项目目录: {root}")
    service_log(f"数据库: {db_path}")
    service_log(f"访问地址: http://{args.host}:{args.port}/history_dashboard.html")
    service_log("提示: 查看完毕后，请在 Termux 中按 Ctrl + C 停止服务")
    service_log("------------------------------------------------")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        service_log("服务已停止。")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
