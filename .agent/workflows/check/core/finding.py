# /check エージェント - 共通データ構造
"""Finding共通データ構造とユーティリティ"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
from datetime import datetime


class Severity(Enum):
    """検出重要度"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Location:
    """問題箇所の位置情報"""
    file: str
    line: Optional[int] = None
    line_range: Optional[tuple] = None  # (start, end)
    
    def to_dict(self) -> Dict:
        return {
            "file": self.file,
            "line": self.line,
            "line_range": self.line_range
        }


@dataclass
class Finding:
    """検出結果のデータ構造
    
    Attributes:
        rule_id: ルールID（例: broken_path, cycle_dependency）
        severity: 重要度（high/medium/low）
        location: 問題箇所
        evidence: 検出根拠
        message: 説明メッセージ
        suggestion: 修正提案
        autofix_allowed: 自動修正可能か
        confidence: 検出確度（0.0-1.0）
    """
    rule_id: str
    severity: Severity
    location: Location
    evidence: Dict[str, Any]
    message: str = ""
    suggestion: str = ""
    autofix_allowed: bool = False
    confidence: float = 1.0
    
    def __post_init__(self):
        # 文字列で渡された場合はEnumに変換
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity.lower())
    
    def to_dict(self) -> Dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "location": self.location.to_dict(),
            "evidence": self.evidence,
            "message": self.message,
            "suggestion": self.suggestion,
            "autofix_allowed": self.autofix_allowed,
            "confidence": self.confidence
        }


@dataclass
class VerifyResult:
    """VERIFYステップの結果"""
    ok: bool
    reason: Optional[str] = None
    resolved: List[str] = field(default_factory=list)  # 解消されたfinding id
    regressed: List[Finding] = field(default_factory=list)  # 退行したfindings
    post_digest: Optional[Dict] = None


@dataclass
class ParseError:
    """パースエラー情報"""
    path: str
    msg: str
    line: Optional[int] = None
    snippet: str = ""


@dataclass
class Edge:
    """依存グラフのエッジ"""
    src_file: str
    dst_file: str
    edge_type: str  # import/extends/uses/path_literal
    raw_target: str  # 元の参照文字列
    confidence: float
    line_range: Optional[tuple] = None
    snippet: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "src": self.src_file,
            "dst": self.dst_file,
            "type": self.edge_type,
            "raw": self.raw_target,
            "confidence": self.confidence,
            "line_range": self.line_range
        }
