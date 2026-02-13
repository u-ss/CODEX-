# -*- coding: utf-8 -*-
"""
Research Agent v4.3.3 - Termination Module
終了条件判定（coverage/convergence/marginal utility）

v4.3.3 変更点:
- ClaimStatus をmodels.pyから参照（単一ソース化）
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Literal, Optional
from math import fabs, sqrt

from .models import ClaimStatus

# 型定義
GapState = Literal["OPEN", "CLOSED", "BLOCKED"]
Status = ClaimStatus  # v4.3.3: 後方互換エイリアス


@dataclass
class ClaimSnapshot:
    """ラウンドごとのClaim状態"""
    claim_id: str
    status: Status
    confidence: float  # -1〜+1
    evidence_mass: float
    telephone_risk: float


@dataclass
class RoundSnapshot:
    """ラウンドのスナップショット"""
    round_idx: int
    gaps: Dict[str, GapState]
    claims: Dict[str, ClaimSnapshot]
    cost: float  # このラウンドのコスト（検索回数など）
    budget_used: float  # 累積使用量
    budget_limit: float
    hard_cap_rounds: int


@dataclass
class TerminationConfig:
    """終了条件の設定"""
    # Coverage
    target_coverage: float = 0.75
    min_coverage: float = 0.55
    
    # Convergence
    eps: float = 0.02  # confidence変化量の閾値
    tau: int = 3       # 監視ウィンドウ（ラウンド数）
    top_k: int = 12    # 重要Claim数
    
    # Marginal Utility
    mu_threshold: float = 0.15
    N: int = 3         # 連続低効用回数
    
    # Utility重み
    w_coverage: float = 0.45
    w_mass: float = 0.20
    w_tel: float = 0.20
    w_status: float = 0.15
    
    # Hard caps
    hard_cap_budget: float = 60.0
    tel_improve_threshold: float = 0.15


@dataclass
class TerminationState:
    """終了判定の状態"""
    history: List[RoundSnapshot] = field(default_factory=list)
    low_mu_streak: int = 0


@dataclass
class TerminationResult:
    """終了判定の結果"""
    should_stop: bool
    coverage: float
    converged: bool
    utility: float
    mu: float
    low_mu_streak: int
    low_mu_confirmed: bool
    budget_exceeded: bool
    hard_cap: bool
    reason: str


def coverage_rate(gaps: Dict[str, GapState]) -> float:
    """Gap閉鎖率を計算"""
    if not gaps:
        return 0.0
    closed = sum(1 for s in gaps.values() if s == "CLOSED")
    return closed / float(len(gaps))


def status_score(status: Status) -> int:
    """ステータスを数値化（改善度判定用）"""
    order = {
        "REFUTED": 0,
        "UNSUPPORTED": 1,
        "CONTESTED": 2,
        "CONDITIONED": 3,
        "VERIFIED": 4
    }
    return order.get(status, 1)


def select_top_k_claims(
    claims: Dict[str, ClaimSnapshot],
    k: int
) -> List[str]:
    """重要なClaim（evidence_mass上位）を選択"""
    items = sorted(
        claims.items(),
        key=lambda kv: kv[1].evidence_mass,
        reverse=True
    )
    return [cid for cid, _ in items[:k]]


def compute_converged(
    history: List[RoundSnapshot],
    cfg: TerminationConfig
) -> bool:
    """
    収束判定：Top-K ClaimのConfidenceが安定しているか
    """
    if len(history) < cfg.tau + 1:
        return False
    
    latest = history[-1]
    watch_ids = select_top_k_claims(latest.claims, cfg.top_k)
    
    # 直近τラウンドでの変化量を計算
    deltas: List[float] = []
    for t in range(len(history) - cfg.tau, len(history)):
        prev = history[t - 1]
        cur = history[t]
        for cid in watch_ids:
            if cid in prev.claims and cid in cur.claims:
                delta = fabs(
                    cur.claims[cid].confidence - prev.claims[cid].confidence
                )
                deltas.append(delta)
    
    if not deltas:
        return False
    
    avg_delta = sum(deltas) / len(deltas)
    return avg_delta < cfg.eps


def compute_round_utility(
    prev: Optional[RoundSnapshot],
    cur: RoundSnapshot,
    cfg: TerminationConfig
) -> float:
    """
    ラウンドの効用（知識獲得量）を計算
    """
    if prev is None:
        return 1.0  # 初回は有用
    
    # Δcoverage
    cov_prev = coverage_rate(prev.gaps)
    cov_cur = coverage_rate(cur.gaps)
    d_cov = max(0.0, cov_cur - cov_prev)
    
    # Δevidence_mass
    common = set(prev.claims.keys()) & set(cur.claims.keys())
    d_mass = 0.0
    for cid in common:
        d_mass += max(0.0, cur.claims[cid].evidence_mass - prev.claims[cid].evidence_mass)
    denom = max(1, len(common))
    d_mass = d_mass / denom
    
    # Δtelephone_risk（減少は良い）
    d_tel = 0.0
    for cid in common:
        drop = max(0.0, prev.claims[cid].telephone_risk - cur.claims[cid].telephone_risk)
        if drop >= cfg.tel_improve_threshold:
            d_tel += drop
    d_tel = d_tel / denom
    
    # Δstatus（向上）
    d_status = 0.0
    for cid in common:
        s_prev = status_score(prev.claims[cid].status)
        s_cur = status_score(cur.claims[cid].status)
        d_status += max(0, s_cur - s_prev)
    d_status = d_status / denom
    
    # 重み付き合計
    utility = (
        cfg.w_coverage * d_cov +
        cfg.w_mass * d_mass +
        cfg.w_tel * d_tel +
        cfg.w_status * d_status
    )
    return utility


def should_stop(
    state: TerminationState,
    cur: RoundSnapshot,
    cfg: TerminationConfig
) -> TerminationResult:
    """
    終了判定メイン関数
    
    Args:
        state: 蓄積された状態
        cur: 現在のラウンド
        cfg: 設定
    
    Returns:
        TerminationResult
    """
    prev = state.history[-1] if state.history else None
    state.history.append(cur)
    
    # 各指標を計算
    cov = coverage_rate(cur.gaps)
    converged = compute_converged(state.history, cfg)
    utility = compute_round_utility(prev, cur, cfg)
    cost = max(cur.cost, 1e-9)
    mu = utility / cost
    
    # 低効用連続カウント
    low_marginal_gain = (mu < cfg.mu_threshold)
    if low_marginal_gain:
        state.low_mu_streak += 1
    else:
        state.low_mu_streak = 0
    
    low_mu_confirmed = (state.low_mu_streak >= cfg.N)
    
    # 予算超過
    budget_exceeded = (
        cur.budget_used >= cur.budget_limit or
        cur.budget_used >= cfg.hard_cap_budget
    )
    
    # ハードキャップ
    hard_cap = (cur.round_idx >= cur.hard_cap_rounds)
    
    # 終了判定
    stop = (
        (cov >= cfg.target_coverage and (converged or low_mu_confirmed)) or
        (budget_exceeded and cov >= cfg.min_coverage) or
        hard_cap
    )
    
    # 理由を特定
    if hard_cap:
        reason = "HARD_CAP_REACHED"
    elif budget_exceeded and cov >= cfg.min_coverage:
        reason = "BUDGET_EXCEEDED_MIN_COVERAGE_MET"
    elif cov >= cfg.target_coverage and converged:
        reason = "COVERAGE_AND_CONVERGED"
    elif cov >= cfg.target_coverage and low_mu_confirmed:
        reason = "COVERAGE_AND_LOW_UTILITY"
    else:
        reason = "CONTINUE"
    
    return TerminationResult(
        should_stop=stop,
        coverage=cov,
        converged=converged,
        utility=utility,
        mu=mu,
        low_mu_streak=state.low_mu_streak,
        low_mu_confirmed=low_mu_confirmed,
        budget_exceeded=budget_exceeded,
        hard_cap=hard_cap,
        reason=reason
    )
