# -*- coding: utf-8 -*-
"""
TOOL_CALL / TOOL_RESULT イベント構造 — 全レイヤで使うログイベント
CODEXAPP設計: run_id/trace_id/span_id/parent_span_id/event_seq 必須
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _gen_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class ToolCallEvent:
    """TOOL_CALLイベント（検索/取得/抽出/検証の全レイヤで使用）"""
    event_type: str = "TOOL_CALL"
    tool_name: str = ""  # "search_web" | "fetch_url" | "extract_text" | "verify_run"
    run_id: str = ""
    trace_id: str = ""
    span_id: str = field(default_factory=_gen_id)
    parent_span_id: str = ""
    event_seq: int = 0
    timestamp: float = field(default_factory=time.time)
    # TOOL_CALL固有
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultEvent:
    """TOOL_RESULTイベント"""
    event_type: str = "TOOL_RESULT"
    tool_name: str = ""
    run_id: str = ""
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    event_seq: int = 0
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    # Fetch固有
    url: str = ""
    final_url: str = ""
    host: str = ""
    engine: str = ""  # "http" | "browser"
    http_status: Optional[int] = None
    error_class: str = ""  # "timeout" | "connection_reset" | "dns_error" | ""
    blocked_signal: str = ""  # "captcha" | "forbidden" | "rate_limited" | ""
    attempt_no: int = 0
    retry_decision: str = ""  # "retry" | "no_retry" | "breaker_open" | "rate_limited"
    decision_reason: str = ""
    # コンテンツ（プレビュー+SHA256のみ、本文はartifact）
    content_preview: str = ""
    content_sha256: str = ""
    content_artifact_id: str = ""
    content_length: int = 0
    # 抽出品質メトリクス（P0-C）
    extraction_ratio: float = 0.0  # テキスト密度（テキスト文字数/生HTML文字数）
    boilerplate_ratio: float = 0.0  # ボイラープレート率
    was_truncated: bool = False  # 上限で切り詰められたか
    quality_grade: str = ""  # "high" | "medium" | "low" | "empty"
    # RateLimiter証跡（P0-B）
    rate_limit_wait_sec: float = 0.0  # ホスト待機秒数
    retry_after_respected: bool = False  # Retry-Afterヘッダー尊重
    # Search固有
    query: str = ""
    results_count: int = 0
    provider: str = ""
    # URL選定（P0-D）
    selection_reason: str = ""  # "selected" | "duplicate" | "budget_exceeded"
    # リンク追跡（P2-A）
    source_url: str = ""        # 追跡元URL
    relevance_score: float = 0.0  # 関連度スコア
    anchor_text: str = ""       # アンカーテキスト
    # 汎用
    status: str = ""  # "success" | "failed" | "skipped"
    skip_reason: str = ""  # "breaker_open" | "url_exhausted" | "budget_exceeded"


@dataclass
class RunSummary:
    """L1 Summary — RUN_SUMMARY（claimed/verified分離）"""
    run_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    total_queries: int = 0
    total_urls_found: int = 0
    total_urls_before_dedup: int = 0  # 重複排除前のURL総数（処理前母数）
    total_fetches: int = 0
    successful_fetches: int = 0
    failed_fetches: int = 0
    skipped_fetches: int = 0
    breaker_opens: int = 0
    # RateLimiter統計（P0-B）
    total_rate_limit_waits: int = 0
    total_rate_limit_wait_sec: float = 0.0
    # 抽出品質統計（P0-C）
    avg_extraction_ratio: float = 0.0
    truncated_count: int = 0
    quality_counts: Dict[str, int] = field(default_factory=dict)  # {"high": N, "medium": N, ...}
    claimed_success: bool = False
    verified_success: bool = False  # VERIFICATION_RUNでのみtrue
    verification_checks: Dict[str, bool] = field(default_factory=dict)


class EventLogger:
    """L3 Trace（JSONL）のロガー"""

    def __init__(self, output_dir: str, run_id: Optional[str] = None):
        self.run_id = run_id or _gen_id()
        self.trace_id = _gen_id()
        self._seq = 0
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._trace_path = self._output_dir / f"{self.run_id}_trace.jsonl"
        self._events: List[Dict] = []

    def log_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        parent_span_id: str = "",
    ) -> ToolCallEvent:
        """TOOL_CALLイベントを記録"""
        self._seq += 1
        event = ToolCallEvent(
            tool_name=tool_name,
            run_id=self.run_id,
            trace_id=self.trace_id,
            parent_span_id=parent_span_id,
            event_seq=self._seq,
            args=args,
        )
        self._write(event)
        return event

    def log_tool_result(self, **kwargs) -> ToolResultEvent:
        """TOOL_RESULTイベントを記録"""
        self._seq += 1
        event = ToolResultEvent(
            run_id=self.run_id,
            trace_id=self.trace_id,
            event_seq=self._seq,
            **kwargs,
        )
        self._write(event)
        return event

    def _write(self, event) -> None:
        """イベントをJSONLファイルとメモリに追記"""
        d = asdict(event)
        self._events.append(d)
        with open(self._trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")

    def get_all_events(self) -> List[Dict]:
        return self._events

    @staticmethod
    def content_sha256(content: str) -> str:
        """コンテンツのSHA256ハッシュ"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def save_summary(self, summary: RunSummary) -> Path:
        """L1 Summaryを保存"""
        path = self._output_dir / f"{self.run_id}_summary.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(summary), f, ensure_ascii=False, indent=2, default=str)
        return path
