#!/usr/bin/env python3
"""FSRS-based memory prediction helpers for the Maimemo dashboard.

The dashboard keeps the Maimemo response -> FSRS rating mapping in config/app.json.
"""

from __future__ import annotations

import json
import math
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from momo_config import memory_algorithm_config

DEFAULT_CONFIG: dict[str, Any] = memory_algorithm_config()


RATING_NAME_BY_RESPONSE = {
    "FORGET": "忘记",
    "VAGUE": "模糊",
    "FAMILIAR": "认识",
    "忘记": "忘记",
    "模糊": "模糊",
    "认识": "认识",
}


def load_memory_algorithm_config(root_dir: Path | None) -> dict[str, Any]:
    return memory_algorithm_config(root_dir)


def _to_utc_datetime(value: str | None, fallback_date: str | None = None) -> datetime | None:
    text = str(value or "").strip()
    if not text and fallback_date:
        text = str(fallback_date).strip()
    if not text:
        return None
    try:
        if len(text) == 10 and text[4:5] == "-" and text[7:8] == "-":
            return datetime.strptime(text, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _date_only(value: str | None) -> str:
    dt = _to_utc_datetime(value)
    if dt:
        return dt.date().isoformat()
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return ""


def _days_between(a: str | None, b: str | None) -> int | None:
    da = _date_only(a)
    db = _date_only(b)
    if not da or not db:
        return None
    try:
        return (datetime.strptime(da, "%Y-%m-%d").date() - datetime.strptime(db, "%Y-%m-%d").date()).days
    except Exception:
        return None


def _clamp_probability(value: float | None, fallback: float = 0.0) -> float:
    try:
        x = float(value)  # type: ignore[arg-type]
    except Exception:
        x = fallback
    if not math.isfinite(x):
        x = fallback
    return max(0.0, min(1.0, x))


def _timedelta_to_days(delta: timedelta) -> float:
    return max(0.0, delta.total_seconds() / 86400.0)


def _interval_days_from_card(card: Any, review_dt: datetime) -> float:
    due = getattr(card, "due", None)
    if not isinstance(due, datetime):
        return 0.0
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return _timedelta_to_days(due.astimezone(timezone.utc) - review_dt)


def _rating_enum(fsrs_rating: Any, raw: str) -> Any:
    name = str(raw or "").strip()
    rating_names = {
        "again": "Again", "忘记": "Again", "forget": "Again", "forgot": "Again",
        "hard": "Hard", "困难": "Hard", "模糊": "Hard", "vague": "Hard",
        "good": "Good", "正常": "Good", "认识": "Good", "familiar": "Good",
        "easy": "Easy", "简单": "Easy", "熟悉": "Easy",
    }
    canonical = rating_names.get(name.lower(), rating_names.get(name, name))
    if not hasattr(fsrs_rating, canonical):
        canonical = "Good"
    return getattr(fsrs_rating, canonical)


def _response_to_rating_name(response: str | None, config: dict[str, Any]) -> str:
    mapping = config.get("ratingMapping") if isinstance(config.get("ratingMapping"), dict) else {}
    raw = str(response or "").strip()
    cn = RATING_NAME_BY_RESPONSE.get(raw, raw)
    return str(mapping.get(raw) or mapping.get(cn) or "Good")


def _canonical_response(response: str | None) -> str:
    raw = str(response or "").strip()
    return RATING_NAME_BY_RESPONSE.get(raw, raw)


@dataclass
class WordEvent:
    word_key: str
    word: str
    study_count: int
    review_datetime: datetime
    response: str
    interval_days: int | None
    last_study_date: str | None = None
    next_study_date: str | None = None
    review_span_days: int | None = None


def _record_key(record: dict[str, Any]) -> str:
    return str(record.get("record_key") or record.get("voc_id") or record.get("spelling") or "").strip().lower()


def _record_word(record: dict[str, Any]) -> str:
    return str(record.get("spelling") or record.get("word") or "").strip()


def _record_response(record: dict[str, Any]) -> str:
    return str(record.get("last_response") or record.get("last_response_cn") or record.get("lastResponse") or "").strip()


def _record_study_count(record: dict[str, Any]) -> int:
    try:
        return int(record.get("study_count") if record.get("study_count") is not None else record.get("studyCount") or 0)
    except Exception:
        return 0


def _build_word_histories(conn: sqlite3.Connection, max_words: int) -> tuple[dict[str, list[WordEvent]], dict[str, dict[str, Any]], str]:
    """Recover approximate per-word review histories from daily latest overview snapshots.

    A daily overview snapshot stores each word's latest study_count, last_study_date,
    next_study_date and last_response. When the tuple (study_count, last_study_date,
    last_response) first appears, we treat it as one historical review event.
    """
    import momo_db  # local import avoids a module import cycle

    histories: dict[str, list[WordEvent]] = {}
    current_records: dict[str, dict[str, Any]] = {}
    seen_event_keys: set[tuple[str, int, str, str]] = set()
    days = momo_db.list_days(conn)
    latest_day = days[-1] if days else ""

    for day in days:
        snap = momo_db.latest_snapshot_time_for_day(conn, day)
        if not snap:
            continue
        records = momo_db.reconstruct_overview_records_at(conn, day, snap)
        for rec in records:
            key = _record_key(rec)
            if not key:
                continue
            current_records[key] = rec
            response = _record_response(rec)
            if not response or response in {"STUDY_RESPONSE_UNSPECIFIED", "未作答"}:
                continue
            study_count = _record_study_count(rec)
            last_study = rec.get("last_study_date") or rec.get("lastStudyDate") or ""
            review_dt = _to_utc_datetime(str(last_study or ""), fallback_date=day)
            if not review_dt:
                continue
            event_key = (key, study_count, review_dt.date().isoformat(), response)
            if event_key in seen_event_keys:
                continue
            seen_event_keys.add(event_key)
            prev_date = rec.get("first_study_date") or rec.get("firstStudyDate") or ""
            next_study = rec.get("next_study_date") or rec.get("nextStudyDate") or ""
            interval = _days_between(str(last_study or ""), str(prev_date or ""))
            review_span = _days_between(str(next_study or ""), str(last_study or ""))
            histories.setdefault(key, []).append(WordEvent(
                word_key=key,
                word=_record_word(rec),
                study_count=study_count,
                review_datetime=review_dt,
                response=response,
                interval_days=interval,
                last_study_date=str(last_study or ""),
                next_study_date=str(next_study or ""),
                review_span_days=review_span,
            ))
            if len(histories) >= max_words and key not in histories:
                break

    for key, events in histories.items():
        events.sort(key=lambda e: (e.review_datetime, e.study_count))
        prev_dt: datetime | None = None
        for e in events:
            if prev_dt is not None:
                e.interval_days = max(0, (e.review_datetime.date() - prev_dt.date()).days)
            elif e.interval_days is None:
                e.interval_days = 0
            prev_dt = e.review_datetime
    return histories, current_records, latest_day



def build_review_history_state_index(
    conn: sqlite3.Connection,
    voc_ids: set[str] | None = None,
    max_words: int = 100000,
) -> dict[str, list[dict[str, Any]]]:
    """Return FSRS input history states indexed by voc_id.

    This exposes the same historical input chain used by the FSRS prediction
    module. Each event is one deduplicated review state recovered from daily
    overview snapshots. It is meant for consumers that need the previous
    review state for a current word, such as time-point comparison.
    """
    histories, current_records, _latest_day = _build_word_histories(conn, max_words)
    wanted = {str(v).strip() for v in (voc_ids or set()) if str(v).strip()}
    out: dict[str, list[dict[str, Any]]] = {}

    for key, events in histories.items():
        rec = current_records.get(key) or {}
        voc_id = str(rec.get("voc_id") or rec.get("vocId") or "").strip()
        if not voc_id:
            continue
        if wanted and voc_id not in wanted:
            continue

        rows: list[dict[str, Any]] = []
        for event in events:
            rows.append({
                "vocId": voc_id,
                "word": event.word,
                "studyCount": event.study_count,
                "lastStudyDateRaw": event.last_study_date or "",
                "nextStudyDateRaw": event.next_study_date or "",
                "lastStudyDate": _date_only(event.last_study_date),
                "nextStudyDate": _date_only(event.next_study_date),
                "memoryDurabilityDays": event.review_span_days,
                "reviewSpanDays": event.review_span_days,
                "response": _canonical_response(event.response),
                "reviewDate": event.review_datetime.date().isoformat(),
            })
        out[voc_id] = rows

    return out

def _new_group_bucket() -> dict[str, Any]:
    return {
        "total": 0,
        "forget": 0,
        "vague": 0,
        "familiar": 0,
        "nextIntervalSum": {"forget": 0.0, "vague": 0.0, "familiar": 0.0},
        "nextIntervalCount": {"forget": 0, "vague": 0, "familiar": 0},
    }


def _add_response_to_bucket(bucket: dict[str, Any], resp: str) -> None:
    bucket["total"] += 1
    if resp == "忘记":
        bucket["forget"] += 1
    elif resp == "模糊":
        bucket["vague"] += 1
    elif resp == "认识":
        bucket["familiar"] += 1


def _add_next_interval_to_bucket(bucket: dict[str, Any], resp: str, interval_days: int | None) -> None:
    key = {"忘记": "forget", "模糊": "vague", "认识": "familiar"}.get(resp)
    if not key or interval_days is None:
        return
    try:
        value = float(interval_days)
    except Exception:
        return
    if not math.isfinite(value) or value < 0:
        return
    bucket["nextIntervalSum"][key] += value
    bucket["nextIntervalCount"][key] += 1


def _group_rates(histories: dict[str, list[WordEvent]], config: dict[str, Any]) -> dict[str, Any]:
    threshold = max(1, int(config.get("lowHistoryReviewThreshold") or 5))
    by_index: dict[int, dict[str, Any]] = {i: _new_group_bucket() for i in range(1, threshold + 1)}
    total = _new_group_bucket()
    for events in histories.values():
        for idx, event in enumerate(events, start=1):
            resp = _canonical_response(event.response)
            if idx <= threshold:
                _add_response_to_bucket(by_index[idx], resp)
            _add_response_to_bucket(total, resp)

            # 调度样本：(第 idx 次复习的结果) -> (第 idx+1 次复习的实际间隔)。
            if idx < len(events):
                next_interval = events[idx].interval_days
                if idx <= threshold:
                    _add_next_interval_to_bucket(by_index[idx], resp, next_interval)
                _add_next_interval_to_bucket(total, resp, next_interval)

    def rates(bucket: dict[str, Any]) -> dict[str, Any]:
        n = max(1, int(bucket["total"]))
        out: dict[str, Any] = {
            "forget": bucket["forget"] / n,
            "vague": bucket["vague"] / n,
            "familiar": bucket["familiar"] / n,
            "total": bucket["total"],
            "avgNextIntervalByResponse": {},
        }
        for key in ("forget", "vague", "familiar"):
            c = int(bucket["nextIntervalCount"].get(key) or 0)
            out["avgNextIntervalByResponse"][key] = (float(bucket["nextIntervalSum"].get(key) or 0.0) / c) if c else None
        return out

    overall = rates(total)
    by_idx = {str(k): rates(v) for k, v in by_index.items()}
    # 给每个阶段补上整体均值，避免低历史/断档时出现 0 天间隔。
    for bucket in by_idx.values():
        for key in ("forget", "vague", "familiar"):
            if bucket["avgNextIntervalByResponse"].get(key) is None:
                bucket["avgNextIntervalByResponse"][key] = overall["avgNextIntervalByResponse"].get(key)
    return {
        "threshold": threshold,
        "byIndex": by_idx,
        "overall": overall,
    }



def _safe_retrievability(scheduler: Any, card: Any, when: datetime, fallback: float) -> float:
    try:
        return _clamp_probability(scheduler.get_card_retrievability(card, current_datetime=when), fallback)
    except Exception:
        return _clamp_probability(fallback, 0.5)


def _safe_review_card(scheduler: Any, card: Any, rating: Any, review_dt: datetime) -> Any:
    try:
        next_card, _log = scheduler.review_card(card, rating, review_datetime=review_dt)
        return next_card
    except Exception:
        return card


def _history_quality(events: list[WordEvent], study_count: int, prediction_cfg: dict[str, Any]) -> dict[str, Any]:
    """Estimate how much we should trust per-word FSRS replay.

    历史记录经常不完整，所以保留两套口径：
    1. original：按墨墨数据库里的 study_count 当作总学习/复习次数；
    2. observed：把该词在本地数据库里第一次可见记录当作第 1 次。

    预测默认使用 observed 口径；original 口径仍输出到前端，方便对比。
    """
    history_count = len(events)
    original_expected_count = max(0, int(study_count or 0))
    original_denominator = max(1, original_expected_count)
    original_coverage = (
        min(1.0, history_count / original_denominator)
        if original_expected_count
        else (1.0 if history_count else 0.0)
    )
    observed_expected_count = history_count
    observed_coverage = 1.0 if history_count else 0.0

    full_threshold = float(prediction_cfg.get("historyCoverageFullThreshold") or 0.8)
    partial_threshold = float(prediction_cfg.get("historyCoveragePartialThreshold") or 0.3)
    min_fsrs_events = max(1, int(prediction_cfg.get("minFsrsHistoryEvents") or 2))
    mode_raw = str(prediction_cfg.get("reviewIndexMode") or "observed").strip().lower()
    review_index_mode = "original" if mode_raw in {"original", "study_count", "study-count", "studycount"} else "observed"
    selected_coverage = original_coverage if review_index_mode == "original" else observed_coverage

    history_depth_weight = min(1.0, max(0.0, history_count / min_fsrs_events))
    fsrs_weight_original = min(1.0, max(0.0, original_coverage)) * history_depth_weight
    fsrs_weight_observed = min(1.0, max(0.0, observed_coverage)) * history_depth_weight
    fsrs_weight = fsrs_weight_original if review_index_mode == "original" else fsrs_weight_observed

    if history_count <= 0:
        status = "无历史，使用群体统计估计"
    elif review_index_mode == "observed":
        status = "按可观测历史计次，FSRS + 群体统计校准"
        if history_count >= min_fsrs_events:
            status = "按可观测历史计次，主要使用 FSRS"
    elif selected_coverage >= full_threshold:
        status = "原始学习次数口径历史较完整，主要使用 FSRS"
    elif selected_coverage >= partial_threshold:
        status = "原始学习次数口径历史不完整，FSRS + 群体统计校准"
    else:
        status = "原始学习次数口径历史严重缺失，主要使用群体统计"

    return {
        "historyCount": history_count,
        "expectedStudyCount": original_expected_count,
        "originalStudyCount": original_expected_count,
        "observedStudyCount": observed_expected_count,
        "historyCoverage": selected_coverage,
        "historyCoverageObserved": observed_coverage,
        "historyCoverageOriginal": original_coverage,
        "historyStatus": status,
        "fsrsWeight": fsrs_weight,
        "fsrsWeightObserved": fsrs_weight_observed,
        "fsrsWeightOriginal": fsrs_weight_original,
        "reviewIndexMode": review_index_mode,
    }


def _estimated_interval(group: dict[str, Any], overall: dict[str, Any], key: str, default_days: float) -> float:
    for bucket in (group, overall):
        try:
            value = bucket.get("avgNextIntervalByResponse", {}).get(key)
            if value is not None and math.isfinite(float(value)) and float(value) >= 0:
                return float(value)
        except Exception:
            pass
    return float(default_days)


def _blend_interval(fsrs_days: float, estimated_days: float, fsrs_weight: float) -> float:
    try:
        f = float(fsrs_days)
    except Exception:
        f = 0.0
    try:
        b = float(estimated_days)
    except Exception:
        b = 1.0
    if not math.isfinite(f) or f <= 0:
        return max(0.0, b)
    w = _clamp_probability(fsrs_weight, 0.0)
    return max(0.0, w * f + (1.0 - w) * b)

def _review_interval_for_rating(scheduler: Any, card: Any, rating: Any, review_dt: datetime) -> float:
    try:
        next_card, _log = scheduler.review_card(card, rating, review_datetime=review_dt)
        return _interval_days_from_card(next_card, review_dt)
    except Exception:
        return 0.0


def build_fsrs_dashboard_payload(conn: sqlite3.Connection, root_dir: Path | None = None) -> dict[str, Any]:
    config = load_memory_algorithm_config(root_dir)
    if not config.get("enabled", True):
        return {"available": False, "enabled": False, "message": "config/app.json 已关闭 FSRS 预测。", "config": config}

    try:
        from fsrs import Card, Rating, Scheduler  # type: ignore
    except Exception as exc:
        return {
            "available": False,
            "enabled": True,
            "message": f"未安装 fsrs 包：{exc}。请先运行 pip install fsrs。",
            "config": config,
        }

    try:
        prediction_cfg = config.get("prediction") if isinstance(config.get("prediction"), dict) else {}
        max_rows = max(1, int(prediction_cfg.get("maxWordRows") or 2000))
        histories, current_records, latest_day = _build_word_histories(conn, max_rows)
        groups = _group_rates(histories, config)
        scheduler = Scheduler(
            desired_retention=float(config.get("desiredRetention") or 0.9),
            learning_steps=[timedelta(minutes=float(x)) for x in (config.get("learningStepsMinutes") or [1, 10])],
            relearning_steps=[timedelta(minutes=float(x)) for x in (config.get("relearningStepsMinutes") or [10])],
            maximum_interval=int(config.get("maximumIntervalDays") or 36500),
            enable_fuzzing=bool(config.get("enableFuzzing", False)),
        )
        today_dt = _to_utc_datetime(latest_day) or datetime.now(timezone.utc)
        items: list[dict[str, Any]] = []
        prediction_cfg = config.get("prediction") if isinstance(config.get("prediction"), dict) else {}
        estimated_default_days = max(1.0, float(prediction_cfg.get("defaultDays") or 30))
        for key, rec in current_records.items():
            if len(items) >= max_rows:
                break
            events = histories.get(key) or []
            study_count = _record_study_count(rec)
            quality = _history_quality(events, study_count, prediction_cfg)
            card = Card()
            for event in events:
                rating_name = _response_to_rating_name(event.response, config)
                rating = _rating_enum(Rating, rating_name)
                card = _safe_review_card(scheduler, card, rating, event.review_datetime)

            next_date_raw = rec.get("next_study_date") or rec.get("nextStudyDate") or ""
            last_date_raw = rec.get("last_study_date") or rec.get("lastStudyDate") or ""
            planned_dt = _to_utc_datetime(str(next_date_raw or "")) or today_dt
            if planned_dt < today_dt:
                planned_dt = today_dt
            current_interval = _days_between(str(next_date_raw or ""), str(last_date_raw or ""))
            if current_interval is None:
                current_interval = max(0, (planned_dt.date() - today_dt.date()).days)
            next_due_offset = max(0, (planned_dt.date() - today_dt.date()).days)

            review_index_observed = max(1, len(events) + 1)
            review_index_original = max(1, study_count + 1) if study_count else review_index_observed
            review_index_mode = str(quality.get("reviewIndexMode") or "observed")
            review_index = review_index_original if review_index_mode == "original" else review_index_observed
            threshold = int(groups["threshold"])
            group = groups["byIndex"].get(str(min(review_index, threshold)), groups["overall"])
            group_forget = _clamp_probability(float(group.get("forget") or 0.0), 0.0)
            retrievability = _safe_retrievability(scheduler, card, planned_dt, 1.0 - group_forget)
            fsrs_forget = 1.0 - retrievability

            # 历史越不完整，越少相信逐词 FSRS 回放，越多使用同阶段群体遗忘率估计。
            stage_weight = min(max(review_index / threshold, 0.0), 1.0) if review_index < threshold else 1.0
            fsrs_weight = _clamp_probability(float(quality.get("fsrsWeight") or 0.0) * stage_weight, 0.0)
            forget_prob = fsrs_weight * fsrs_forget + (1.0 - fsrs_weight) * group_forget
            forget_prob = _clamp_probability(forget_prob, group_forget)

            non_forget = max(1e-9, 1.0 - group_forget)
            vague_share = _clamp_probability(float(group.get("vague") or 0.0) / non_forget, 0.0)
            vague_prob = (1.0 - forget_prob) * vague_share
            familiar_prob = max(0.0, 1.0 - forget_prob - vague_prob)

            rating_forget = _rating_enum(Rating, _response_to_rating_name("忘记", config))
            rating_vague = _rating_enum(Rating, _response_to_rating_name("模糊", config))
            rating_familiar = _rating_enum(Rating, _response_to_rating_name("认识", config))
            raw_interval_forget = _review_interval_for_rating(scheduler, card, rating_forget, planned_dt)
            raw_interval_vague = _review_interval_for_rating(scheduler, card, rating_vague, planned_dt)
            raw_interval_familiar = _review_interval_for_rating(scheduler, card, rating_familiar, planned_dt)
            interval_forget = _blend_interval(
                raw_interval_forget,
                _estimated_interval(group, groups["overall"], "forget", max(1.0, min(estimated_default_days, 3.0))),
                fsrs_weight,
            )
            interval_vague = _blend_interval(
                raw_interval_vague,
                _estimated_interval(group, groups["overall"], "vague", max(2.0, min(estimated_default_days, 7.0))),
                fsrs_weight,
            )
            interval_familiar = _blend_interval(
                raw_interval_familiar,
                _estimated_interval(group, groups["overall"], "familiar", estimated_default_days),
                fsrs_weight,
            )
            expected_interval = forget_prob * interval_forget + vague_prob * interval_vague + familiar_prob * interval_familiar
            items.append({
                "word": _record_word(rec),
                "recordKey": key,
                "studyCount": study_count,
                "originalStudyCount": quality.get("originalStudyCount"),
                "observedStudyCount": quality.get("observedStudyCount"),
                "historyCount": len(events),
                "expectedStudyCount": quality.get("expectedStudyCount"),
                "reviewIndexMode": quality.get("reviewIndexMode"),
                "reviewIndexUsed": review_index,
                "reviewIndexObserved": review_index_observed,
                "reviewIndexOriginal": review_index_original,
                "historyCoverage": quality.get("historyCoverage"),
                "historyCoverageObserved": quality.get("historyCoverageObserved"),
                "historyCoverageOriginal": quality.get("historyCoverageOriginal"),
                "historyStatus": quality.get("historyStatus"),
                "fsrsWeight": fsrs_weight,
                "fsrsWeightObserved": quality.get("fsrsWeightObserved"),
                "fsrsWeightOriginal": quality.get("fsrsWeightOriginal"),
                "difficulty": getattr(card, "difficulty", None),
                "stability": getattr(card, "stability", None),
                "retrievability": retrievability,
                "forgetProbability": forget_prob,
                "vagueProbability": vague_prob,
                "familiarProbability": familiar_prob,
                "currentIntervalDays": current_interval,
                "nextReviewDate": _date_only(str(next_date_raw or "")) or planned_dt.date().isoformat(),
                "nextDueOffsetDays": next_due_offset,
                "intervalIfForgetDays": interval_forget,
                "intervalIfVagueDays": interval_vague,
                "intervalIfFamiliarDays": interval_familiar,
                "expectedNextIntervalDays": expected_interval,
                "expectedNextReviewDate": (planned_dt + timedelta(days=expected_interval)).date().isoformat(),
                "expectedNextOffsetDays": next_due_offset + expected_interval,
                "lastResponse": _canonical_response(_record_response(rec)),
                "lastStudyDate": _date_only(str(last_date_raw or "")),
            })

        items.sort(key=lambda x: (x.get("nextDueOffsetDays", 10**9), -(x.get("forgetProbability") or 0), x.get("word") or ""))
        return {
            "available": True,
            "enabled": True,
            "message": "FSRS 预测已启用。",
            "reviewIndexMode": str((config.get("prediction") or {}).get("reviewIndexMode") or "observed"),
            "referenceDate": latest_day,
            "config": config,
            "groupRates": groups,
            "items": items,
            "itemCount": len(items),
            "currentWordCount": len(current_records),
            "historyWordCount": len(histories),
        }
    except Exception as exc:
        return {
            "available": False,
            "enabled": True,
            "message": f"FSRS 预测计算失败：{exc}",
            "config": config,
        }
