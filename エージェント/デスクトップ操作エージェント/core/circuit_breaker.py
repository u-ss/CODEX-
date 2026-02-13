# circuit_breaker.py - 細粒度Circuit Breaker
# ChatGPT 5.2相談（ラリー3）に基づく実装

from __future__ import annotations
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
from typing import Any, Deque, Dict, Tuple
import time

from .failure_taxonomy import FailType


@dataclass(frozen=True)
class CBKey:
    """Circuit Breakerのキー（screen_key, action_kind, locator_version）"""
    screen_key: str
    action_kind: str
    locator_version: str


class CBState(str, Enum):
    """Circuit Breakerの状態"""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class Threshold:
    """開閉の閾値"""
    window_s: float = 60.0      # 監視ウィンドウ（秒）
    fail_count: int = 5         # ウィンドウ内失敗数
    consecutive_fail: int = 3   # 連続失敗数（0で無効）
    open_s: float = 30.0        # OPEN期間（秒）


# 失敗タイプ別のデフォルト閾値
DEFAULT_THRESHOLDS: Dict[FailType, Threshold] = {
    FailType.PERMISSION:    Threshold(window_s=300, fail_count=1, consecutive_fail=1, open_s=300),
    FailType.MODAL_DIALOG:  Threshold(window_s=120, fail_count=2, consecutive_fail=2, open_s=120),
    FailType.MISCLICK:      Threshold(window_s=120, fail_count=4, consecutive_fail=3, open_s=60),
    FailType.LOCATOR_STALE: Threshold(window_s=120, fail_count=4, consecutive_fail=3, open_s=60),
    FailType.UI_UPDATE:     Threshold(window_s=120, fail_count=4, consecutive_fail=3, open_s=60),
    FailType.NETWORK:       Threshold(window_s=60,  fail_count=5, consecutive_fail=0, open_s=30),
    FailType.TRANSIENT:     Threshold(window_s=60,  fail_count=6, consecutive_fail=0, open_s=20),
    FailType.WRONG_STATE:   Threshold(window_s=120, fail_count=4, consecutive_fail=2, open_s=60),
    FailType.UNKNOWN:       Threshold(window_s=60,  fail_count=6, consecutive_fail=0, open_s=20),
}


@dataclass
class CBRecord:
    """Circuit Breaker記録"""
    state: CBState = CBState.CLOSED
    opened_until: float = 0.0
    history: Deque[Tuple[float, FailType, bool]] = field(default_factory=lambda: deque(maxlen=200))
    consecutive_fail: int = 0


class CircuitBreaker:
    """
    細粒度Circuit Breaker
    
    特徴:
    - (screen_key, action_kind, locator_version) 単位での遮断
    - 失敗タイプ別の閾値
    - 連続失敗とウィンドウ内失敗数の両方で判定
    """
    
    def __init__(self, thresholds: Dict[FailType, Threshold] = None):
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self._m: Dict[CBKey, CBRecord] = {}
    
    def allow(self, key: CBKey) -> bool:
        """アクション実行を許可するか"""
        rec = self._m.get(key)
        if not rec:
            return True
        
        now = time.time()
        if rec.state == CBState.OPEN:
            if now >= rec.opened_until:
                rec.state = CBState.HALF_OPEN
                return True
            return False
        
        return True  # CLOSED / HALF_OPEN は試行OK
    
    def record(self, key: CBKey, ok: bool, fail_type: FailType) -> None:
        """結果を記録"""
        rec = self._m.setdefault(key, CBRecord())
        now = time.time()
        rec.history.append((now, fail_type, ok))
        
        if ok:
            rec.consecutive_fail = 0
            # HALF_OPEN で成功したら CLOSED に戻す
            if rec.state == CBState.HALF_OPEN:
                rec.state = CBState.CLOSED
                rec.opened_until = 0.0
            return
        
        rec.consecutive_fail += 1
        th = self.thresholds.get(fail_type, self.thresholds[FailType.UNKNOWN])
        
        # ウィンドウ内失敗数
        win_start = now - th.window_s
        fail_in_window = sum(
            1 for ts, ft, is_ok in rec.history
            if (ts >= win_start and (not is_ok) and ft == fail_type)
        )
        
        # OPEN 判定
        consec_trigger = (th.consecutive_fail > 0 and rec.consecutive_fail >= th.consecutive_fail)
        window_trigger = (fail_in_window >= th.fail_count)
        
        if consec_trigger or window_trigger:
            rec.state = CBState.OPEN
            rec.opened_until = now + th.open_s
    
    def snapshot(self, key: CBKey) -> Dict[str, Any]:
        """現在の状態を取得"""
        rec = self._m.get(key)
        if not rec:
            return {"state": "NONE"}
        return {
            "state": rec.state.value,
            "opened_until": rec.opened_until,
            "consecutive_fail": rec.consecutive_fail,
        }
    
    def reset(self, key: CBKey) -> None:
        """手動リセット"""
        if key in self._m:
            del self._m[key]
    
    def reset_all(self) -> None:
        """全てリセット"""
        self._m.clear()
