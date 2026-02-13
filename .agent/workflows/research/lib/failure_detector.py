"""
Research Agent L6 失敗検知モジュール

Phase 3.5 (Verification) で失敗パターンを検知し、
Recovery Loop への入力を提供する。

MVP実装: 上位3パターン
1. ツール実行失敗（timeout/exit code/権限）
2. スキーマ違反（JSON不正/必須欠落）
3. 虚偽完了（主張と実態の不一致）
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
import hashlib
import json

# スキーマバージョン（互換性管理用）
SCHEMA_VERSION = 1

# コンテキストの最大サイズ（バイト）
MAX_CONTEXT_SIZE = 2048

# フォールバックログパス
FALLBACK_LOG_PATH = Path("_logs/research_failures_fallback.log")

# 重大度レベル
Severity = Literal["info", "warn", "error", "fatal"]


class FailureType(Enum):
    """失敗種別（L6上位3パターン + 拡張）"""
    # MVP上位3パターン
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_NONZERO_EXIT = "tool_nonzero_exit"
    TOOL_PERMISSION = "tool_permission"
    SCHEMA_VIOLATION = "schema_violation"
    MALFORMED_JSON = "malformed_json"
    CLAIMED_BUT_NOT_DONE = "claimed_but_not_done"
    
    # 拡張パターン（将来用）
    SCOPE_DRIFT = "scope_drift"
    HALLUCINATION = "hallucination"
    SOURCE_MISMATCH = "source_mismatch"
    STALE_DATA = "stale_data"
    CONTRADICTION_UNRESOLVED = "contradiction_unresolved"
    VERIFICATION_SKIPPED = "verification_skipped"
    RETRY_LOOP = "retry_loop"
    UNKNOWN = "unknown"


class RecoveryAction(Enum):
    """回復アクション"""
    RETRY = "retry"  # リトライ
    FIX = "fix"  # 修正して再実行
    ESCALATE = "escalate"  # ユーザーにエスカレート
    SKIP = "skip"  # スキップして続行
    ABORT = "abort"  # 中止


@dataclass
class FailureEvent:
    """失敗イベント（v1 スキーマ）"""
    failure_id: str
    failure_type: FailureType
    timestamp: str
    phase: str  # "wide", "reasoning", "deep", "verification", "synthesis"
    description: str
    severity: Severity = "error"  # ★v1追加: 重大度
    context: Dict[str, Any] = field(default_factory=dict)
    recovery_action: Optional[RecoveryAction] = None
    tool_invocation_id: Optional[str] = None  # ★v1追加: ツール呼び出しID
    resolved: bool = False
    resolution_note: str = ""
    schema_version: int = SCHEMA_VERSION  # ★v1追加: スキーマバージョン
    
    def to_dict(self) -> Dict[str, Any]:
        """JSONL出力用にdict変換"""
        d = asdict(self)
        d["failure_type"] = self.failure_type.value
        if self.recovery_action:
            d["recovery_action"] = self.recovery_action.value
        # コンテキストサイズ制限
        if d.get("context"):
            ctx_str = json.dumps(d["context"], ensure_ascii=False)
            if len(ctx_str) > MAX_CONTEXT_SIZE:
                d["context"] = {"truncated": True, "original_size": len(ctx_str)}
        return d
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FailureEvent":
        """dictからFailureEventを復元"""
        d = d.copy()
        d["failure_type"] = FailureType(d["failure_type"])
        if d.get("recovery_action"):
            d["recovery_action"] = RecoveryAction(d["recovery_action"])
        return cls(**d)


def _generate_failure_id(failure_type: FailureType, context: Dict) -> str:
    """失敗IDを生成（署名ベース）"""
    sig = f"{failure_type.value}:{json.dumps(context, sort_keys=True)}"
    return f"fail_{hashlib.sha256(sig.encode()).hexdigest()[:12]}"


class FailureDetector:
    """
    失敗検知器
    
    使用例:
        detector = FailureDetector()
        
        # ツール実行結果を検査
        event = detector.detect_tool_failure(
            exit_code=1,
            stderr="Permission denied",
            timeout=False
        )
        if event:
            detector.log_failure(event)
    """
    
    def __init__(self, log_path: Optional[Path] = None):
        """
        Args:
            log_path: 失敗ログのパス（デフォルト: _logs/research_failures.jsonl）
        """
        if log_path is None:
            log_path = Path("_logs/research_failures.jsonl")
        self.log_path = log_path
        self._ensure_log_dir()
    
    def _ensure_log_dir(self):
        """ログディレクトリを作成"""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
    
    def detect_tool_failure(
        self,
        exit_code: int,
        stderr: str = "",
        timeout: bool = False,
        phase: str = "verification"
    ) -> Optional[FailureEvent]:
        """
        ツール実行失敗を検知
        
        Args:
            exit_code: 終了コード
            stderr: 標準エラー出力
            timeout: タイムアウトフラグ
            phase: 現在のフェーズ
        
        Returns:
            失敗イベント（検知した場合）
        """
        context = {
            "exit_code": exit_code,
            "stderr_snippet": stderr[:500] if stderr else "",
            "timeout": timeout
        }
        
        if timeout:
            return FailureEvent(
                failure_id=_generate_failure_id(FailureType.TOOL_TIMEOUT, context),
                failure_type=FailureType.TOOL_TIMEOUT,
                timestamp=datetime.now().isoformat(),
                phase=phase,
                description="ツール実行がタイムアウト",
                context=context,
                recovery_action=RecoveryAction.RETRY
            )
        
        if exit_code != 0:
            # 権限エラーを検出
            permission_keywords = ["permission denied", "eacces", "eperm", "access denied"]
            if any(kw in stderr.lower() for kw in permission_keywords):
                return FailureEvent(
                    failure_id=_generate_failure_id(FailureType.TOOL_PERMISSION, context),
                    failure_type=FailureType.TOOL_PERMISSION,
                    timestamp=datetime.now().isoformat(),
                    phase=phase,
                    description="権限エラーによりツール実行失敗",
                    context=context,
                    recovery_action=RecoveryAction.ESCALATE
                )
            
            return FailureEvent(
                failure_id=_generate_failure_id(FailureType.TOOL_NONZERO_EXIT, context),
                failure_type=FailureType.TOOL_NONZERO_EXIT,
                timestamp=datetime.now().isoformat(),
                phase=phase,
                description=f"ツール実行失敗（exit_code={exit_code}）",
                context=context,
                recovery_action=RecoveryAction.FIX
            )
        
        return None
    
    def detect_schema_violation(
        self,
        data: Any,
        required_fields: List[str],
        phase: str = "verification"
    ) -> Optional[FailureEvent]:
        """
        スキーマ違反を検知
        
        Args:
            data: 検査対象データ
            required_fields: 必須フィールドリスト
            phase: 現在のフェーズ
        
        Returns:
            失敗イベント（検知した場合）
        """
        # JSON不正
        if not isinstance(data, dict):
            context = {"data_type": str(type(data))}
            return FailureEvent(
                failure_id=_generate_failure_id(FailureType.MALFORMED_JSON, context),
                failure_type=FailureType.MALFORMED_JSON,
                timestamp=datetime.now().isoformat(),
                phase=phase,
                description="データがdict形式ではない",
                context=context,
                recovery_action=RecoveryAction.FIX
            )
        
        # 必須フィールド欠落
        missing = [f for f in required_fields if f not in data]
        if missing:
            context = {"missing_fields": missing, "present_fields": list(data.keys())}
            return FailureEvent(
                failure_id=_generate_failure_id(FailureType.SCHEMA_VIOLATION, context),
                failure_type=FailureType.SCHEMA_VIOLATION,
                timestamp=datetime.now().isoformat(),
                phase=phase,
                description=f"必須フィールド欠落: {missing}",
                context=context,
                recovery_action=RecoveryAction.FIX
            )
        
        return None
    
    def detect_claimed_but_not_done(
        self,
        claim: str,
        evidence: Optional[str],
        phase: str = "verification"
    ) -> Optional[FailureEvent]:
        """
        虚偽完了を検知（主張と実態の不一致）
        
        Args:
            claim: 主張（例: "ファイルを作成した"）
            evidence: 証拠（例: ファイルパス、git diff等）。Noneなら未検証
            phase: 現在のフェーズ
        
        Returns:
            失敗イベント（検知した場合）
        """
        if evidence is None or evidence.strip() == "":
            context = {"claim": claim, "evidence": None}
            return FailureEvent(
                failure_id=_generate_failure_id(FailureType.CLAIMED_BUT_NOT_DONE, context),
                failure_type=FailureType.CLAIMED_BUT_NOT_DONE,
                timestamp=datetime.now().isoformat(),
                phase=phase,
                description=f"主張に対する証拠がない: {claim[:100]}",
                context=context,
                recovery_action=RecoveryAction.FIX
            )
        
        return None
    
    def log_failure(self, event: FailureEvent) -> None:
        """
        失敗イベントをJSONLに永続化（★v1.1: エラーハンドリング追加）
        
        Args:
            event: 失敗イベント
        
        永続化失敗時はフォールバックログか標準エラーに退避
        """
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except (IOError, OSError) as e:
            # フォールバック1: 別ファイルに退避
            try:
                FALLBACK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(FALLBACK_LOG_PATH, "a", encoding="utf-8") as f:
                    fallback_entry = {
                        "original_error": str(e),
                        "event": event.to_dict()
                    }
                    f.write(json.dumps(fallback_entry, ensure_ascii=False) + "\n")
            except Exception:
                # フォールバック2: 標準エラー出力
                import sys
                print(f"[FailureDetector] 永続化失敗: {e}", file=sys.stderr)
                print(f"[FailureDetector] Event: {event.to_dict()}", file=sys.stderr)
    
    def load_failures(self, limit: int = 100) -> List[FailureEvent]:
        """
        過去の失敗イベントを読み込み
        
        Args:
            limit: 読み込む最大件数（新しい順）
        
        Returns:
            失敗イベントリスト
        """
        if not self.log_path.exists():
            return []
        
        events = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(FailureEvent.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, KeyError):
                        continue
        
        return events[-limit:]
    
    def mark_resolved(
        self,
        failure_id: str,
        resolution_note: str = ""
    ) -> bool:
        """
        ★v1.1追加: 失敗イベントを解決済みにマーク
        
        Args:
            failure_id: 失敗ID
            resolution_note: 解決メモ
        
        Returns:
            成功したらTrue
        """
        if not self.log_path.exists():
            return False
        
        # 全件読み込み
        events = []
        found = False
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = FailureEvent.from_dict(json.loads(line))
                    if event.failure_id == failure_id:
                        event.resolved = True
                        event.resolution_note = resolution_note
                        found = True
                    events.append(event)
                except (json.JSONDecodeError, KeyError):
                    continue
        
        if not found:
            return False
        
        # 全件書き戻し
        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            return True
        except (IOError, OSError):
            return False
    
    def get_failure_summary(self) -> Dict[str, int]:
        """
        失敗種別ごとの集計
        
        Returns:
            {failure_type: count} の辞書
        """
        events = self.load_failures(limit=1000)
        summary = {}
        for event in events:
            key = event.failure_type.value
            summary[key] = summary.get(key, 0) + 1
        return summary
    
    def get_top_patterns(
        self,
        k: int = 3,
        window: int = 200,
        group_by: Tuple[str, ...] = ("failure_type",)
    ) -> List[Dict[str, Any]]:
        """
        ★v1追加: 上位K件の失敗パターンを取得
        
        Args:
            k: 上位件数
            window: 直近何件から集計するか
            group_by: グループ化キー（"failure_type", "phase", "tool_invocation_id"）
        
        Returns:
            [{"pattern": {...}, "count": N, "last_seen": timestamp}, ...]
        """
        events = self.load_failures(limit=window)
        pattern_counts: Dict[str, Dict] = {}
        
        for event in events:
            # パターンキー生成
            key_parts = []
            for field in group_by:
                if field == "failure_type":
                    key_parts.append(event.failure_type.value)
                elif field == "phase":
                    key_parts.append(event.phase)
                elif field == "tool_invocation_id" and event.tool_invocation_id:
                    key_parts.append(event.tool_invocation_id)
            
            key = "|".join(key_parts)
            if key not in pattern_counts:
                pattern_counts[key] = {
                    "pattern": {f: key_parts[i] for i, f in enumerate(group_by) if i < len(key_parts)},
                    "count": 0,
                    "last_seen": event.timestamp
                }
            pattern_counts[key]["count"] += 1
            pattern_counts[key]["last_seen"] = event.timestamp
        
        # 頻度順でソート
        sorted_patterns = sorted(
            pattern_counts.values(),
            key=lambda x: x["count"],
            reverse=True
        )
        return sorted_patterns[:k]


# Phase 3.5 Verification からの呼び出しヘルパー
def check_verification_failures(
    tool_results: List[Dict],
    claims: List[Dict],
    detector: Optional[FailureDetector] = None
) -> List[FailureEvent]:
    """
    Verification フェーズで失敗を検知
    
    Args:
        tool_results: ツール実行結果リスト
        claims: 検証対象Claimリスト
        detector: 失敗検知器（省略時は新規作成）
    
    Returns:
        検知した失敗イベントリスト
    """
    if detector is None:
        detector = FailureDetector()
    
    failures = []
    
    # ツール実行失敗チェック
    for result in tool_results:
        event = detector.detect_tool_failure(
            exit_code=result.get("exit_code", 0),
            stderr=result.get("stderr", ""),
            timeout=result.get("timeout", False),
            phase="verification"
        )
        if event:
            failures.append(event)
            detector.log_failure(event)
    
    # Claimスキーマチェック（v4.3.2: statement/claim_text両方許容）
    required_claim_fields = ["claim_id", "status"]  # statement/claim_textは別途チェック
    for claim in claims:
        # v4.3.2: statement OR claim_text のいずれかが必須
        has_text = claim.get("statement") or claim.get("claim_text")
        if not has_text:
            # v4.3.3: FailureEvent正しいパラメータで生成
            event = FailureEvent(
                failure_id=_generate_failure_id(FailureType.SCHEMA_VIOLATION, {"claim": claim}),
                failure_type=FailureType.SCHEMA_VIOLATION,
                timestamp=datetime.now().isoformat(),
                phase="verification",
                description="Claim missing 'statement' or 'claim_text' field",
                context={"claim": claim},
                severity="error"
            )
            failures.append(event)
            detector.log_failure(event)
            continue
        
        event = detector.detect_schema_violation(
            data=claim,
            required_fields=required_claim_fields,
            phase="verification"
        )
        if event:
            failures.append(event)
            detector.log_failure(event)
    
    return failures
