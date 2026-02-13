# -*- coding: utf-8 -*-
"""
ChatGPT State Monitor v1.0

リアルタイムでChatGPTの状態を監視。
DOM監視 + SS検証の併用で高精度な状態把握。
"""

import time
import hashlib
import threading
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any

try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False


class ChatGPTState(Enum):
    """ChatGPTの状態"""
    UNKNOWN = "unknown"       # 不明
    IDLE = "idle"             # 入力待ち（テキストボックス表示、停止ボタンなし）
    SENDING = "sending"       # 送信中（テキスト入力後Enter押下直後）
    GENERATING = "generating" # 回答生成中（停止ボタン表示中）
    COMPLETE = "complete"     # 回答完了（停止ボタン消失、テキスト安定）
    ERROR = "error"           # エラー（レート制限、ネットワークエラー等）
    LOGGED_OUT = "logged_out" # ログアウト状態


@dataclass
class StateSnapshot:
    """状態スナップショット"""
    state: ChatGPTState
    timestamp: float
    
    # DOM情報
    has_textbox: bool = False
    stop_button_visible: bool = False
    send_button_enabled: Optional[bool] = None
    message_count: int = 0
    last_message_length: int = 0
    last_message_hash: str = ""
    
    # エラー情報
    rate_limited: bool = False
    error_banner: bool = False
    
    # URL情報
    url: str = ""
    has_conversation_id: bool = False


@dataclass
class StateTransition:
    """状態遷移"""
    from_state: ChatGPTState
    to_state: ChatGPTState
    timestamp: float
    reason: str
    snapshot: StateSnapshot


class DOMPoller:
    """
    DOM監視クラス（同期版）
    """
    
    # セレクタ定義
    SELECTORS = {
        "textbox": "#prompt-textarea, textarea[placeholder*='Message']",
        "stop_button": "button[aria-label*='Stop'], button[data-testid='stop-button']",
        "send_button": "button[data-testid='send-button'], button[aria-label*='Send']",
        "assistant_message": "div[data-message-author-role='assistant']",
        "rate_limit": "div:has-text('You\\'ve reached')",
        "error_banner": "div[role='alert']",
    }
    
    def __init__(self, page):
        self.page = page
        self._last_hash = ""
        self._last_count = 0
    
    def poll(self) -> StateSnapshot:
        """現在の状態をポーリング"""
        now = time.time()
        
        # 各要素を安全に取得
        has_textbox = self._safe_count(self.SELECTORS["textbox"]) > 0
        stop_visible = self._safe_visible(self.SELECTORS["stop_button"])
        send_enabled = self._safe_enabled(self.SELECTORS["send_button"])
        
        # メッセージ情報
        msg_count = self._safe_count(self.SELECTORS["assistant_message"])
        msg_len, msg_hash = self._get_last_message_info()
        
        # エラー情報
        rate_limited = self._safe_count(self.SELECTORS["rate_limit"]) > 0
        error_banner = self._safe_count(self.SELECTORS["error_banner"]) > 0
        
        # URL情報
        url = self.page.url
        has_conv_id = "/c/" in url
        
        # 状態判定
        state = self._determine_state(
            has_textbox, stop_visible, send_enabled,
            rate_limited, error_banner, url
        )
        
        return StateSnapshot(
            state=state,
            timestamp=now,
            has_textbox=has_textbox,
            stop_button_visible=stop_visible,
            send_button_enabled=send_enabled,
            message_count=msg_count,
            last_message_length=msg_len,
            last_message_hash=msg_hash,
            rate_limited=rate_limited,
            error_banner=error_banner,
            url=url,
            has_conversation_id=has_conv_id,
        )
    
    def _safe_count(self, selector: str) -> int:
        """安全にcount()を実行"""
        try:
            return self.page.locator(selector).count()
        except Exception:
            return 0
    
    def _safe_visible(self, selector: str) -> bool:
        """安全にis_visible()を確認"""
        try:
            loc = self.page.locator(selector).first
            return loc.count() > 0 and loc.is_visible()
        except Exception:
            return False
    
    def _safe_enabled(self, selector: str) -> Optional[bool]:
        """安全にenabled状態を確認"""
        try:
            loc = self.page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                return not loc.is_disabled()
            return None
        except Exception:
            return None
    
    def _get_last_message_info(self) -> tuple:
        """最新メッセージのlengthとhashを取得"""
        try:
            loc = self.page.locator(self.SELECTORS["assistant_message"]).last
            if loc.count() > 0:
                text = loc.inner_text()
                return len(text), hashlib.md5(text.encode()).hexdigest()[:8]
        except Exception:
            pass
        return 0, ""
    
    def _determine_state(
        self, has_textbox: bool, stop_visible: bool, send_enabled: Optional[bool],
        rate_limited: bool, error_banner: bool, url: str
    ) -> ChatGPTState:
        """複合条件で状態を判定"""
        
        # ログアウト
        if "/auth/login" in url:
            return ChatGPTState.LOGGED_OUT
        
        # エラー
        if rate_limited:
            return ChatGPTState.ERROR
        if error_banner:
            return ChatGPTState.ERROR
        
        # 生成中（停止ボタンが見える）
        if stop_visible:
            return ChatGPTState.GENERATING
        
        # アイドル（テキストボックスあり、停止ボタンなし）
        if has_textbox and not stop_visible:
            return ChatGPTState.IDLE
        
        return ChatGPTState.UNKNOWN


class ChatGPTStateMonitor:
    """
    リアルタイム状態監視クラス
    """
    
    def __init__(
        self,
        page,
        poll_interval_ms: int = 500,
        stable_window_ms: int = 2000,
        on_state_change: Optional[Callable[[StateTransition], None]] = None,
    ):
        self.page = page
        self.poll_interval = poll_interval_ms / 1000
        self.stable_window = stable_window_ms / 1000
        self.on_state_change = on_state_change
        
        self.poller = DOMPoller(page)
        self._current_state = ChatGPTState.UNKNOWN
        self._last_snapshot: Optional[StateSnapshot] = None
        self._stable_since: Optional[float] = None
        self._history: List[StateSnapshot] = []
        self._running = False
    
    @property
    def current_state(self) -> ChatGPTState:
        return self._current_state
    
    @property 
    def is_stable(self) -> bool:
        """状態が安定しているか"""
        if self._stable_since is None:
            return False
        return (time.time() - self._stable_since) >= self.stable_window
    
    def poll_once(self) -> StateSnapshot:
        """1回ポーリングして状態を更新"""
        snapshot = self.poller.poll()
        
        # 状態変化の検出
        if self._last_snapshot:
            # テキスト変化（生成中）
            if snapshot.last_message_hash != self._last_snapshot.last_message_hash:
                self._stable_since = None  # 安定リセット
            elif self._stable_since is None:
                self._stable_since = time.time()
        
        # 状態遷移の検出
        if snapshot.state != self._current_state:
            old_state = self._current_state
            self._current_state = snapshot.state
            self._stable_since = None
            
            transition = StateTransition(
                from_state=old_state,
                to_state=snapshot.state,
                timestamp=snapshot.timestamp,
                reason=self._infer_reason(old_state, snapshot),
                snapshot=snapshot,
            )
            
            if self.on_state_change:
                self.on_state_change(transition)
        
        self._last_snapshot = snapshot
        self._history.append(snapshot)
        
        # 履歴は直近100件のみ保持
        if len(self._history) > 100:
            self._history = self._history[-100:]
        
        return snapshot
    
    def _infer_reason(self, old_state: ChatGPTState, snapshot: StateSnapshot) -> str:
        """状態遷移の理由を推測"""
        if snapshot.state == ChatGPTState.GENERATING:
            return "停止ボタン表示検出"
        if snapshot.state == ChatGPTState.IDLE and old_state == ChatGPTState.GENERATING:
            return "停止ボタン消失、生成完了"
        if snapshot.state == ChatGPTState.ERROR:
            if snapshot.rate_limited:
                return "レート制限検出"
            return "エラーバナー検出"
        if snapshot.state == ChatGPTState.LOGGED_OUT:
            return "ログインページにリダイレクト"
        return "状態変化"
    
    def wait_for_state(
        self,
        target_states: List[ChatGPTState],
        timeout_ms: int = 120000,
        require_stable: bool = True,
    ) -> tuple:
        """
        指定状態になるまで待機
        
        Returns:
            (success: bool, final_snapshot: StateSnapshot)
        """
        start = time.time()
        timeout = timeout_ms / 1000
        
        while (time.time() - start) < timeout:
            snapshot = self.poll_once()
            
            if snapshot.state in target_states:
                if require_stable:
                    if self.is_stable:
                        return True, snapshot
                else:
                    return True, snapshot
            
            # エラー状態は即座に返す
            if snapshot.state in [ChatGPTState.ERROR, ChatGPTState.LOGGED_OUT]:
                return False, snapshot
            
            time.sleep(self.poll_interval)
        
        # タイムアウト
        return False, self._last_snapshot
    
    def wait_for_generation_complete(self, timeout_ms: int = 120000) -> tuple:
        """
        生成完了を待機（GENERATING → IDLE遷移を検出）
        
        Returns:
            (success: bool, final_snapshot: StateSnapshot)
        """
        start = time.time()
        timeout = timeout_ms / 1000
        saw_generating = False
        
        print("[Monitor] Waiting for generation...")
        
        while (time.time() - start) < timeout:
            snapshot = self.poll_once()
            
            # 生成中状態を検出
            if snapshot.state == ChatGPTState.GENERATING:
                saw_generating = True
                print(f"[Monitor] GENERATING: msg_len={snapshot.last_message_length}")
            
            # 生成完了判定: 生成中を経験後、IDLEになり安定
            if saw_generating and snapshot.state == ChatGPTState.IDLE:
                if self.is_stable:
                    print(f"[Monitor] COMPLETE: stable for {self.stable_window}s")
                    return True, snapshot
            
            # エラー
            if snapshot.state in [ChatGPTState.ERROR, ChatGPTState.LOGGED_OUT]:
                print(f"[Monitor] ERROR: {snapshot.state.value}")
                return False, snapshot
            
            time.sleep(self.poll_interval)
        
        print("[Monitor] TIMEOUT")
        return False, self._last_snapshot


# SS検証機能（オプション）
class SSVerifier:
    """状態変化時のSS検証"""
    
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path(__file__).parent.parent / "logs" / "state_ss"
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def capture_and_verify(self, state: ChatGPTState) -> Dict[str, Any]:
        """SSを撮影して状態を確認"""
        if not HAS_MSS:
            return {"success": False, "error": "mss not installed"}
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        paths = []
        
        with mss.mss() as sct:
            for i, mon in enumerate(sct.monitors[1:], 1):
                fp = self.output_dir / f"state_{state.value}_{ts}_mon{i}.png"
                shot = sct.grab(mon)
                mss.tools.to_png(shot.rgb, shot.size, output=str(fp))
                paths.append(fp)
        
        return {
            "success": True,
            "state": state.value,
            "screenshots": [str(p) for p in paths],
            "timestamp": ts,
        }


def _cli_main() -> int:
    print("ChatGPT State Monitor v1.0")
    print("Usage: Import and use ChatGPTStateMonitor class")
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path

    _repo_root = _Path(__file__).resolve()
    for _parent in _repo_root.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in sys.path:
                sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(_run_logged_main("desktop", "state_monitor", _cli_main, phase_name="STATE_MONITOR_CLI"))

