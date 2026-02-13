"""
Locator Bank - UCB候補選択システム

ChatGPT相談（Rally 4）で設計したLocator学習・選択ロジック。
複数のセレクタ候補からUCB+安全フィルタで最良候補を選択。
"""

import math
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path
import logging

# パッケージとしてインポート、または直接実行
try:
    from .learning_store import LearningStore, CandidateStats
except ImportError:
    from learning_store import LearningStore, CandidateStats

logger = logging.getLogger(__name__)


@dataclass
class LocatorCandidate:
    """セレクタ候補"""
    selector_id: str         # 候補ID（例: "css:#submit"）
    layer: str               # CDP / UIA / PIXEL
    selector_type: str       # css / xpath / uia / coords
    selector_value: str      # 実際のセレクタ
    priority: int = 0        # 静的優先度（高いほど優先）
    
    def __hash__(self):
        return hash(self.selector_id)


@dataclass
class BucketKey:
    """候補グループのキー"""
    screen_key: str   # 画面識別子
    intent: str       # 意図（例: "click_submit"）
    element_role: str # 要素役割（例: "button"）
    
    def __str__(self):
        return f"{self.screen_key}:{self.intent}:{self.element_role}"


class LocatorBank:
    """
    UCB候補選択システム
    
    UCBスコア計算（ChatGPT Rally 4）:
    score_i = μ̂_i + c√(ln N / n_i) - β * p_misclick,i
    
    c = 1.0（探索係数）
    β = 0.5（misclickペナルティ）
    """
    
    # UCBパラメータ
    C_EXPLORE = 1.0       # 探索係数
    BETA_MISCLICK = 0.5   # misclickペナルティ
    MIN_SAMPLES = 1       # UCB計算開始の最小試行数
    MISCLICK_THRESHOLD = 0.2  # この率以上は安全フィルタで除外
    
    def __init__(self, store: Optional[LearningStore] = None):
        self.store = store or LearningStore()
        self._candidates: Dict[str, List[LocatorCandidate]] = {}  # bucket_key -> candidates
    
    def register_candidate(
        self,
        bucket: BucketKey,
        candidate: LocatorCandidate
    ):
        """候補を登録"""
        key = str(bucket)
        if key not in self._candidates:
            self._candidates[key] = []
        
        # 重複チェック
        existing_ids = {c.selector_id for c in self._candidates[key]}
        if candidate.selector_id not in existing_ids:
            self._candidates[key].append(candidate)
            logger.debug(f"Registered: {key} -> {candidate.selector_id}")
    
    def register_candidates(
        self,
        bucket: BucketKey,
        candidates: List[LocatorCandidate]
    ):
        """複数候補を一括登録"""
        for c in candidates:
            self.register_candidate(bucket, c)
    
    def get_candidates(self, bucket: BucketKey) -> List[LocatorCandidate]:
        """バケット内の全候補を取得"""
        return self._candidates.get(str(bucket), [])
    
    def select_best(
        self,
        bucket: BucketKey,
        exclude_open: bool = True,
        safe_filter: bool = True
    ) -> Optional[LocatorCandidate]:
        """
        UCB+安全フィルタで最良候補を選択
        
        Args:
            bucket: 候補グループ
            exclude_open: CB OPEN候補を除外
            safe_filter: misclick率が高い候補を除外
        
        Returns:
            最良候補、なければNone
        """
        bucket_key = str(bucket)
        candidates = self._candidates.get(bucket_key, [])
        
        if not candidates:
            logger.warning(f"No candidates for {bucket_key}")
            return None
        
        # 1. 可用候補をフィルタ
        available = []
        for c in candidates:
            # CB OPEN除外
            if exclude_open and self.store.is_open(bucket_key, c.selector_id):
                logger.debug(f"Skip (OPEN): {c.selector_id}")
                continue
            
            # 安全フィルタ
            if safe_filter:
                stats = self.store.get_stats(bucket_key, c.selector_id)
                if stats and stats.n >= 10 and stats.misclick_rate > self.MISCLICK_THRESHOLD:
                    logger.debug(f"Skip (unsafe): {c.selector_id} misclick={stats.misclick_rate:.2f}")
                    continue
            
            available.append(c)
        
        if not available:
            logger.warning(f"All candidates filtered out for {bucket_key}")
            return None
        
        # 2. UCBスコア計算
        scores = {}
        total_n = sum(
            self.store.get_stats(bucket_key, c.selector_id).n
            if self.store.get_stats(bucket_key, c.selector_id) else 0
            for c in available
        )
        total_n = max(total_n, 1)
        
        for c in available:
            stats = self.store.get_stats(bucket_key, c.selector_id)
            
            if stats is None or stats.n < self.MIN_SAMPLES:
                # 未試行候補は必ず1回試す（強制探索）
                scores[c.selector_id] = float('inf')
            else:
                # UCBスコア: μ̂ + c√(ln N / n) - β * p_misclick
                mu = stats.mean_reward
                explore = self.C_EXPLORE * math.sqrt(math.log(total_n) / stats.n)
                penalty = self.BETA_MISCLICK * stats.misclick_rate
                
                scores[c.selector_id] = mu + explore - penalty
        
        # 3. 最高スコアの候補を選択
        best_id = max(scores, key=scores.get)
        best = next(c for c in available if c.selector_id == best_id)
        
        logger.debug(f"Selected: {best.selector_id} (score={scores[best_id]:.3f})")
        return best
    
    def update_result(
        self,
        bucket: BucketKey,
        selector_id: str,
        outcome: str  # success/misclick/not_found/timeout/state_mismatch
    ) -> float:
        """
        試行結果を記録
        
        Returns:
            報酬値
        """
        return self.store.record_outcome(str(bucket), selector_id, outcome)
    
    def get_layer_candidates(
        self,
        bucket: BucketKey,
        layer: str
    ) -> List[LocatorCandidate]:
        """指定レイヤーの候補のみ取得"""
        return [c for c in self.get_candidates(bucket) if c.layer == layer]
    
    def escalate_layer(
        self,
        bucket: BucketKey,
        current_layer: str
    ) -> Optional[str]:
        """
        レイヤーエスカレーション
        
        現レイヤーの候補が全滅 → 次レイヤーへ
        
        Returns:
            次レイヤー名、なければNone
        """
        layer_order = ["CDP", "UIA", "PIXEL", "VLM"]
        
        try:
            current_idx = layer_order.index(current_layer)
        except ValueError:
            return None
        
        # 現レイヤーで可用候補があるか
        available = [
            c for c in self.get_layer_candidates(bucket, current_layer)
            if not self.store.is_open(str(bucket), c.selector_id)
        ]
        
        if available:
            return None  # まだ使える
        
        # 次レイヤーへ
        for next_layer in layer_order[current_idx + 1:]:
            next_candidates = self.get_layer_candidates(bucket, next_layer)
            if next_candidates:
                logger.info(f"Layer escalation: {current_layer} -> {next_layer}")
                return next_layer
        
        return None


# テスト
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    
    print("=== Locator Bank テスト ===")
    
    store = LearningStore(Path("_temp/test_learning.db"))
    store.reset()
    
    bank = LocatorBank(store)
    
    bucket = BucketKey("vscode:main", "click_submit", "button")
    
    # 候補登録
    bank.register_candidates(bucket, [
        LocatorCandidate("css:#submit", "CDP", "css", "#submit"),
        LocatorCandidate("xpath://button[@id='submit']", "CDP", "xpath", "//button[@id='submit']"),
        LocatorCandidate("uia:name=Submit", "UIA", "uia", "name='Submit'"),
    ])
    
    # テスト1: 初回選択（未試行は強制探索）
    print("\n--- Test 1: First Selection ---")
    best = bank.select_best(bucket)
    print(f"Selected: {best.selector_id}")
    
    # テスト2: 結果記録後の選択
    print("\n--- Test 2: After Updates ---")
    bank.update_result(bucket, "css:#submit", "success")
    bank.update_result(bucket, "css:#submit", "success")
    bank.update_result(bucket, "xpath://button[@id='submit']", "misclick")
    
    for c in bank.get_candidates(bucket):
        stats = store.get_stats(str(bucket), c.selector_id)
        if stats:
            print(f"{c.selector_id}: n={stats.n}, mean={stats.mean_reward:.2f}")
    
    best = bank.select_best(bucket)
    print(f"Selected: {best.selector_id}")
    
    print("\n✅ テスト完了")
