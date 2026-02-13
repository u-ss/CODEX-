# -*- coding: utf-8 -*-
"""
Research Agent v4.3.2 - Scoring Module
信頼性スコア計算（freshness × authority × bias）

v4.3.2 変更点:
- stance: int → 文字列Enum（"supports"/"refutes"/"neutral"）に統一
- stance_to_int()で後方互換
- timezone未使用import削除
"""

from dataclasses import dataclass, field
from datetime import datetime
from math import exp, sqrt
from typing import List, Dict, Set, Any, Optional, Literal

from .models import Stance, stance_to_int


# 定数
EPS = 1e-9

# ソースTier定義
TIER_BASE = {
    "S": 1.00,  # 規格/法令/政府/公式ドキュメント/一次ソース
    "A": 0.85,  # 査読論文/主要学会/統計機関
    "B": 0.70,  # 専門企業ブログ/技術者ブログ（実測あり）
    "C": 0.55,  # まとめサイト/一般ブログ/Q&Aフォーラム
    "D": 0.40,  # SNS/匿名掲示板
}

# 半減期（日数）
HALF_LIFE_DAYS = {
    "FAST": 7.0,     # 速報/脆い話題
    "MEDIUM": 30.0,  # 通常
    "SLOW": 365.0,   # 歴史/定義/不変に近い
}

# バイアスフラグの重み
BIAS_WEIGHTS = {
    "sponsored": 0.9,
    "affiliate": 0.7,
    "vendor": 0.6,
    "press_release": 0.5,
    "partner": 0.4,
}


@dataclass
class Evidence:
    """証拠データ（v4.3.2: stance文字列Enum化）"""
    claim_id: str
    url: str
    tier: str = "C"  # S/A/B/C/D
    published_at: Optional[datetime] = None
    stance: Stance = "neutral"  # v4.3.2: 文字列Enum統一
    bias_flags: Set[str] = field(default_factory=set)
    citations_to_high_tier: int = 0  # 高Tier/一次への参照数
    cluster_key: str = ""  # 相関抑制用（domain/publisher）


@dataclass
class ScoredEvidence:
    """スコア付き証拠"""
    evidence: Evidence
    freshness_score: float
    authority_score: float
    bias_risk: float
    evidence_weight: float


def freshness_score(
    published_at: Optional[datetime],
    now: datetime,
    time_sensitivity: str = "MEDIUM"
) -> float:
    """
    鮮度スコア（指数減衰）
    
    Args:
        published_at: 公開日時（Noneなら0.5を返す）
        now: 現在日時
        time_sensitivity: FAST/MEDIUM/SLOW
    
    Returns:
        0.0〜1.0のスコア
    """
    if published_at is None:
        return 0.5  # 不明な場合は中程度
    
    # 両方ともtimezone-naiveに揃える
    if published_at.tzinfo is not None:
        published_at = published_at.replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    
    age_days = max(0.0, (now - published_at).total_seconds() / 86400.0)
    half_life = HALF_LIFE_DAYS.get(time_sensitivity, HALF_LIFE_DAYS["MEDIUM"])
    return 0.5 ** (age_days / half_life)


def authority_score(
    tier: str,
    citations_to_high_tier: int = 0,
    bonus_max: float = 0.25,
    tau: float = 3.0
) -> float:
    """
    権威性スコア
    
    Args:
        tier: ソースのTier（S/A/B/C/D）
        citations_to_high_tier: 高Tierへの引用数
        bonus_max: 引用ボーナスの最大値
        tau: 飽和パラメータ
    
    Returns:
        0.0〜1.0のスコア
    """
    base = TIER_BASE.get(tier.upper(), 0.55)
    k = max(0, citations_to_high_tier)
    # 飽和する引用ボーナス
    citation_bonus = 1.0 + bonus_max * (1.0 - exp(-k / tau))
    return min(1.0, base * citation_bonus)


def bias_risk_from_flags(flags: Set[str]) -> float:
    """
    バイアスリスクを算出
    
    Args:
        flags: バイアスフラグのセット
    
    Returns:
        0.0〜1.0のリスク値
    """
    if not flags:
        return 0.0
    # 最大リスクを採用
    return max(BIAS_WEIGHTS.get(f.lower(), 0.3) for f in flags)


def evidence_weight(
    ev: Evidence,
    now: datetime,
    time_sensitivity: str = "MEDIUM",
    penalty: float = 0.8,
    min_bias: float = 0.2
) -> ScoredEvidence:
    """
    証拠の重みを計算
    
    Args:
        ev: Evidence
        now: 現在日時
        time_sensitivity: FAST/MEDIUM/SLOW
        penalty: バイアスペナルティ係数
        min_bias: バイアスの下限（完全ゼロにしない）
    
    Returns:
        ScoredEvidence
    """
    f = freshness_score(ev.published_at, now, time_sensitivity)
    a = authority_score(ev.tier, ev.citations_to_high_tier)
    r = bias_risk_from_flags(ev.bias_flags)
    bias_multiplier = max(min_bias, 1.0 - r * penalty)
    weight = a * f * bias_multiplier
    
    return ScoredEvidence(
        evidence=ev,
        freshness_score=f,
        authority_score=a,
        bias_risk=r,
        evidence_weight=weight
    )


@dataclass
class ConfidenceResult:
    """信頼度集約結果"""
    confidence: float  # -1.0〜+1.0
    pos_weight: float
    neg_weight: float
    neutral_weight: float
    evidence_mass: float
    coverage: float
    details: List[Dict[str, Any]]


def aggregate_confidence(
    evidences: List[Evidence],
    now: datetime,
    time_sensitivity: str = "MEDIUM"
) -> ConfidenceResult:
    """
    証拠を集約して信頼度を計算
    
    Args:
        evidences: Evidenceのリスト
        now: 現在日時
        time_sensitivity: FAST/MEDIUM/SLOW
    
    Returns:
        ConfidenceResult
    """
    # 相関抑制：同一cluster内のk本目は 1/sqrt(k) で減衰
    seen: Dict[str, int] = {}
    pos = 0.0
    neg = 0.0
    neutral = 0.0
    details = []
    
    for ev in evidences:
        scored = evidence_weight(ev, now, time_sensitivity)
        w = scored.evidence_weight
        
        # 相関減衰
        if ev.cluster_key:
            k = seen.get(ev.cluster_key, 0) + 1
            seen[ev.cluster_key] = k
            corr_mult = 1.0 / sqrt(k)
            w *= corr_mult
        
        # stanceを数値に変換（v4.3.2: 後方互換）
        stance_val = stance_to_int(ev.stance)
        contrib = w * float(stance_val)
        details.append({
            "url": ev.url,
            "cluster_key": ev.cluster_key,
            "tier": ev.tier,
            "stance": ev.stance,  # 文字列のまま出力
            "weight": w,
            "contrib": contrib,
        })
        
        if stance_val > 0:
            pos += w
        elif stance_val < 0:
            neg += w
        else:
            neutral += w
    
    # 正規化して[-1, +1]に
    confidence = (pos - neg) / (pos + neg + EPS)
    evidence_mass = pos + neg
    coverage = evidence_mass / (evidence_mass + neutral + EPS)
    
    return ConfidenceResult(
        confidence=confidence,
        pos_weight=pos,
        neg_weight=neg,
        neutral_weight=neutral,
        evidence_mass=evidence_mass,
        coverage=coverage,
        details=details
    )
