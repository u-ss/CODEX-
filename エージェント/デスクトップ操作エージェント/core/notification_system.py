"""
Notification System - 通知設計

ChatGPT相談（Rally 9）で設計した通知システムを実装:
- 通知タイプ: INFO, PROGRESS, WARNING, ERROR, ACTION_REQUIRED
- マージ: 連続同タイプはマージ
- チャンネル: Toast, Panel, Log, Sound
- テンプレート: 構造化JSON対応
"""

import json
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """通知タイプ"""
    INFO = "info"                     # 情報（進行状況）
    PROGRESS = "progress"             # 進捗更新
    WARNING = "warn"                  # 警告（フォールバック等）
    ERROR = "error"                   # エラー（失敗確定）
    ACTION_REQUIRED = "action"        # ユーザー介入必須（Ask）


class NotificationChannel(Enum):
    """通知チャンネル"""
    TOAST = "toast"       # ポップアップ通知
    PANEL = "panel"       # 常駐パネル
    LOG = "log"           # ログのみ
    SOUND = "sound"       # 音声通知


@dataclass
class NotificationConfig:
    """通知設定"""
    merge_window_seconds: int = 5        # マージウィンドウ
    max_queue_size: int = 100            # 最大キューサイズ
    auto_dismiss_seconds: int = 10       # 自動消去（INFO/PROGRESS）
    sound_enabled: bool = True           # 音声通知有効
    default_channels: List[NotificationChannel] = field(
        default_factory=lambda: [NotificationChannel.TOAST, NotificationChannel.LOG]
    )


@dataclass
class Notification:
    """通知エントリ"""
    type: NotificationType
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    channels: List[NotificationChannel] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    action_options: List[str] = field(default_factory=list)  # ACTION_REQUIRED用
    merge_count: int = 1
    dismissed: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "channels": [c.value for c in self.channels],
            "context": self.context,
            "action_options": self.action_options,
            "merge_count": self.merge_count
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# 通知テンプレート（Rally 9より）
class NotificationTemplates:
    """通知テンプレート集"""
    
    @staticmethod
    def circuit_breaker_open(
        app_name: str,
        screen_key: str,
        action_type: str,
        failures: int,
        retry_after: float,
        observation: Dict[str, Any]
    ) -> Notification:
        """サーキットブレーカーOPEN通知"""
        return Notification(
            type=NotificationType.ACTION_REQUIRED,
            title=f"⚠️ Circuit Breaker OPEN",
            message=f"{app_name} / {screen_key}: {action_type}が{failures}回連続失敗",
            context={
                "app_name": app_name,
                "screen_key": screen_key,
                "action_type": action_type,
                "failures": failures,
                "retry_after_sec": retry_after,
                "observation": observation
            },
            action_options=[
                "手段切替（UIA→DOM）",
                "手動で実行",
                "中断"
            ]
        )
    
    @staticmethod
    def fallback_triggered(
        from_method: str,
        to_method: str,
        target: str,
        reason: str
    ) -> Notification:
        """フォールバック発動通知"""
        return Notification(
            type=NotificationType.WARNING,
            title="📂 Fallback Triggered",
            message=f"{from_method} → {to_method}: {reason}",
            context={
                "from_method": from_method,
                "to_method": to_method,
                "target": target,
                "reason": reason
            }
        )
    
    @staticmethod
    def unknown_app_detected(
        process_name: str,
        window_title: str
    ) -> Notification:
        """未知アプリ検知通知"""
        return Notification(
            type=NotificationType.WARNING,
            title="🔍 Unknown App Detected",
            message=f"{process_name}: 調査モードに移行",
            context={
                "process_name": process_name,
                "window_title": window_title
            }
        )
    
    @staticmethod
    def ask_approval(
        goal: str,
        action: str,
        target: str,
        risk: str,
        evidence: str,
        expected_result: str
    ) -> Notification:
        """承認要求通知（Askカード）"""
        return Notification(
            type=NotificationType.ACTION_REQUIRED,
            title="🤔 承認が必要です",
            message=f"{goal}",
            context={
                "action": action,
                "target": target,
                "risk": risk,
                "evidence": evidence,
                "expected_result": expected_result
            },
            action_options=[
                "✅ 承認(1回)",
                "✋ 拒否",
                "🧩 手動で実行",
                "🛑 中断"
            ]
        )
    
    @staticmethod
    def action_success(
        action: str,
        target: str,
        elapsed_ms: int
    ) -> Notification:
        """アクション成功通知"""
        return Notification(
            type=NotificationType.INFO,
            title="✅ 成功",
            message=f"{action}: {target} ({elapsed_ms}ms)",
            context={
                "action": action,
                "target": target,
                "elapsed_ms": elapsed_ms
            }
        )
    
    @staticmethod
    def all_methods_failed(
        goal: str,
        tried_methods: List[str],
        last_error: str
    ) -> Notification:
        """全手段失敗通知"""
        return Notification(
            type=NotificationType.ERROR,
            title="❌ 自動解決不能",
            message=f"{goal}: 全手段({', '.join(tried_methods)})が失敗",
            context={
                "goal": goal,
                "tried_methods": tried_methods,
                "last_error": last_error
            },
            action_options=[
                "手動で実行して「続行」",
                "調査だけ続行（UIA/SS収集）",
                "中断してログ保存"
            ]
        )


class NotificationManager:
    """通知マネージャー"""
    
    def __init__(self, config: Optional[NotificationConfig] = None):
        self.config = config or NotificationConfig()
        self._queue: List[Notification] = []
        self._handlers: Dict[NotificationChannel, Callable[[Notification], None]] = {}
        self._pending_action: Optional[Notification] = None
    
    def register_handler(
        self,
        channel: NotificationChannel,
        handler: Callable[[Notification], None]
    ):
        """チャンネルハンドラ登録"""
        self._handlers[channel] = handler
    
    def send(self, notification: Notification):
        """通知送信"""
        # チャンネルがなければデフォルト
        if not notification.channels:
            notification.channels = self.config.default_channels.copy()
        
        # マージチェック
        merged = self._try_merge(notification)
        
        if not merged:
            self._queue.append(notification)
            if len(self._queue) > self.config.max_queue_size:
                self._queue.pop(0)  # FIFO
        
        # ハンドラ呼び出し
        target = merged or notification
        for channel in target.channels:
            if channel in self._handlers:
                try:
                    self._handlers[channel](target)
                except Exception as e:
                    logger.warning(f"Handler error ({channel.value}): {e}")
        
        # ACTION_REQUIREDの場合は保持
        if notification.type == NotificationType.ACTION_REQUIRED:
            self._pending_action = notification
        
        # ログ出力
        self._log_notification(target)
    
    def _try_merge(self, notification: Notification) -> Optional[Notification]:
        """マージ試行"""
        if not self._queue:
            return None
        
        window = timedelta(seconds=self.config.merge_window_seconds)
        
        for existing in reversed(self._queue):
            if existing.dismissed:
                continue
            
            # 同タイプ・同タイトルでウィンドウ内
            if (existing.type == notification.type and
                existing.title == notification.title and
                datetime.now() - existing.timestamp < window):
                existing.merge_count += 1
                existing.timestamp = datetime.now()
                existing.message = notification.message  # 最新メッセージ
                logger.debug(f"Merged notification (count={existing.merge_count})")
                return existing
        
        return None
    
    def _log_notification(self, notification: Notification):
        """ログ出力"""
        level = logging.INFO
        if notification.type == NotificationType.WARNING:
            level = logging.WARNING
        elif notification.type == NotificationType.ERROR:
            level = logging.ERROR
        elif notification.type == NotificationType.ACTION_REQUIRED:
            level = logging.WARNING
        
        logger.log(level, f"[{notification.type.value.upper()}] {notification.title}: {notification.message}")
    
    def get_pending_action(self) -> Optional[Notification]:
        """保留中のACTION_REQUIREDを取得"""
        return self._pending_action
    
    def resolve_action(self, response: str):
        """ACTION_REQUIREDを解決"""
        if self._pending_action:
            logger.info(f"Action resolved: {response}")
            self._pending_action.dismissed = True
            self._pending_action = None
    
    def dismiss_all(self, notification_type: Optional[NotificationType] = None):
        """通知を消去"""
        for n in self._queue:
            if notification_type is None or n.type == notification_type:
                n.dismissed = True
    
    def get_recent(self, count: int = 10) -> List[Notification]:
        """直近の通知を取得"""
        return [n for n in self._queue if not n.dismissed][-count:]
    
    def get_stats(self) -> Dict[str, Any]:
        """統計"""
        by_type = {}
        for t in NotificationType:
            by_type[t.value] = sum(1 for n in self._queue if n.type == t and not n.dismissed)
        
        return {
            "total": len(self._queue),
            "active": sum(1 for n in self._queue if not n.dismissed),
            "pending_action": self._pending_action is not None,
            "by_type": by_type
        }


# 使用例とテスト
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=== Notification System テスト ===")
    
    manager = NotificationManager()
    
    # テスト1: 基本通知
    n1 = NotificationTemplates.action_success("Click", "button_submit", 250)
    manager.send(n1)
    stats = manager.get_stats()
    print(f"Test 1 - Basic: total={stats['total']} (expected: 1)")
    
    # テスト2: 連続同タイプでマージ
    n2 = NotificationTemplates.action_success("Click", "button_next", 150)
    manager.send(n2)
    stats = manager.get_stats()
    print(f"Test 2 - Merged: total={stats['total']} (expected: 1, merged)")
    
    # テスト3: ACTION_REQUIRED
    n3 = NotificationTemplates.ask_approval(
        goal="フォーム送信",
        action="Click",
        target="submit_button",
        risk="medium",
        evidence="DOM: visible/enabled",
        expected_result="送信完了画面"
    )
    manager.send(n3)
    pending = manager.get_pending_action()
    print(f"Test 3 - Pending action: {pending is not None} (expected: True)")
    
    # テスト4: resolve
    manager.resolve_action("承認(1回)")
    pending = manager.get_pending_action()
    print(f"Test 4 - After resolve: {pending is None} (expected: True)")
    
    # テスト5: CB OPEN通知
    n5 = NotificationTemplates.circuit_breaker_open(
        app_name="test.exe",
        screen_key="main",
        action_type="Click",
        failures=3,
        retry_after=30.0,
        observation={"diff_percent": 0.5}
    )
    manager.send(n5)
    stats = manager.get_stats()
    print(f"Test 5 - CB OPEN: pending_action={stats['pending_action']} (expected: True)")
    
    # 結果
    passed = stats['pending_action'] and stats['total'] >= 2
    print(f"\n{'✅ テスト完了' if passed else '❌ 一部失敗'}")
