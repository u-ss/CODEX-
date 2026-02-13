"""
Event Driven Wait - イベント駆動待機

ChatGPT相談（Rally 4）で設計したイベント駆動待機を実装

固定スリープ廃止、短周期チェックで状態変化を検出:
- UIA要素の出現/有効化
- ウィンドウタイトル変化
- 画面差分が一定以上
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, Any, List
from datetime import datetime, timedelta
from enum import Enum, auto
import time
import logging

logger = logging.getLogger(__name__)


class WaitResult(Enum):
    """待機結果"""
    SUCCESS = auto()      # 期待した変化を検出
    TIMEOUT = auto()      # タイムアウト
    CANCELLED = auto()    # キャンセル
    ERROR = auto()        # エラー発生


@dataclass
class WaitCondition:
    """待機条件"""
    name: str
    checker: Callable[[], bool]
    description: str = ""
    priority: int = 0  # 高いほど優先


@dataclass
class WaitConfig:
    """待機設定"""
    poll_interval_ms: int = 200     # ポーリング間隔（ミリ秒）
    timeout_seconds: float = 60.0   # タイムアウト（秒）
    min_stable_checks: int = 2      # 安定確認回数
    cpu_throttle_ms: int = 50       # CPU負荷軽減のための最小スリープ


@dataclass
class WaitEvent:
    """待機イベント"""
    condition_name: str
    detected_at: datetime
    elapsed_seconds: float
    check_count: int
    stable_count: int


class EventDrivenWaiter:
    """イベント駆動待機"""
    
    def __init__(self, config: Optional[WaitConfig] = None):
        self.config = config or WaitConfig()
        self._cancel_requested = False
        self._events: List[WaitEvent] = []
    
    def wait_for_any(
        self,
        conditions: List[WaitCondition],
        timeout: Optional[float] = None
    ) -> tuple[WaitResult, Optional[WaitCondition]]:
        """
        いずれかの条件が満たされるまで待機
        
        Returns:
            (結果, 満たされた条件)
        """
        timeout = timeout or self.config.timeout_seconds
        start = datetime.now()
        deadline = start + timedelta(seconds=timeout)
        
        check_count = 0
        stable_counts = {c.name: 0 for c in conditions}
        
        logger.info(f"Wait started: {len(conditions)} conditions, timeout={timeout}s")
        
        while datetime.now() < deadline:
            if self._cancel_requested:
                logger.info("Wait cancelled")
                return (WaitResult.CANCELLED, None)
            
            check_count += 1
            
            # 優先度順にソート
            sorted_conditions = sorted(conditions, key=lambda c: -c.priority)
            
            for condition in sorted_conditions:
                try:
                    if condition.checker():
                        stable_counts[condition.name] += 1
                        
                        # 安定確認
                        if stable_counts[condition.name] >= self.config.min_stable_checks:
                            elapsed = (datetime.now() - start).total_seconds()
                            event = WaitEvent(
                                condition_name=condition.name,
                                detected_at=datetime.now(),
                                elapsed_seconds=elapsed,
                                check_count=check_count,
                                stable_count=stable_counts[condition.name]
                            )
                            self._events.append(event)
                            logger.info(
                                f"Condition met: {condition.name} "
                                f"(elapsed={elapsed:.2f}s, checks={check_count})"
                            )
                            return (WaitResult.SUCCESS, condition)
                    else:
                        # 条件が満たされなくなったらリセット
                        stable_counts[condition.name] = 0
                        
                except Exception as e:
                    logger.warning(f"Condition check error: {condition.name}: {e}")
            
            # ポーリング間隔待機（CPU負荷軽減）
            sleep_ms = max(self.config.poll_interval_ms, self.config.cpu_throttle_ms)
            time.sleep(sleep_ms / 1000)
        
        elapsed = (datetime.now() - start).total_seconds()
        logger.warning(f"Wait timeout: {elapsed:.2f}s, checks={check_count}")
        return (WaitResult.TIMEOUT, None)
    
    def wait_for_all(
        self,
        conditions: List[WaitCondition],
        timeout: Optional[float] = None
    ) -> tuple[WaitResult, List[WaitCondition]]:
        """
        全ての条件が満たされるまで待機
        
        Returns:
            (結果, 満たされた条件のリスト)
        """
        timeout = timeout or self.config.timeout_seconds
        start = datetime.now()
        deadline = start + timedelta(seconds=timeout)
        
        met_conditions = set()
        stable_counts = {c.name: 0 for c in conditions}
        
        logger.info(f"Wait ALL started: {len(conditions)} conditions")
        
        while datetime.now() < deadline:
            if self._cancel_requested:
                return (WaitResult.CANCELLED, [])
            
            for condition in conditions:
                if condition.name in met_conditions:
                    continue
                    
                try:
                    if condition.checker():
                        stable_counts[condition.name] += 1
                        if stable_counts[condition.name] >= self.config.min_stable_checks:
                            met_conditions.add(condition.name)
                            logger.info(f"Condition met: {condition.name}")
                    else:
                        stable_counts[condition.name] = 0
                except Exception as e:
                    logger.warning(f"Condition check error: {condition.name}: {e}")
            
            if len(met_conditions) == len(conditions):
                met = [c for c in conditions if c.name in met_conditions]
                elapsed = (datetime.now() - start).total_seconds()
                logger.info(f"All conditions met: elapsed={elapsed:.2f}s")
                return (WaitResult.SUCCESS, met)
            
            time.sleep(self.config.poll_interval_ms / 1000)
        
        met = [c for c in conditions if c.name in met_conditions]
        return (WaitResult.TIMEOUT, met)
    
    def cancel(self):
        """待機をキャンセル"""
        self._cancel_requested = True
    
    def reset(self):
        """リセット"""
        self._cancel_requested = False
    
    def get_events(self) -> List[WaitEvent]:
        """イベント履歴取得"""
        return self._events.copy()


# 便利なConditionファクトリ
class ConditionFactory:
    """よく使う待機条件のファクトリ"""
    
    @staticmethod
    def uia_element_exists(
        finder: Callable[[], Any],
        name: str = "UIA Element Exists"
    ) -> WaitCondition:
        """UIA要素が存在するか"""
        def checker():
            try:
                element = finder()
                return element is not None
            except:
                return False
        return WaitCondition(name=name, checker=checker)
    
    @staticmethod
    def uia_element_enabled(
        finder: Callable[[], Any],
        name: str = "UIA Element Enabled"
    ) -> WaitCondition:
        """UIA要素が有効か"""
        def checker():
            try:
                element = finder()
                return element is not None and element.is_enabled()
            except:
                return False
        return WaitCondition(name=name, checker=checker)
    
    @staticmethod
    def window_title_contains(
        getter: Callable[[], str],
        expected: str,
        name: str = "Window Title"
    ) -> WaitCondition:
        """ウィンドウタイトルが指定文字列を含むか"""
        def checker():
            try:
                title = getter()
                return expected in title
            except:
                return False
        return WaitCondition(name=name, checker=checker, description=f"contains '{expected}'")
    
    @staticmethod
    def screen_diff_above(
        differ: Callable[[], float],
        threshold: float = 10.0,
        name: str = "Screen Diff"
    ) -> WaitCondition:
        """画面差分が閾値以上か"""
        def checker():
            try:
                diff = differ()
                return diff >= threshold
            except:
                return False
        return WaitCondition(name=name, checker=checker, description=f">={threshold}%")
    
    @staticmethod
    def screen_diff_below(
        differ: Callable[[], float],
        threshold: float = 5.0,
        name: str = "Screen Stable"
    ) -> WaitCondition:
        """画面差分が閾値以下か（画面が安定したか）"""
        def checker():
            try:
                diff = differ()
                return diff <= threshold
            except:
                return False
        return WaitCondition(name=name, checker=checker, description=f"<={threshold}%")
    
    @staticmethod
    def custom(
        checker: Callable[[], bool],
        name: str,
        description: str = ""
    ) -> WaitCondition:
        """カスタム条件"""
        return WaitCondition(name=name, checker=checker, description=description)


# 使用例
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=== Event Driven Wait テスト ===")
    
    # テスト用のカウンター（3回目で条件満たす）
    counter = [0]
    
    def mock_checker():
        counter[0] += 1
        return counter[0] >= 3
    
    # 待機設定
    config = WaitConfig(
        poll_interval_ms=100,
        timeout_seconds=5.0,
        min_stable_checks=2
    )
    
    waiter = EventDrivenWaiter(config)
    
    # 条件作成
    conditions = [
        WaitCondition(name="test_condition", checker=mock_checker)
    ]
    
    # 待機実行
    result, met = waiter.wait_for_any(conditions, timeout=3.0)
    print(f"Result: {result.name}")
    print(f"Met condition: {met.name if met else None}")
    print(f"Counter: {counter[0]}")
    
    # イベント履歴
    for event in waiter.get_events():
        print(f"Event: {event.condition_name}, elapsed={event.elapsed_seconds:.2f}s")
    
    print("\n✅ テスト完了")
