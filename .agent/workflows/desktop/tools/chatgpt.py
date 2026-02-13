# -*- coding: utf-8 -*-
"""
Desktop Tools v5.1.0 - ChatGPT操作モジュール

ChatGPT（chatgpt.com）をCDP経由で操作するためのツール。
v5.1.0: AdaptiveSelector統合、FSM待機に移行
"""

import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Page, Browser

# フォールバック用セレクタ定数（AdaptiveSelectorの結果が得られない場合のみ使用）
SELECTORS = {
    "textarea": "#prompt-textarea",
    "send_button": "button[data-testid='send-button']",
    "stop_button": "button[aria-label='Stop streaming']",
    "assistant_message": "div[data-message-author-role='assistant']",
    "rate_limit": "div:has-text('You\\'ve reached')",
    "error_alert": "div[role='alert']",
}

# エラークラス
class ChatGPTError(Exception):
    """ChatGPT操作エラーの基底クラス"""
    pass

class SessionExpiredError(ChatGPTError):
    """セッション切れエラー"""
    pass

class RateLimitError(ChatGPTError):
    """レート制限エラー"""
    pass

class PageNotFoundError(ChatGPTError):
    """ChatGPTページが見つからない"""
    pass

class GenerationError(ChatGPTError):
    """生成エラー（タイムアウト、詰まり等）"""
    pass


async def connect_to_chatgpt(cdp_port: int = 9223) -> tuple[Browser, Page]:
    """
    CDPでChatGPTページに接続
    
    Args:
        cdp_port: CDPポート番号
        
    Returns:
        (browser, page) タプル
        
    Raises:
        PageNotFoundError: ChatGPTページが見つからない場合
    """
    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
    
    # ChatGPTページを探す
    for context in browser.contexts:
        for page in context.pages:
            if "chatgpt.com" in page.url:
                return browser, page
    
    await browser.close()
    raise PageNotFoundError("ChatGPTページが見つかりません。ブラウザでchatgpt.comを開いてください。")


def check_session_valid(page: Page) -> bool:
    """
    セッションが有効かチェック
    
    Returns:
        True: 有効、False: セッション切れ
    """
    url = page.url
    if "/auth/login" in url or "accounts.google.com" in url:
        return False
    return True


async def detect_rate_limit(page: Page) -> bool:
    """
    レート制限を検出
    
    Returns:
        True: レート制限発生、False: なし
    """
    locator = page.locator(SELECTORS["rate_limit"])
    count = await locator.count()
    return count > 0


async def wait_for_generation_complete(page: Page, timeout_ms: int = 180000) -> bool:
    """
    回答生成完了を待機（FSM統合版）
    
    AdaptiveSelectorとFSMを使用して合議判定。
    
    Args:
        page: Playwrightページ
        timeout_ms: タイムアウト（ミリ秒）
        
    Returns:
        True: 完了、False: タイムアウト/エラー
    """
    from .integrations.chatgpt.generation_fsm import (
        wait_for_generation_async,
        ChatGPTWaitConfig,
        GenState,
    )
    
    cfg = ChatGPTWaitConfig(
        max_total_timeout_ms=timeout_ms,
        stable_window_ms=2500,
    )
    
    success, fsm = await wait_for_generation_async(page, cfg)
    return success


async def wait_for_generation_complete_legacy(page: Page, timeout_ms: int = 180000) -> bool:
    """
    回答生成完了を待機（レガシー版 - フォールバック用）
    
    stop-button方式を使用。
    """
    from .integrations.chatgpt.adaptive_selector import AsyncAdaptiveSelector
    
    selector = AsyncAdaptiveSelector(page)
    stop_result = await selector.discover_stop_button()
    send_result = await selector.discover_send_button()
    
    stop_selector = stop_result.selector or SELECTORS["stop_button"]
    send_selector = send_result.selector or SELECTORS["send_button"]
    
    stop_btn = page.locator(stop_selector)
    send_btn = page.locator(send_selector)
    
    try:
        # Phase 1: 生成開始確認（stop-button出現を待機）
        try:
            await stop_btn.wait_for(state="visible", timeout=10000)
        except Exception:
            # stop-buttonが出現しない場合（非常に短い回答、またはエラー）
            # send-buttonがvisibleならすでに完了と判断
            try:
                await send_btn.wait_for(state="visible", timeout=3000)
                return True
            except Exception:
                return False
        
        # Phase 2: 生成完了確認（stop-button消失を待機）
        await stop_btn.wait_for(state="hidden", timeout=timeout_ms)
        
        # 安定待機（DOM更新完了を待つ）
        await page.wait_for_timeout(2000)
        return True
        
    except Exception:
        return False


async def get_latest_response(page: Page) -> str:
    """
    最新のアシスタント回答を取得
    
    Returns:
        回答テキスト
    """
    locator = page.locator(SELECTORS["assistant_message"]).last
    return await locator.inner_text()


async def ask_chatgpt(
    query: str,
    cdp_port: int = 9223,
    timeout_ms: int = 180000,
    auto_close: bool = False,
    use_fsm: bool = True,
) -> str:
    """
    ChatGPTに質問を送り、回答を取得する
    
    Args:
        query: 質問テキスト
        cdp_port: CDPポート（デフォルト9223）
        timeout_ms: 回答待ちタイムアウト（ミリ秒）
        auto_close: 完了後にブラウザ接続を閉じるか
        use_fsm: FSM待機を使用するか（v5.1.0）
        
    Returns:
        回答テキスト
        
    Raises:
        SessionExpiredError: セッション切れ
        RateLimitError: レート制限
        PageNotFoundError: ChatGPTページが見つからない
        GenerationError: 生成エラー
        ChatGPTError: その他のエラー
    """
    browser, page = await connect_to_chatgpt(cdp_port)
    
    try:
        # セッション確認
        if not check_session_valid(page):
            raise SessionExpiredError("セッション切れです。手動でログインしてください。")
        
        # ページをフォアグラウンドに
        await page.bring_to_front()
        
        # 入力欄待機（AdaptiveSelector対応）
        textarea_selector = "#prompt-textarea, textarea[placeholder*='Message']"
        textarea = page.locator(textarea_selector)
        await textarea.wait_for(state="visible", timeout=10000)
        
        # 質問入力（fill()で途中送信を防止）
        await textarea.fill(query)
        await page.wait_for_timeout(500)
        
        # 送信
        await textarea.press("Enter")
        
        # レート制限チェック
        await page.wait_for_timeout(2000)
        if await detect_rate_limit(page):
            raise RateLimitError("レート制限に達しました。しばらく待ってから再試行してください。")
        
        # 生成完了待機
        if use_fsm:
            completed = await wait_for_generation_complete(page, timeout_ms)
        else:
            completed = await wait_for_generation_complete_legacy(page, timeout_ms)
        
        if not completed:
            raise GenerationError(f"回答生成がタイムアウトしました（{timeout_ms}ms）")
        
        # 回答取得
        response = await get_latest_response(page)
        return response
        
    finally:
        if auto_close:
            await browser.close()


# === 便利関数 ===

def ask_chatgpt_sync(query: str, cdp_port: int = 9223, timeout_ms: int = 180000) -> str:
    """ask_chatgptの同期版"""
    return asyncio.run(ask_chatgpt(query, cdp_port, timeout_ms, auto_close=True))


# === CLI ===

def _cli_main() -> int:
    import sys

    if len(sys.argv) < 2:
        print("Usage: python chatgpt.py <query>")
        print("Example: python chatgpt.py 'Hello, how are you?'")
        return 1

    query = " ".join(sys.argv[1:])
    print(f"Query: {query}")
    print("Sending to ChatGPT...")

    try:
        response = ask_chatgpt_sync(query)
        print("\n=== Response ===")
        print(response)
        return 0
    except ChatGPTError as e:
        print(f"Error: {e}")
        return 1


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
    raise SystemExit(_run_logged_main("desktop", "chatgpt_tool", _cli_main, phase_name="CHATGPT_TOOL_CLI"))


