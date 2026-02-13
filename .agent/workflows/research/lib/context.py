# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Context Module
Phase間で共有される実行コンテキスト

GPT-5.2設計: ResearchRunContextでセッション状態を一元管理
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from .termination import TerminationConfig, TerminationState
from .failure_detector import FailureEvent


@dataclass
class ResearchRunContext:
    """
    リサーチセッションの共有コンテキスト
    
    全Phaseがこのコンテキストを読み書きし、
    Orchestratorが状態遷移を制御する
    """
    # セッション識別
    session_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    query: str = ""  # 元のリサーチクエリ
    
    # Phase 1 出力
    raw_claims: List[Dict[str, Any]] = field(default_factory=list)
    search_log: List[Dict[str, Any]] = field(default_factory=list)
    
    # Phase 2 出力
    normalized_claims: List[Dict[str, Any]] = field(default_factory=list)
    gaps: List[Dict[str, Any]] = field(default_factory=list)
    sub_questions: List[str] = field(default_factory=list)
    
    # Phase 3 出力
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    coverage_map: Dict[str, Any] = field(default_factory=dict)
    deep_round: int = 0  # Phase 3のラウンド番号
    
    # Phase 3.5 出力
    verified_claims: List[Dict[str, Any]] = field(default_factory=list)
    counterevidence_log: List[Dict[str, Any]] = field(default_factory=list)
    required_actions: List[Dict[str, Any]] = field(default_factory=list)  # 差し戻し用
    
    # Phase 4 出力
    final_report: Optional[str] = None
    report_data: Optional[Dict[str, Any]] = None  # 構造化レポート（監査/再利用用）
    
    # 終了条件管理
    termination_config: TerminationConfig = field(default_factory=TerminationConfig)
    termination_state: TerminationState = field(default_factory=TerminationState)
    
    # 失敗追跡
    failures: List[FailureEvent] = field(default_factory=list)
    
    # 実行メトリクス
    phase_results: List[Dict[str, Any]] = field(default_factory=list)
    verify_rollback_count: int = 0  # Phase 3.5 → 3 の差し戻し回数
    max_verify_rollbacks: int = 2  # 差し戻し上限
    
    # 出力パス
    output_dir: Optional[Path] = None
    output_integrity_pass: bool = True
    missing_output_artifacts: List[str] = field(default_factory=list)
    workflow_logger: Optional[Any] = None
    
    # サーキットブレーカー（403再試行抑止）
    circuit_breaker: Optional[Any] = None
    seen_urls: set = field(default_factory=set)  # 同run内URL重複fetch防止
    
    def add_failure(self, event: FailureEvent):
        """失敗イベントを追加"""
        self.failures.append(event)
    
    def can_rollback(self) -> bool:
        """差し戻し可能か（上限チェック）"""
        return self.verify_rollback_count < self.max_verify_rollbacks
    
    def increment_rollback(self):
        """差し戻しカウントをインクリメント"""
        self.verify_rollback_count += 1
    
    def get_summary(self) -> Dict[str, Any]:
        """実行サマリを取得"""
        search_queries_attempted = sum(
            1
            for row in self.search_log
            if isinstance(row, dict) and str(row.get("query", "")).strip()
        )
        # サーキットブレーカー統計
        breaker_stats = self.circuit_breaker.get_stats() if self.circuit_breaker else {}
        return {
            "session_id": self.session_id,
            "query": self.query,
            "raw_claims_count": len(self.raw_claims),
            "normalized_claims_count": len(self.normalized_claims),
            "evidence_count": len(self.evidence),
            "verified_claims_count": len(self.verified_claims),
            "failures_count": len(self.failures),
            "search_queries_attempted": search_queries_attempted,
            "deep_rounds": self.deep_round,
            "verify_rollbacks": self.verify_rollback_count,
            "has_report": self.final_report is not None,
            "output_integrity_pass": bool(self.output_integrity_pass),
            "missing_output_artifacts": list(self.missing_output_artifacts),
            # サーキットブレーカー統計
            "retry_guard_stats": breaker_stats,
            "blocked_urls_count": breaker_stats.get("blocked_urls", 0),
            "blocked_hosts_count": breaker_stats.get("blocked_hosts", 0),
            "max_same_url_attempts": breaker_stats.get("max_same_url_attempts", 0),
        }
