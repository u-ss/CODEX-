# -*- coding: utf-8 -*-
"""
設定モジュール — stealth_research全体の設定管理
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class RetryConfig:
    """リトライポリシー設定"""
    # リトライ対象ステータスコード（一時障害のみ）
    retryable_statuses: Set[int] = field(
        default_factory=lambda: {429, 500, 502, 503, 504}
    )
    # 非リトライ（即座に諦める）ステータスコード
    non_retryable_statuses: Set[int] = field(
        default_factory=lambda: {401, 403, 404, 410}
    )
    max_attempts_per_url: int = 2
    backoff_base_sec: float = 1.0
    backoff_max_sec: float = 30.0
    jitter_range: float = 0.5  # ±50%


@dataclass
class BreakerConfig:
    """サーキットブレーカー設定"""
    # Host単位: この回数403/captchaが来たらopen
    host_fail_threshold: int = 3
    # Host単位: open後のクールダウン（秒）
    host_cooldown_sec: float = 300.0  # 5分
    # URL単位: 403は1回で十分
    url_max_attempts: int = 2
    # 監視ウィンドウ（秒）
    window_sec: float = 600.0  # 10分


@dataclass
class RateLimitConfig:
    """レート制御設定"""
    # Host別の最小リクエスト間隔（秒）
    min_interval_sec: float = 2.0
    # Host別の同時実行数
    max_concurrent_per_host: int = 2
    # グローバルの最大同時実行数
    max_concurrent_global: int = 5
    # Retry-After ヘッダーの尊重
    respect_retry_after: bool = True


@dataclass
class FetchConfig:
    """フェッチャー設定"""
    timeout_sec: int = 30
    max_chars: int = 15000
    user_agents: List[str] = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    ])
    # ブラウザフェッチャー設定
    browser_headless: bool = True
    browser_timeout_ms: int = 30000


@dataclass
class SearchConfig:
    """検索エンジン設定"""
    # プロバイダ優先順位
    providers: List[str] = field(
        default_factory=lambda: ["bing_rss", "duckduckgo"]
    )
    max_results_per_provider: int = 10
    # 学術検索プロバイダ
    academic_providers: List[str] = field(
        default_factory=lambda: ["openalex", "semantic_scholar"]
    )


@dataclass
class LogConfig:
    """ログ設定"""
    # L3 Trace のJSONLファイル名接尾辞
    trace_suffix: str = "_trace.jsonl"
    # L2 Metadata のJSON接尾辞
    metadata_suffix: str = "_metadata.json"
    # L1 Summary のJSON接尾辞
    summary_suffix: str = "_summary.json"
    # コンテンツをログに含めるか（プレビュー+shaのみ推奨）
    log_content_preview_chars: int = 200
    # アーティファクト保存先
    artifact_dir: str = "_outputs/stealth_research"


@dataclass
class BudgetConfig:
    """取得予算 — 無限ループの物理的防止"""
    max_urls: int = 50
    max_fetches: int = 100  # リトライ含む
    max_time_sec: float = 600.0  # 10分
    max_bytes: int = 50_000_000  # 50MB


@dataclass
class StealthResearchConfig:
    """stealth_research全体設定"""
    retry: RetryConfig = field(default_factory=RetryConfig)
    breaker: BreakerConfig = field(default_factory=BreakerConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    log: LogConfig = field(default_factory=LogConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "StealthResearchConfig":
        """辞書から設定を生成"""
        cfg = cls()
        for section_name, section_class in [
            ("retry", RetryConfig),
            ("breaker", BreakerConfig),
            ("rate_limit", RateLimitConfig),
            ("fetch", FetchConfig),
            ("search", SearchConfig),
            ("log", LogConfig),
            ("budget", BudgetConfig),
        ]:
            if section_name in d:
                section_data = d[section_name]
                current = getattr(cfg, section_name)
                for k, v in section_data.items():
                    if hasattr(current, k):
                        setattr(current, k, v)
        return cfg
