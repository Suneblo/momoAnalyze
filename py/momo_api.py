#!/usr/bin/env python3
"""Small helpers for MaiMemo Open API calls used by the local dashboard service."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = "https://open.maimemo.com/open/api/v1"


def read_token(token_file: Path) -> str:
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"token 为空: {token_file}")
    return token


def _request(token: str, method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    url = f"{BASE}{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text else {}
        except Exception as exc:
            last_error = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"请求失败: {method} {path}: {last_error}")


def get_json(token: str, path: str) -> dict[str, Any]:
    return _request(token, "GET", path)


def post_json(token: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _request(token, "POST", path, payload or {})


def delete_json(token: str, path: str) -> dict[str, Any]:
    return _request(token, "DELETE", path)


def data_payload(resp: dict[str, Any]) -> dict[str, Any]:
    data = resp.get("data")
    return data if isinstance(data, dict) else resp


def ensure_success(resp: dict[str, Any], action: str) -> None:
    # Some public examples omit the success wrapper, while study endpoints in this
    # project return {success,data}. Treat explicit success=false as failure.
    if resp.get("success") is False:
        raise RuntimeError(f"{action} 失败: " + json.dumps(resp, ensure_ascii=False, indent=2))


def list_notepads(token: str, limit: int = 10, offset: int = 0) -> dict[str, Any]:
    # Momo notepads endpoint rejects limit > 10. Keep the clamp here so callers
    # cannot accidentally generate a 400 by passing a larger page size.
    q = urllib.parse.urlencode({"limit": max(1, min(int(limit or 10), 10)), "offset": max(0, int(offset or 0))})
    resp = get_json(token, f"/notepads?{q}")
    ensure_success(resp, "读取云词本列表")
    data = data_payload(resp)
    return {"notepads": data.get("notepads", []) or []}


def get_notepad(token: str, notepad_id: str) -> dict[str, Any]:
    resp = get_json(token, f"/notepads/{urllib.parse.quote(str(notepad_id))}")
    ensure_success(resp, "读取云词本")
    data = data_payload(resp)
    return data.get("notepad") or data


def create_notepad(token: str, *, title: str, brief: str = "", content: str = "", tags: list[str] | None = None, status: str = "PUBLISHED") -> dict[str, Any]:
    payload = {"notepad": {"title": title, "brief": brief, "content": content, "tags": tags or [], "status": status or "PUBLISHED"}}
    resp = post_json(token, "/notepads", payload)
    ensure_success(resp, "创建云词本")
    data = data_payload(resp)
    return data.get("notepad") or data


def update_notepad(token: str, notepad_id: str, *, title: str, brief: str, content: str, tags: list[str] | None = None, status: str = "PUBLISHED") -> dict[str, Any]:
    payload = {"notepad": {"title": title, "brief": brief, "content": content, "tags": tags or [], "status": status or "PUBLISHED"}}
    resp = post_json(token, f"/notepads/{urllib.parse.quote(str(notepad_id))}", payload)
    ensure_success(resp, "更新云词本")
    data = data_payload(resp)
    return data.get("notepad") or data


def delete_notepad(token: str, notepad_id: str) -> dict[str, Any]:
    resp = delete_json(token, f"/notepads/{urllib.parse.quote(str(notepad_id))}")
    ensure_success(resp, "删除云词本")
    data = data_payload(resp)
    return data.get("notepad") or data


def query_vocabulary(token: str, spellings: list[str] | None = None, ids: list[str] | None = None) -> list[dict[str, Any]]:
    if spellings:
        payload = {"spellings": list(dict.fromkeys([str(x).strip() for x in spellings if str(x).strip()]))[:1000]}
    else:
        payload = {"ids": list(dict.fromkeys([str(x).strip() for x in (ids or []) if str(x).strip()]))[:1000]}
    if not payload.get("spellings") and not payload.get("ids"):
        return []
    resp = post_json(token, "/vocabulary/query", payload)
    ensure_success(resp, "查询单词 ID")
    data = data_payload(resp)
    voc = data.get("voc") or data.get("vocs") or []
    return voc if isinstance(voc, list) else []


def advance_study(token: str, voc_ids: list[str]) -> dict[str, Any]:
    ids = list(dict.fromkeys([str(x).strip() for x in voc_ids if str(x).strip()]))[:1000]
    if not ids:
        return {"advanced_count": 0}
    resp = post_json(token, "/study/advance_study", {"voc_ids": ids})
    ensure_success(resp, "提前复习")
    data = data_payload(resp)
    return data


def add_words_to_study(token: str, voc_ids: list[str], advance: bool = False) -> dict[str, Any]:
    ids = list(dict.fromkeys([str(x).strip() for x in voc_ids if str(x).strip()]))[:1000]
    if not ids:
        return {"added_count": 0}
    resp = post_json(token, "/study/add_words", {"words": [{"id": x} for x in ids], "advance": bool(advance)})
    ensure_success(resp, "添加到学习计划")
    data = data_payload(resp)
    return data
