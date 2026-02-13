# -*- coding: utf-8 -*-
"""
Implementation Agent v4.2.4 - Self-Healing Module
失敗分類 + 適応リトライ + 局所サーキットブレーカー

v4.2.1 変更点:
- CircuitBreakerにcooldown_seconds/next_retry_at追加（自律復帰）
- ENVIRONMENTは修復後リトライ可能に変更
- suggest_recovery_action()追加

v4.2.4 変更点:
- allow()の日時比較をdatetime.fromisoformat()で厳密化
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
from hashlib import sha256
import re


class FailureCategory(Enum):
    """失敗カテゴリ"""
    TRANSIENT = "transient"         # ネットワーク、タイムアウト → リトライ価値あり
    DETERMINISTIC = "deterministic" # 型エラー、テスト失敗 → 修正が必要
    ENVIRONMENT = "environment"     # 依存不足、バージョン差 → 環境修復
    FLAKY = "flaky"                # 不安定テスト → 隔離
    POLICY = "policy"              # 危険操作、制約違反 → 即停止
    UNKNOWN = "unknown"


@dataclass
class FailureRecord:
    """失敗記録"""
    phase: str
    command: str
    signature_hash: str
    category: FailureCategory
    attempt: int
    first_seen_at: str
    last_seen_at: str
    stderr_snippet: str = ""
    exit_code: int = 1


class CircuitState(Enum):
    """サーキットブレーカー状態"""
    CLOSED = "closed"    # 正常
    OPEN = "open"        # 遮断中
    HALF_OPEN = "half"   # 試行中


@dataclass
class CircuitBreaker:
    """局所サーキットブレーカー（v4.2.4: datetime厳密比較）"""
    key: str  # cmd_hash or test_id
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    opened_at: Optional[str] = None
    
    # 設定
    failure_threshold: int = 3
    success_threshold: int = 2  # HALF_OPEN時の成功回数
    
    # v4.2.1追加: 自律復帰
    cooldown_seconds: int = 60  # OPENからHALF_OPENへのクールダウン
    next_retry_at: Optional[str] = None  # HALF_OPEN試行可能時刻
    
    def on_failure(self) -> None:
        """失敗時の処理"""
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = datetime.now().isoformat()
            # v4.2.1: 自律復帰時刻を計算
            retry_time = datetime.now() + timedelta(seconds=self.cooldown_seconds)
            self.next_retry_at = retry_time.isoformat()
    
    def on_success(self) -> None:
        """成功時の処理"""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0
    
    def allow(self) -> bool:
        """実行許可判定（v4.2.4: datetime厳密比較）"""
        if self.state == CircuitState.OPEN:
            # v4.2.4: cooldown経過で自動HALF_OPEN（datetime厳密比較）
            if self.next_retry_at:
                retry_time = datetime.fromisoformat(self.next_retry_at)
                if datetime.now() >= retry_time:
                    self.try_half_open()
                    return True
            return False
        return True
    
    def try_half_open(self) -> None:
        """外部からHALF_OPENに遷移（試行許可）"""
        if self.state == CircuitState.OPEN:
            self.state = CircuitState.HALF_OPEN
            self.success_count = 0
            self.next_retry_at = None


# 失敗パターン（正規表現）
FAILURE_PATTERNS = {
    FailureCategory.TRANSIENT: [
        r"timeout",
        r"connection refused",
        r"network error",
        r"ECONNRESET",
        r"rate limit",
    ],
    FailureCategory.ENVIRONMENT: [
        r"module not found",
        r"command not found",
        r"No such file or directory",
        r"ModuleNotFoundError",
        r"ImportError",
        r"version mismatch",
    ],
    FailureCategory.DETERMINISTIC: [
        r"TypeError",
        r"SyntaxError",
        r"AssertionError",
        r"FAILED",
        r"error:",
    ],
    FailureCategory.FLAKY: [
        r"flaky",
        r"intermittent",
        r"race condition",
    ],
    FailureCategory.POLICY: [
        r"permission denied",
        r"access denied",
        r"security",
        r"forbidden",
    ],
}


def compute_signature(command: str, stderr: str) -> str:
    """エラーシグネチャを計算"""
    # コマンド + stderrの要点をハッシュ化
    normalized = re.sub(r'\d+', 'N', stderr[:500])  # 数値を正規化
    content = f"{command}|{normalized}"
    return sha256(content.encode()).hexdigest()[:16]


def classify_failure(
    stderr: str,
    exit_code: int,
    phase: str
) -> FailureCategory:
    """
    失敗を分類
    
    Args:
        stderr: 標準エラー出力
        exit_code: 終了コード
        phase: フェーズ名
    
    Returns:
        FailureCategory
    """
    stderr_lower = stderr.lower()
    
    # パターンマッチング
    for category, patterns in FAILURE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, stderr_lower, re.IGNORECASE):
                return category
    
    # 終了コードベースの推測
    if exit_code == 0:
        return FailureCategory.UNKNOWN
    elif exit_code in [124, 137]:  # timeout/killed
        return FailureCategory.TRANSIENT
    elif exit_code == 1:
        return FailureCategory.DETERMINISTIC
    
    return FailureCategory.UNKNOWN


def should_retry(
    record: FailureRecord,
    max_attempts: int = 3
) -> Tuple[bool, str]:
    """
    リトライすべきか判定
    
    Args:
        record: FailureRecord
        max_attempts: 最大試行回数
    
    Returns:
        (should_retry, reason)
    """
    if record.attempt >= max_attempts:
        return False, "max_attempts_reached"
    
    if record.category == FailureCategory.TRANSIENT:
        return True, "transient_failure"
    
    if record.category == FailureCategory.FLAKY:
        return True, "flaky_test"
    
    if record.category == FailureCategory.DETERMINISTIC:
        return False, "requires_fix"
    
    if record.category == FailureCategory.ENVIRONMENT:
        # v4.2.1: ENVIRONMENTは修復後リトライ可能
        return True, "requires_env_repair_then_retry"
    
    if record.category == FailureCategory.POLICY:
        return False, "policy_violation"
    
    return False, "unknown_failure"


def suggest_action(category: FailureCategory) -> str:
    """
    カテゴリに基づいて推奨アクションを提案
    """
    actions = {
        FailureCategory.TRANSIENT: "リトライ（exponential backoff）",
        FailureCategory.DETERMINISTIC: "コード修正 → 再テスト",
        FailureCategory.ENVIRONMENT: "環境修復（依存インストール/バージョン確認）",
        FailureCategory.FLAKY: "テスト隔離 → 安定化修正",
        FailureCategory.POLICY: "即停止 → ユーザー報告",
        FailureCategory.UNKNOWN: "ログ確認 → 手動判断",
    }
    return actions.get(category, "手動判断")
