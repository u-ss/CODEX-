# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Verification Module
Phase 3.5: 検証フェーズのデータ構造

Note: 実際の検証ロジックはAntigravity（LLM）がSKILL.mdに従って実行

v4.3.3 変更点:
- ClaimStatusをmodels.pyから参照（Enum→Literal統一）
- CounterEvidence.stanceをmodels.pyから参照

v4.3.2 変更点:
- CounterEvidence.stance: int → Stance（文字列Enum）
- determine_status: CONTESTED判定を早期に移動（埋もれ防止）
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Literal
from enum import Enum

from .models import Stance, ClaimStatus


# v4.3.3: 後方互換のためEnumを維持（値はClaimStatusと同一）
class ClaimStatusEnum(str, Enum):
    """Claimの検証ステータス（後方互換用Enum）"""
    VERIFIED = "VERIFIED"
    CONDITIONED = "CONDITIONED"
    CONTESTED = "CONTESTED"
    UNSUPPORTED = "UNSUPPORTED"
    REFUTED = "REFUTED"


class ClaimType(str, Enum):
    """Claimの種類"""
    FACT = "FACT"           # 事実
    FORECAST = "FORECAST"   # 予測
    INFERENCE = "INFERENCE" # 推論
    OPINION = "OPINION"     # 意見
    POLICY = "POLICY"       # 方針/規制


@dataclass
class Issue:
    """検証で発見された問題"""
    code: str           # SCOPE_MISSING, TELEPHONE_GAME, STALE, etc.
    severity: str       # LOW/MED/HIGH
    message: str


@dataclass
class CounterEvidence:
    """反証情報（v4.3.2: stance文字列化）"""
    type: str           # DIRECT/WEAKENING/CONDITION
    stance: str = "refutes"  # v4.3.2: 文字列Enum統一
    summary: str = ""
    source_url: str = ""
    weight: float = 0.0


@dataclass
class RequiredAction:
    """追加調査アクション"""
    type: str           # PRIMARY_SOURCE_SEARCH, DEFINITION_CHECK, etc.
    priority: int
    query: str
    stop_condition: str
    rationale: str


@dataclass
class Citation:
    """引用情報"""
    url: str
    role: str           # PRIMARY/INDEPENDENT_CONFIRMATION/CONTEXT


@dataclass
class PrimarySourceTrace:
    """一次ソース照合結果"""
    primary_sources: List[Dict[str, Any]] = field(default_factory=list)
    trace_paths: List[Dict[str, Any]] = field(default_factory=list)
    telephone_risk: float = 0.0  # 0〜1、伝言ゲームリスク


@dataclass
class EvidenceSummary:
    """証拠サマリ"""
    pos_weight: float = 0.0
    neg_weight: float = 0.0
    neutral_weight: float = 0.0
    telephone_risk: float = 0.0


@dataclass
class Scope:
    """Claimのスコープ"""
    time_range: Optional[Dict[str, Any]] = None
    geo: Optional[str] = None
    population: Optional[str] = None
    definitions: Dict[str, str] = field(default_factory=dict)


@dataclass
class VerifiedClaim:
    """検証済みClaim（Phase 3.5の出力）"""
    claim_id: str
    claim_text: str
    claim_type: ClaimType = ClaimType.FACT
    scope: Scope = field(default_factory=Scope)
    
    # 検証結果
    status: ClaimStatusEnum = ClaimStatusEnum.UNSUPPORTED
    confidence_before: float = 0.0
    confidence_after: float = 0.0
    
    # 証拠サマリ
    evidence_summary: EvidenceSummary = field(default_factory=EvidenceSummary)
    primary_source_trace: PrimarySourceTrace = field(default_factory=PrimarySourceTrace)
    
    # 検出事項
    issues: List[Issue] = field(default_factory=list)
    counter_evidence: List[CounterEvidence] = field(default_factory=list)
    
    # 採用/除外
    recommended_citations: List[Citation] = field(default_factory=list)
    rejected_citations: List[Citation] = field(default_factory=list)
    
    # 追加アクション
    required_actions: List[RequiredAction] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """辞書形式でエクスポート"""
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "claim_type": self.claim_type.value,
            "status": self.status.value,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "evidence_summary": {
                "pos_weight": self.evidence_summary.pos_weight,
                "neg_weight": self.evidence_summary.neg_weight,
                "neutral_weight": self.evidence_summary.neutral_weight,
                "telephone_risk": self.evidence_summary.telephone_risk,
            },
            "issues": [
                {"code": i.code, "severity": i.severity, "message": i.message}
                for i in self.issues
            ],
            "required_actions": [
                {"type": a.type, "priority": a.priority, "query": a.query}
                for a in self.required_actions
            ],
        }


def is_contested(pos_weight: float, neg_weight: float, threshold: float = 0.5) -> bool:
    """
    CONTESTED判定ヘルパー（v4.3.2追加）
    
    Args:
        pos_weight: 支持証拠の重み
        neg_weight: 反証の重み
        threshold: 挑戦判定の閾値
    
    Returns:
        両方が強い場合True
    """
    return pos_weight > threshold and neg_weight > threshold


def determine_status(
    confidence: float,
    pos_weight: float,
    neg_weight: float,
    has_primary_source: bool,
    telephone_risk: float
) -> ClaimStatusEnum:
    """
    confidenceと証拠状況からstatusを判定
    
    Note: これは目安であり、最終判定はAntigravityが行う
    
    v4.3.2: CONTESTED判定を早期に移動（埋もれ防止）
    """
    # v4.3.2: 支持・反対が拮抗（最初に判定）
    if is_contested(pos_weight, neg_weight):
        return ClaimStatusEnum.CONTESTED
    
    # 直接否定
    if confidence <= -0.5 and neg_weight > pos_weight * 2:
        return ClaimStatusEnum.REFUTED
    
    # 高信頼で反証なし
    if confidence >= 0.6 and has_primary_source and telephone_risk < 0.3:
        return ClaimStatusEnum.VERIFIED
    
    # 概ね支持、条件付き
    if confidence >= 0.3 and pos_weight > neg_weight:
        return ClaimStatusEnum.CONDITIONED
    
    # 根拠不足
    return ClaimStatusEnum.UNSUPPORTED
