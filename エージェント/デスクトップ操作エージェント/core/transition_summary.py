"""
Transition Summary（遷移サマリ）

目的: "何が起きたか"の短い説明を生成（人にも機械にも効く）

ChatGPT 5.2フィードバック（2026-02-05 Round6）より:
「ほしい出力（毎ステップ）: transition_summary / confidence_delta」

設計:
- URL変化/モーダル出現/フォーカス移動/主要要素の消失...の上位数件
- 確度が上がった根拠/下がった矛盾
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any


class TransitionType(Enum):
    """遷移タイプ"""
    URL_CHANGE = "URL変化"
    MODAL_APPEAR = "モーダル出現"
    MODAL_CLOSE = "モーダル消失"
    FOCUS_MOVE = "フォーカス移動"
    ELEMENT_APPEAR = "要素出現"
    ELEMENT_DISAPPEAR = "要素消失"
    VALUE_CHANGE = "値変化"
    STATE_CHANGE = "状態変化"
    NAVIGATION = "ナビゲーション"
    LOADING_START = "読み込み開始"
    LOADING_END = "読み込み完了"
    ERROR = "エラー発生"


@dataclass
class TransitionEvent:
    """遷移イベント"""
    transition_type: TransitionType
    from_value: Optional[str]
    to_value: Optional[str]
    importance: float          # 0.0-1.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def format(self) -> str:
        """フォーマット"""
        if self.from_value and self.to_value:
            return f"{self.transition_type.value}: {self.from_value[:20]}→{self.to_value[:20]}"
        elif self.to_value:
            return f"{self.transition_type.value}: {self.to_value[:40]}"
        else:
            return self.transition_type.value


@dataclass
class ConfidenceDelta:
    """確度の変化"""
    prev_confidence: float
    curr_confidence: float
    reason: str
    supporting_evidence: list[str] = field(default_factory=list)
    conflicting_evidence: list[str] = field(default_factory=list)
    
    @property
    def delta(self) -> float:
        return self.curr_confidence - self.prev_confidence
    
    def format(self) -> str:
        direction = "↑" if self.delta > 0 else "↓" if self.delta < 0 else "→"
        return f"確度{direction} {self.prev_confidence:.0%}→{self.curr_confidence:.0%} ({self.reason})"


@dataclass
class TransitionSummary:
    """遷移サマリ"""
    events: list[TransitionEvent] = field(default_factory=list)
    confidence_delta: Optional[ConfidenceDelta] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def add_event(self, event: TransitionEvent) -> None:
        self.events.append(event)
        self.events.sort(key=lambda e: e.importance, reverse=True)
    
    def get_top_events(self, n: int = 3) -> list[TransitionEvent]:
        """重要度上位n件"""
        return self.events[:n]
    
    def format(self) -> str:
        """サマリをフォーマット"""
        lines = []
        
        if not self.events:
            lines.append("変化なし")
        else:
            lines.append(f"変化({len(self.events)}件):")
            for event in self.get_top_events(3):
                lines.append(f"  • {event.format()}")
        
        if self.confidence_delta:
            lines.append(self.confidence_delta.format())
        
        return "\n".join(lines)
    
    def to_oneliner(self) -> str:
        """1行サマリ"""
        if not self.events:
            return "変化なし"
        
        parts = [e.format() for e in self.get_top_events(2)]
        return " / ".join(parts)


class TransitionAnalyzer:
    """遷移分析器"""
    
    def __init__(self):
        self.prev_state: dict = {}
        self.history: list[TransitionSummary] = []
        self.max_history = 50
    
    def analyze(self, current_state: dict) -> TransitionSummary:
        """状態を分析して遷移サマリを生成"""
        summary = TransitionSummary()
        
        # 各キーをチェック
        for key, curr_value in current_state.items():
            prev_value = self.prev_state.get(key)
            
            if prev_value is None:
                continue  # 初回は無視
            
            if str(prev_value) != str(curr_value):
                event = self._detect_transition(key, prev_value, curr_value)
                if event:
                    summary.add_event(event)
        
        # 確度の変化
        prev_conf = self.prev_state.get("confidence", 0.5)
        curr_conf = current_state.get("confidence", 0.5)
        
        if abs(curr_conf - prev_conf) > 0.05:
            summary.confidence_delta = ConfidenceDelta(
                prev_confidence=prev_conf,
                curr_confidence=curr_conf,
                reason=self._explain_confidence_change(prev_conf, curr_conf, current_state)
            )
        
        # 状態を更新
        self.prev_state = current_state.copy()
        
        # 履歴に追加
        self.history.append(summary)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        
        return summary
    
    def _detect_transition(self, key: str, prev: Any, curr: Any) -> Optional[TransitionEvent]:
        """遷移を検出"""
        # URL
        if key == "url":
            return TransitionEvent(
                transition_type=TransitionType.URL_CHANGE,
                from_value=str(prev),
                to_value=str(curr),
                importance=0.9
            )
        
        # モーダル
        if key == "modal_visible":
            if curr and not prev:
                return TransitionEvent(
                    transition_type=TransitionType.MODAL_APPEAR,
                    from_value=None,
                    to_value="モーダル",
                    importance=0.95
                )
            elif not curr and prev:
                return TransitionEvent(
                    transition_type=TransitionType.MODAL_CLOSE,
                    from_value="モーダル",
                    to_value=None,
                    importance=0.8
                )
        
        # フォーカス
        if key == "focus" or key == "active_element":
            return TransitionEvent(
                transition_type=TransitionType.FOCUS_MOVE,
                from_value=str(prev),
                to_value=str(curr),
                importance=0.6
            )
        
        # 読み込み
        if key == "loading":
            if curr and not prev:
                return TransitionEvent(
                    transition_type=TransitionType.LOADING_START,
                    from_value=None,
                    to_value="読み込み中",
                    importance=0.7
                )
            elif not curr and prev:
                return TransitionEvent(
                    transition_type=TransitionType.LOADING_END,
                    from_value="読み込み中",
                    to_value=None,
                    importance=0.7
                )
        
        # 状態
        if key == "state" or key == "category":
            return TransitionEvent(
                transition_type=TransitionType.STATE_CHANGE,
                from_value=str(prev),
                to_value=str(curr),
                importance=0.8
            )
        
        # その他の値変化
        return TransitionEvent(
            transition_type=TransitionType.VALUE_CHANGE,
            from_value=str(prev)[:20],
            to_value=str(curr)[:20],
            importance=0.3
        )
    
    def _explain_confidence_change(self, prev: float, curr: float, state: dict) -> str:
        """確度変化の理由を説明"""
        if curr > prev:
            # 上昇
            if state.get("modal_visible"):
                return "モーダル検出で確度上昇"
            elif state.get("url"):
                return "URLマッチで確度上昇"
            else:
                return "観測一致で確度上昇"
        else:
            # 低下
            if state.get("conflicts"):
                return "観測矛盾で確度低下"
            elif state.get("stale"):
                return "証拠が古くなり確度低下"
            else:
                return "状態不一致で確度低下"
    
    def get_recent_changes(self, n: int = 5) -> list[str]:
        """最近の変化を取得"""
        recent = []
        for summary in self.history[-n:]:
            for event in summary.get_top_events(1):
                recent.append(event.format())
        return recent
    
    def format_timeline(self, n: int = 10) -> str:
        """タイムラインをフォーマット"""
        lines = ["Transition Timeline:"]
        
        for i, summary in enumerate(self.history[-n:], 1):
            oneliner = summary.to_oneliner()
            lines.append(f"  {i}. {oneliner}")
        
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("Transition Summary テスト")
    print("=" * 60)
    
    analyzer = TransitionAnalyzer()
    
    # 初期状態
    state1 = {
        "url": "https://chatgpt.com/",
        "modal_visible": False,
        "loading": False,
        "confidence": 0.7,
    }
    summary1 = analyzer.analyze(state1)
    print("\n--- 状態1（初期） ---")
    print(summary1.format())
    
    # URL変化
    state2 = {
        "url": "https://chatgpt.com/c/abc123",
        "modal_visible": False,
        "loading": True,
        "confidence": 0.6,
    }
    summary2 = analyzer.analyze(state2)
    print("\n--- 状態2（URL変化+読み込み開始） ---")
    print(summary2.format())
    
    # モーダル出現
    state3 = {
        "url": "https://chatgpt.com/c/abc123",
        "modal_visible": True,
        "loading": False,
        "confidence": 0.9,
    }
    summary3 = analyzer.analyze(state3)
    print("\n--- 状態3（モーダル出現） ---")
    print(summary3.format())
    
    # タイムライン
    print("\n--- タイムライン ---")
    print(analyzer.format_timeline())
    
    print("\n" + "=" * 60)
    print("テスト完了")
