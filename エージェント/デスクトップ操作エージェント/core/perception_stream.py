"""
Perception Stream（知覚ストリーム）

目的: 状況把握をリアルタイムで行う

ChatGPT 5.2フィードバック（2026-02-05 Round5）より:
「リアルタイム化の要点は『常時フル観測』ではなく、イベント駆動で差分だけ更新すること」

設計:
- Perception Bus: 各層が「変化」を検知したらObservation Deltaを投げる
- Watcher: Layer2+/Layer3/Layer1の変化検知
- Incremental State Update: Deltaを受けてbelief/confidenceを更新
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any, Callable
from queue import Queue
import threading
import time


class DeltaType(Enum):
    """変化タイプ"""
    URL_CHANGE = "url_change"
    DOM_MUTATION = "dom_mutation"
    NAVIGATION = "navigation"
    NETWORK_IDLE = "network_idle"
    DIALOG_OPEN = "dialog_open"
    DIALOG_CLOSE = "dialog_close"
    FOCUS_CHANGE = "focus_change"
    WINDOW_FOREGROUND = "window_foreground"
    VALUE_CHANGE = "value_change"
    MODAL_APPEAR = "modal_appear"
    SCREEN_DIFF = "screen_diff"
    ERROR = "error"


class ObservationLayer(Enum):
    """観測レイヤー"""
    CDP = "cdp"       # Layer2+ (Playwright/CDP)
    UIA = "uia"       # Layer3 (Pywinauto)
    SS = "ss"         # Layer1 (Screenshot)


@dataclass
class ObservationDelta:
    """観測差分"""
    delta_type: DeltaType
    layer: ObservationLayer
    old_value: Any
    new_value: Any
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "type": self.delta_type.value,
            "layer": self.layer.value,
            "old": str(self.old_value)[:50],
            "new": str(self.new_value)[:50],
            "timestamp": self.timestamp,
            "confidence": self.confidence,
        }


class DeltaSubscriber:
    """Delta購読者インターフェース"""
    
    def on_delta(self, delta: ObservationDelta) -> None:
        raise NotImplementedError


class PerceptionBus:
    """知覚バス（Deltaの配信）"""
    
    def __init__(self, max_queue_size: int = 100):
        self.subscribers: list[DeltaSubscriber] = []
        self.delta_queue: Queue = Queue(maxsize=max_queue_size)
        self.delta_history: list[ObservationDelta] = []
        self.max_history = 200
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def subscribe(self, subscriber: DeltaSubscriber) -> None:
        """購読者を登録"""
        self.subscribers.append(subscriber)
    
    def unsubscribe(self, subscriber: DeltaSubscriber) -> None:
        """購読解除"""
        if subscriber in self.subscribers:
            self.subscribers.remove(subscriber)
    
    def publish(self, delta: ObservationDelta) -> None:
        """Deltaを発行"""
        # 履歴に追加
        self.delta_history.append(delta)
        if len(self.delta_history) > self.max_history:
            self.delta_history = self.delta_history[-self.max_history:]
        
        # 購読者に通知
        for sub in self.subscribers:
            try:
                sub.on_delta(delta)
            except Exception as e:
                print(f"Subscriber error: {e}")
    
    def get_recent_deltas(self, n: int = 10) -> list[ObservationDelta]:
        """最近のDeltaを取得"""
        return self.delta_history[-n:]
    
    def get_deltas_by_type(self, delta_type: DeltaType) -> list[ObservationDelta]:
        """タイプ別にDeltaを取得"""
        return [d for d in self.delta_history if d.delta_type == delta_type]


class CDPWatcher:
    """CDP (Layer2+) 変化監視"""
    
    def __init__(self, bus: PerceptionBus):
        self.bus = bus
        self.last_url: Optional[str] = None
        self.last_title: Optional[str] = None
        self.watching = False
    
    def check_changes(self, page: Any) -> list[ObservationDelta]:
        """変化をチェック"""
        deltas = []
        
        try:
            # URL変化
            current_url = page.url
            if self.last_url and current_url != self.last_url:
                delta = ObservationDelta(
                    delta_type=DeltaType.URL_CHANGE,
                    layer=ObservationLayer.CDP,
                    old_value=self.last_url,
                    new_value=current_url
                )
                deltas.append(delta)
                self.bus.publish(delta)
            self.last_url = current_url
            
            # タイトル変化
            current_title = page.title()
            if self.last_title and current_title != self.last_title:
                delta = ObservationDelta(
                    delta_type=DeltaType.NAVIGATION,
                    layer=ObservationLayer.CDP,
                    old_value=self.last_title,
                    new_value=current_title
                )
                deltas.append(delta)
                self.bus.publish(delta)
            self.last_title = current_title
            
            # ダイアログチェック
            dialog = page.query_selector("[role='dialog']")
            if dialog and dialog.is_visible():
                delta = ObservationDelta(
                    delta_type=DeltaType.DIALOG_OPEN,
                    layer=ObservationLayer.CDP,
                    old_value=None,
                    new_value="dialog"
                )
                deltas.append(delta)
                self.bus.publish(delta)
                
        except Exception as e:
            delta = ObservationDelta(
                delta_type=DeltaType.ERROR,
                layer=ObservationLayer.CDP,
                old_value=None,
                new_value=str(e)
            )
            deltas.append(delta)
            self.bus.publish(delta)
        
        return deltas


class UIAWatcher:
    """UIA (Layer3) 変化監視"""
    
    def __init__(self, bus: PerceptionBus):
        self.bus = bus
        self.last_foreground: Optional[str] = None
        self.last_focus: Optional[str] = None
    
    def check_changes(self) -> list[ObservationDelta]:
        """変化をチェック"""
        deltas = []
        
        try:
            from pywinauto import Desktop
            import win32gui
            
            # フォアグラウンドウィンドウ
            fg_hwnd = win32gui.GetForegroundWindow()
            fg_title = win32gui.GetWindowText(fg_hwnd)
            
            if self.last_foreground and fg_title != self.last_foreground:
                delta = ObservationDelta(
                    delta_type=DeltaType.WINDOW_FOREGROUND,
                    layer=ObservationLayer.UIA,
                    old_value=self.last_foreground,
                    new_value=fg_title
                )
                deltas.append(delta)
                self.bus.publish(delta)
            self.last_foreground = fg_title
            
        except Exception as e:
            delta = ObservationDelta(
                delta_type=DeltaType.ERROR,
                layer=ObservationLayer.UIA,
                old_value=None,
                new_value=str(e),
                confidence=0.5
            )
            deltas.append(delta)
            self.bus.publish(delta)
        
        return deltas


class TransitionTracker(DeltaSubscriber):
    """遷移追跡（何が変わったかを要約）"""
    
    def __init__(self):
        self.transitions: list[dict] = []
        self.max_transitions = 50
    
    def on_delta(self, delta: ObservationDelta) -> None:
        """Deltaを受けて遷移を記録"""
        transition = {
            "type": delta.delta_type.value,
            "layer": delta.layer.value,
            "from": str(delta.old_value)[:30] if delta.old_value else None,
            "to": str(delta.new_value)[:30] if delta.new_value else None,
            "timestamp": delta.timestamp,
        }
        
        self.transitions.append(transition)
        if len(self.transitions) > self.max_transitions:
            self.transitions = self.transitions[-self.max_transitions:]
    
    def get_summary(self) -> list[str]:
        """変化要約を取得"""
        summary = []
        for t in self.transitions[-10:]:
            if t["from"] and t["to"]:
                summary.append(f"{t['type']}: {t['from']} → {t['to']}")
            elif t["to"]:
                summary.append(f"{t['type']}: {t['to']}")
        return summary


class PerceptionStream:
    """知覚ストリーム統合"""
    
    def __init__(self):
        self.bus = PerceptionBus()
        self.cdp_watcher = CDPWatcher(self.bus)
        self.uia_watcher = UIAWatcher(self.bus)
        self.transition_tracker = TransitionTracker()
        
        # 遷移追跡を購読
        self.bus.subscribe(self.transition_tracker)
    
    def observe(self, page: Any = None) -> dict:
        """全レイヤーを観測"""
        result = {
            "cdp_deltas": [],
            "uia_deltas": [],
            "transitions": [],
        }
        
        if page:
            result["cdp_deltas"] = self.cdp_watcher.check_changes(page)
        
        result["uia_deltas"] = self.uia_watcher.check_changes()
        result["transitions"] = self.transition_tracker.get_summary()
        
        return result
    
    def get_recent_changes(self, n: int = 5) -> list[str]:
        """最近の変化を取得"""
        return self.transition_tracker.get_summary()[-n:]
    
    def format_status(self) -> str:
        """ステータスをフォーマット"""
        recent = self.bus.get_recent_deltas(5)
        
        lines = ["Perception Stream Status:"]
        lines.append(f"  Total deltas: {len(self.bus.delta_history)}")
        lines.append(f"  Recent changes:")
        
        for delta in recent:
            icon = {
                DeltaType.URL_CHANGE: "🔗",
                DeltaType.NAVIGATION: "📍",
                DeltaType.DIALOG_OPEN: "📋",
                DeltaType.DIALOG_CLOSE: "✅",
                DeltaType.FOCUS_CHANGE: "👁️",
                DeltaType.WINDOW_FOREGROUND: "🪟",
                DeltaType.ERROR: "❌",
            }.get(delta.delta_type, "📌")
            
            lines.append(f"    {icon} {delta.delta_type.value}: {str(delta.new_value)[:40]}")
        
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("Perception Stream テスト")
    print("=" * 60)
    
    stream = PerceptionStream()
    
    # 手動でDeltaを発行（モック）
    print("\n--- Delta発行テスト ---")
    
    stream.bus.publish(ObservationDelta(
        delta_type=DeltaType.URL_CHANGE,
        layer=ObservationLayer.CDP,
        old_value="https://example.com/page1",
        new_value="https://example.com/page2"
    ))
    
    stream.bus.publish(ObservationDelta(
        delta_type=DeltaType.DIALOG_OPEN,
        layer=ObservationLayer.CDP,
        old_value=None,
        new_value="確認ダイアログ"
    ))
    
    stream.bus.publish(ObservationDelta(
        delta_type=DeltaType.WINDOW_FOREGROUND,
        layer=ObservationLayer.UIA,
        old_value="VS Code",
        new_value="Brave"
    ))
    
    print(stream.format_status())
    
    print("\n--- 遷移要約 ---")
    for t in stream.get_recent_changes():
        print(f"  {t}")
    
    print("\n" + "=" * 60)
    print("テスト完了")
