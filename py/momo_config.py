#!/usr/bin/env python3
"""Project configuration loader.

The project has a single editable configuration file: app.json.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


REQUIRED_TOP_LEVEL_KEYS = ("server", "paths", "sync", "memoryAlgorithm")
APP_CONFIG_NAME = "app.json"

DEFAULT_APP_CONFIG: dict[str, Any] = {
    "server": {
        "backend": {
            "host": "127.0.0.1",
            "port": 8000,
        },
        "frontend": {
            "host": "0.0.0.0",
            "port": 5173,
        },
    },
    "paths": {
        "database": "data/momo.sqlite",
        "token": "token.txt",
    },
    "sync": {
        "cooldownMinutes": 0,
    },
    "memoryAlgorithm": {
        "algorithm": "fsrs",
        "enabled": True,
        "desiredRetention": 0.9,
        "maximumIntervalDays": 36500,
        "enableFuzzing": False,
        "learningStepsMinutes": [1, 10],
        "relearningStepsMinutes": [10],
        "lowHistoryReviewThreshold": 5,
        "ratingMapping": {
            "忘记": "Again",
            "模糊": "Hard",
            "认识": "Good",
            "FORGET": "Again",
            "VAGUE": "Hard",
            "FAMILIAR": "Good",
        },
        "prediction": {
            "maxWordRows": 2000,
            "tablePreviewRows": 50,
            "defaultDays": 30,
            "historyCoverageFullThreshold": 0.8,
            "historyCoveragePartialThreshold": 0.3,
            "minFsrsHistoryEvents": 2,
            "reviewIndexMode": "observed",
        },
    },
}


def project_root_from_file() -> Path:
    return Path(__file__).resolve().parents[1]


def app_config_path(root_dir: Path | str | None = None) -> Path:
    root = Path(root_dir).resolve() if root_dir else project_root_from_file()
    return root / APP_CONFIG_NAME


def _merge_defaults(value: Any, defaults: Any) -> tuple[Any, bool]:
    if not isinstance(defaults, dict):
        return value, False
    if not isinstance(value, dict):
        return deepcopy(defaults), True

    changed = False
    out = dict(value)
    for key, default_value in defaults.items():
        if key not in out or out[key] in (None, ""):
            out[key] = deepcopy(default_value)
            changed = True
        elif isinstance(out[key], dict) and isinstance(default_value, dict):
            merged, child_changed = _merge_defaults(out[key], default_value)
            out[key] = merged
            changed = changed or child_changed
    return out, changed


def ensure_app_config(root_dir: Path | str | None = None) -> Path:
    """Create app.json on fresh clones and fill newly added default keys."""
    root = Path(root_dir).resolve() if root_dir else project_root_from_file()
    path = app_config_path(root)

    if not path.exists():
        path.write_text(json.dumps(DEFAULT_APP_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"配置文件必须是 JSON 对象: {path}")

    merged, changed = _merge_defaults(data, DEFAULT_APP_CONFIG)
    if changed:
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_app_config(root_dir: Path | str | None = None) -> dict[str, Any]:
    root = Path(root_dir).resolve() if root_dir else project_root_from_file()
    path = app_config_path(root)
    if not path.exists():
        ensure_app_config(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"配置文件必须是 JSON 对象: {path}")
    _, changed = _merge_defaults(data, DEFAULT_APP_CONFIG)
    if changed:
        ensure_app_config(root)
        data = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in data]
    if missing:
        raise ValueError("app.json 缺少配置项: " + ", ".join(missing))
    return data


def config_get(config: dict[str, Any], *keys: str) -> Any:
    cur: Any = config
    for key in keys:
        if not isinstance(cur, dict) or key not in cur or cur[key] in (None, ""):
            raise KeyError("app.json 缺少配置项: " + ".".join(keys))
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
        raise ValueError("app.json 的 memoryAlgorithm 必须是对象")
    return deepcopy(value)
