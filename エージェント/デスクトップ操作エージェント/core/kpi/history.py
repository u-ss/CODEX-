# -*- coding: utf-8 -*-
"""
History - KPI履歴の蓄積

KPIサマリーを時系列で蓄積し、トレンド検知・アラートの基盤とする。
1行1レコードのJSONL形式で保存。
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Dict, List
import json
import time
import os


@dataclass(frozen=True)
class KPIHistoryRecord:
    """KPI履歴の1レコード"""
    ts: float                  # epoch seconds
    env: str                   # prod/stg/dev
    app: str                   # excel/outlook/freee/...
    build: str                 # git sha or version
    summary: Dict[str, Any]    # KPIAggregator.finalize()の出力


def now_ts() -> float:
    """現在時刻のepoch seconds"""
    return time.time()


def append_history(path: str, rec: KPIHistoryRecord) -> None:
    """履歴ファイルにレコードを追記"""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")


def read_history(path: str, limit: int = 400) -> List[Dict[str, Any]]:
    """
    履歴ファイルを読み込む。
    
    Args:
        path: JSONLファイルパス
        limit: 最大取得件数（末尾から）
    
    Returns:
        履歴レコードのリスト（ts昇順）
    """
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                    if isinstance(rec, dict) and "ts" in rec and "summary" in rec:
                        out.append(rec)
                except Exception:
                    continue
    except FileNotFoundError:
        return []
    
    return out[-limit:] if len(out) > limit else out


def get_metric_series(history: List[Dict[str, Any]], metric_path: str) -> List[float]:
    """
    履歴からメトリクスの時系列を抽出。
    
    Args:
        history: read_history()の出力
        metric_path: ドットパス（例: "actions.pixel_rate"）
    
    Returns:
        メトリクス値のリスト
    """
    xs = []
    for h in history:
        cur = h.get("summary", {})
        for part in metric_path.split("."):
            cur = cur.get(part, {})
        xs.append(float(cur) if isinstance(cur, (int, float)) else 0.0)
    return xs
