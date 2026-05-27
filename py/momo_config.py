#!/usr/bin/env python3
"""Project configuration loader.

The project has a single editable configuration file: config/app.json.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


REQUIRED_TOP_LEVEL_KEYS = ("server", "paths", "sync", "memoryAlgorithm")


def project_root_from_file() -> Path:
    return Path(__file__).resolve().parents[1]


def load_app_config(root_dir: Path | str | None = None) -> dict[str, Any]:
    root = Path(root_dir).resolve() if root_dir else project_root_from_file()
    path = root / "config" / "app.json"
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"配置文件必须是 JSON 对象: {path}")
    missing = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in data]
    if missing:
        raise ValueError("config/app.json 缺少配置项: " + ", ".join(missing))
    return data


def config_get(config: dict[str, Any], *keys: str) -> Any:
    cur: Any = config
    for key in keys:
        if not isinstance(cur, dict) or key not in cur or cur[key] in (None, ""):
            raise KeyError("config/app.json 缺少配置项: " + ".".join(keys))
        cur = cur[key]
    return cur


def resolve_project_path(root_dir: Path | str | None, value: str | Path) -> Path:
    root = Path(root_dir).resolve() if root_dir else project_root_from_file()
    raw = Path(str(value))
    if not raw.is_absolute():
        raw = root / raw
    return raw.resolve()


def backend_host(root_dir: Path | str | None = None) -> str:
    return str(config_get(load_app_config(root_dir), "server", "backend", "host"))


def backend_port(root_dir: Path | str | None = None) -> int:
    return int(config_get(load_app_config(root_dir), "server", "backend", "port"))


def frontend_host(root_dir: Path | str | None = None) -> str:
    return str(config_get(load_app_config(root_dir), "server", "frontend", "host"))


def frontend_port(root_dir: Path | str | None = None) -> int:
    return int(config_get(load_app_config(root_dir), "server", "frontend", "port"))


def database_path(root_dir: Path | str | None = None) -> Path:
    cfg = load_app_config(root_dir)
    return resolve_project_path(root_dir, config_get(cfg, "paths", "database"))


def token_path(root_dir: Path | str | None = None) -> Path:
    cfg = load_app_config(root_dir)
    return resolve_project_path(root_dir, config_get(cfg, "paths", "token"))


def sync_cooldown_minutes(root_dir: Path | str | None = None) -> int:
    cfg = load_app_config(root_dir)
    return max(0, int(config_get(cfg, "sync", "cooldownMinutes") or 0))


def memory_algorithm_config(root_dir: Path | str | None = None) -> dict[str, Any]:
    cfg = load_app_config(root_dir)
    value = config_get(cfg, "memoryAlgorithm")
    if not isinstance(value, dict):
        raise ValueError("config/app.json 的 memoryAlgorithm 必须是对象")
    return deepcopy(value)
