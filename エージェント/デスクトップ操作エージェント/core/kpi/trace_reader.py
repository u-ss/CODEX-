# -*- coding: utf-8 -*-
"""
Trace Reader - JSONL形式のtraceファイルを読み込み、正規化する

フィールド名のゆれ（ts/timestamp/time, ok/success等）を吸収し、
統一されたTraceEventオブジェクトを生成する。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, Optional
import json
import datetime as dt


def _parse_ts(value: Any) -> float:
    """
    traceのtsがISO文字列/epoch秒/epochミリ秒どれでも受け付ける。
    戻り値はepoch seconds(float)に統一。
    """
    if value is None:
        raise ValueError("ts is missing")
    if isinstance(value, (int, float)):
        # ミリ秒っぽい場合（1e12超）
        if value > 1e12:
            return float(value) / 1000.0
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        # epoch文字列
        if s.isdigit():
            v = int(s)
            return v / 1000.0 if v > 1e12 else float(v)
        # ISO 8601
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s).timestamp()
    raise TypeError(f"unsupported ts type: {type(value)}")


def _get_any(d: Dict[str, Any], keys: Iterable[str], default=None):
    """複数のキー候補から最初に見つかった値を返す"""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


@dataclass(frozen=True)
class TraceEvent:
    """正規化されたトレースイベント"""
    ts: float
    event: str
    task_id: str
    step_id: Optional[str]
    action_id: Optional[str]
    action_kind: Optional[str]
    layer: Optional[str]
    ok: Optional[bool]
    duration_ms: Optional[float]
    fail_type: Optional[str]
    retry_index: Optional[int]
    cb_fired: bool
    hitl: bool
    screen_key: Optional[str]
    locator_version: Optional[str]
    locator_id: Optional[str]
    intent: Optional[str]
    app_id: Optional[str]
    raw: Dict[str, Any]


def normalize_event(rec: Dict[str, Any]) -> TraceEvent:
    """
    生のJSONレコードを正規化されたTraceEventに変換。
    フィールド名のゆれを吸収。
    """
    # イベント名のゆれ: event/type/name
    event = _get_any(rec, ["event", "type", "name"], "unknown")
    
    ts = _parse_ts(_get_any(rec, ["ts", "timestamp", "time"], None))
    
    # ID系ゆれ
    task_id = str(_get_any(rec, ["task_id", "taskId", "tid"], "unknown_task"))
    step_id = _get_any(rec, ["step_id", "stepId", "sid"], None)
    action_id = _get_any(rec, ["action_id", "actionId", "aid"], None)
    
    action_kind = _get_any(rec, ["action_kind", "actionKind", "kind"], None)
    layer = _get_any(rec, ["layer", "executor", "route_layer"], None)
    
    # ok/success のゆれ
    ok_val = _get_any(rec, ["ok", "success", "is_success"], None)
    ok = None if ok_val is None else bool(ok_val)
    
    duration_ms = _get_any(rec, ["duration_ms", "durationMs", "elapsed_ms"], None)
    duration_ms = None if duration_ms is None else float(duration_ms)
    
    fail_type = _get_any(rec, ["fail_type", "failType", "failure_type"], None)
    if fail_type is not None:
        fail_type = str(fail_type)
    
    retry_index = _get_any(rec, ["retry_index", "retryIndex", "attempt"], None)
    if retry_index is not None:
        retry_index = int(retry_index)
    
    cb_fired = bool(_get_any(rec, ["cb_fired", "circuit_fired", "cbOpen"], False))
    hitl = bool(_get_any(rec, ["hitl", "human_in_the_loop", "needs_confirm"], False))
    
    screen_key = _get_any(rec, ["screen_key", "screenKey"], None)
    locator_version = _get_any(rec, ["locator_version", "locatorVersion"], None)
    locator_id = _get_any(rec, ["locator_id", "locatorId"], None)
    intent = _get_any(rec, ["intent"], None)
    app_id = _get_any(rec, ["app_id", "appId", "app"], None)
    
    return TraceEvent(
        ts=ts, event=str(event),
        task_id=task_id, step_id=step_id, action_id=action_id,
        action_kind=action_kind, layer=layer,
        ok=ok, duration_ms=duration_ms,
        fail_type=fail_type, retry_index=retry_index,
        cb_fired=cb_fired, hitl=hitl,
        screen_key=screen_key, locator_version=locator_version,
        locator_id=locator_id, intent=intent, app_id=app_id,
        raw=rec,
    )


def read_jsonl(path: str) -> Iterator[TraceEvent]:
    """
    JSONLファイルを読み込み、正規化されたTraceEventを順次yield。
    壊れた行はスキップ。
    """
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
                if not isinstance(rec, dict):
                    continue
                yield normalize_event(rec)
            except Exception:
                # 壊れた行はスキップ（必要なら別ログへ）
                continue
