# -*- coding: utf-8 -*-
"""
Desktop Control v5.1.0 - ChatGPT Generation FSM
生成状態の合議判定（stopボタン + テキスト変化 + 安定化）

v5.1.0: AdaptiveSelector統合、ポーリングループ追加
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, TYPE_CHECKING
import time
import hashlib

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from playwright.async_api import Page as AsyncPage


class GenState(str, Enum):
    """生成状態"""
    IDLE = "idle"               # 待機中
    ACKED = "acked"             # ユーザーメッセージが追加された
    RUNNING = "running"         # 生成中
    STABILIZING = "stabilizing" # 変化が止まりつつある
    DONE = "done"               # 完了
    ERROR = "error"             # エラー


@dataclass(frozen=True)
class ChatGPTSignals:
    """ChatGPTの観測シグナル"""
    ts_ms: int
    url: str
    has_textbox: bool
    send_enabled: Optional[bool] = None
    stop_button_visible: Optional[bool] = None
    last_msg_len: Optional[int] = None
    last_msg_hash: Optional[str] = None
    rate_limited: bool = False
    error_banner: bool = False


@dataclass
class GenFSM:
    """生成状態機械"""
    state: GenState = GenState.IDLE
    last_progress_ms: int = 0
    last_len: int = 0
    stable_since_ms: int = 0
    last_hash: str = ""
    start_ms: int = 0
    
    def reset(self) -> None:
        """リセット"""
        self.state = GenState.IDLE
        self.last_progress_ms = 0
        self.last_len = 0
        self.stable_since_ms = 0
        self.last_hash = ""
        self.start_ms = 0


@dataclass(frozen=True)
class ChatGPTWaitConfig:
    """待機設定"""
    ack_timeout_ms: int = 10000
    start_timeout_ms: int = 15000
    stall_timeout_ms: int = 30000       # 進捗停止判定（長めに）
    stable_window_ms: int = 2000        # 変化なし継続でDONE（v5.1.0: 1200→2000）
    poll_interval_ms_fast: int = 300
    poll_interval_ms_slow: int = 800
    slow_after_ms: int = 5000
    max_total_timeout_ms: int = 180000


def update_gen_fsm(
    fsm: GenFSM,
    sig: ChatGPTSignals,
    cfg: ChatGPTWaitConfig,
) -> GenFSM:
    """
    シグナルに基づいて状態遷移
    
    合議判定:
    - stopボタン有り → RUNNING
    - テキスト長が増加 → 進捗あり（RUNNING継続）
    - テキスト変化なし一定時間 → STABILIZING → DONE
    """
    now_ms = sig.ts_ms
    
    # エラー検出
    if sig.error_banner or sig.rate_limited:
        fsm.state = GenState.ERROR
        return fsm
    
    # IDLE状態
    if fsm.state == GenState.IDLE:
        if sig.stop_button_visible:
            fsm.state = GenState.RUNNING
            fsm.start_ms = now_ms
            fsm.last_progress_ms = now_ms
        return fsm
    
    # ACKED状態（送信確認後）
    if fsm.state == GenState.ACKED:
        if sig.stop_button_visible:
            fsm.state = GenState.RUNNING
            fsm.start_ms = now_ms
            fsm.last_progress_ms = now_ms
        elif now_ms - fsm.start_ms > cfg.start_timeout_ms:
            fsm.state = GenState.ERROR
        return fsm
    
    # RUNNING状態
    if fsm.state == GenState.RUNNING:
        # 進捗チェック
        has_progress = False
        if sig.last_msg_len is not None and sig.last_msg_len > fsm.last_len:
            has_progress = True
            fsm.last_len = sig.last_msg_len
        if sig.last_msg_hash and sig.last_msg_hash != fsm.last_hash:
            has_progress = True
            fsm.last_hash = sig.last_msg_hash
        
        if has_progress:
            fsm.last_progress_ms = now_ms
            fsm.stable_since_ms = 0
        else:
            # 変化なし
            if fsm.stable_since_ms == 0:
                fsm.stable_since_ms = now_ms
        
        # stopボタンが消えた = 生成完了の候補
        if not sig.stop_button_visible:
            if fsm.stable_since_ms > 0:
                stable_duration = now_ms - fsm.stable_since_ms
                if stable_duration >= cfg.stable_window_ms:
                    fsm.state = GenState.DONE
                else:
                    fsm.state = GenState.STABILIZING
            else:
                fsm.state = GenState.STABILIZING
                fsm.stable_since_ms = now_ms
        
        # 詰まり検出
        if fsm.last_progress_ms > 0:
            stall_duration = now_ms - fsm.last_progress_ms
            if stall_duration > cfg.stall_timeout_ms:
                fsm.state = GenState.ERROR
        
        return fsm
    
    # STABILIZING状態
    if fsm.state == GenState.STABILIZING:
        # まだstopボタンが見える = RUNNING戻り
        if sig.stop_button_visible:
            fsm.state = GenState.RUNNING
            return fsm
        
        # 安定化時間チェック
        stable_duration = now_ms - fsm.stable_since_ms if fsm.stable_since_ms > 0 else 0
        if stable_duration >= cfg.stable_window_ms:
            fsm.state = GenState.DONE
        
        return fsm
    
    return fsm


def get_poll_interval(fsm: GenFSM, cfg: ChatGPTWaitConfig) -> int:
    """現在の状態に応じたポーリング間隔"""
    now_ms = int(time.time() * 1000)
    elapsed = now_ms - fsm.start_ms if fsm.start_ms > 0 else 0
    
    if elapsed > cfg.slow_after_ms:
        return cfg.poll_interval_ms_slow
    return cfg.poll_interval_ms_fast


def is_generation_complete(fsm: GenFSM) -> bool:
    """生成完了したか"""
    return fsm.state in (GenState.DONE, GenState.ERROR)


# === v5.1.0: シグナル観測とポーリングループ ===

def observe_signals_sync(page: "Page") -> ChatGPTSignals:
    """
    ページからシグナルを観測（同期版）
    
    AdaptiveSelectorを使って動的にボタンを発見し、状態を取得。
    """
    from .adaptive_selector import AdaptiveSelector, FALLBACK_SELECTORS
    
    now_ms = int(time.time() * 1000)
    selector = AdaptiveSelector(page)
    
    # URL
    url = page.url
    
    # テキストボックス
    has_textbox = page.locator("#prompt-textarea, textarea[placeholder*='Message']").count() > 0
    
    # 停止ボタン
    stop_result = selector.discover_stop_button()
    stop_visible = False
    if stop_result.selector:
        stop_btn = page.locator(stop_result.selector)
        stop_visible = stop_btn.count() > 0 and stop_btn.is_visible()
    
    # 送信ボタン
    send_result = selector.discover_send_button()
    send_enabled = None
    if send_result.selector:
        send_btn = page.locator(send_result.selector)
        if send_btn.count() > 0:
            send_enabled = send_btn.is_visible() and not send_btn.is_disabled()
    
    # 最新メッセージ
    msg_len = None
    msg_hash = None
    try:
        assistant_msg = page.locator(FALLBACK_SELECTORS["assistant_message"]).last
        if assistant_msg.count() > 0:
            text = assistant_msg.inner_text()
            msg_len = len(text)
            msg_hash = hashlib.md5(text.encode()).hexdigest()[:8]
    except Exception:
        pass  # セレクタエラー時はスキップ
    
    # エラー検出
    rate_limited = page.locator("div:has-text('You\\'ve reached')").count() > 0
    error_banner = page.locator("div[role='alert']").count() > 0
    
    return ChatGPTSignals(
        ts_ms=now_ms,
        url=url,
        has_textbox=has_textbox,
        send_enabled=send_enabled,
        stop_button_visible=stop_visible,
        last_msg_len=msg_len,
        last_msg_hash=msg_hash,
        rate_limited=rate_limited,
        error_banner=error_banner,
    )


async def observe_signals_async(page: "AsyncPage") -> ChatGPTSignals:
    """
    ページからシグナルを観測（非同期版）
    """
    from .adaptive_selector import AsyncAdaptiveSelector, FALLBACK_SELECTORS
    
    now_ms = int(time.time() * 1000)
    selector = AsyncAdaptiveSelector(page)
    
    url = page.url
    has_textbox = await page.locator("#prompt-textarea, textarea[placeholder*='Message']").count() > 0
    
    # 停止ボタン
    stop_result = await selector.discover_stop_button()
    stop_visible = False
    if stop_result.selector:
        stop_btn = page.locator(stop_result.selector)
        if await stop_btn.count() > 0:
            try:
                stop_visible = await stop_btn.is_visible()
            except Exception:
                pass
    
    # 送信ボタン
    send_result = await selector.discover_send_button()
    send_enabled = None
    if send_result.selector:
        send_btn = page.locator(send_result.selector)
        if await send_btn.count() > 0:
            try:
                is_vis = await send_btn.is_visible()
                is_dis = await send_btn.is_disabled()
                send_enabled = is_vis and not is_dis
            except Exception:
                pass
    
    # 最新メッセージ
    msg_len = None
    msg_hash = None
    try:
        assistant_msg = page.locator(FALLBACK_SELECTORS["assistant_message"]).last
        if await assistant_msg.count() > 0:
            text = await assistant_msg.inner_text()
            msg_len = len(text)
            msg_hash = hashlib.md5(text.encode()).hexdigest()[:8]
    except Exception:
        pass  # セレクタエラー時はスキップ
    
    # エラー検出
    rate_limited = await page.locator("div:has-text('You\\'ve reached')").count() > 0
    error_banner = await page.locator("div[role='alert']").count() > 0
    
    return ChatGPTSignals(
        ts_ms=now_ms,
        url=url,
        has_textbox=has_textbox,
        send_enabled=send_enabled,
        stop_button_visible=stop_visible,
        last_msg_len=msg_len,
        last_msg_hash=msg_hash,
        rate_limited=rate_limited,
        error_banner=error_banner,
    )


def wait_for_generation_sync(
    page: "Page",
    cfg: Optional[ChatGPTWaitConfig] = None,
) -> tuple[bool, GenFSM]:
    """
    生成完了をFSMで待機（同期版）
    
    Returns:
        (success, fsm): 成功時True、FSMの最終状態
    """
    if cfg is None:
        cfg = ChatGPTWaitConfig()
    
    fsm = GenFSM()
    start_time = time.time()
    
    while True:
        # タイムアウトチェック
        elapsed_ms = int((time.time() - start_time) * 1000)
        if elapsed_ms > cfg.max_total_timeout_ms:
            fsm.state = GenState.ERROR
            return False, fsm
        
        # シグナル観測
        sig = observe_signals_sync(page)
        
        # FSM更新
        fsm = update_gen_fsm(fsm, sig, cfg)
        
        # 完了判定
        if is_generation_complete(fsm):
            return fsm.state == GenState.DONE, fsm
        
        # ポーリング待機
        interval = get_poll_interval(fsm, cfg)
        page.wait_for_timeout(interval)


async def wait_for_generation_async(
    page: "AsyncPage",
    cfg: Optional[ChatGPTWaitConfig] = None,
) -> tuple[bool, GenFSM]:
    """
    生成完了をFSMで待機（非同期版）
    
    Returns:
        (success, fsm): 成功時True、FSMの最終状態
    """
    import asyncio
    
    if cfg is None:
        cfg = ChatGPTWaitConfig()
    
    fsm = GenFSM()
    start_time = time.time()
    
    while True:
        # タイムアウトチェック
        elapsed_ms = int((time.time() - start_time) * 1000)
        if elapsed_ms > cfg.max_total_timeout_ms:
            fsm.state = GenState.ERROR
            return False, fsm
        
        # シグナル観測
        sig = await observe_signals_async(page)
        
        # FSM更新
        fsm = update_gen_fsm(fsm, sig, cfg)
        
        # 完了判定
        if is_generation_complete(fsm):
            return fsm.state == GenState.DONE, fsm
        
        # ポーリング待機
        interval = get_poll_interval(fsm, cfg)
        await asyncio.sleep(interval / 1000)

