# -*- coding: utf-8 -*-
"""
Desktop Control v5.1.0 - Adaptive Selector Module

DOM解析で要素を動的に発見する適応型セレクタ。
ChatGPTのUI変更に対応するため、ハードコードセレクタに頼らない設計。
"""

import hashlib
from dataclasses import dataclass
from typing import Optional, List
from playwright.sync_api import Page, Locator
from playwright.async_api import Page as AsyncPage


@dataclass
class SelectorCandidate:
    """セレクタ候補"""
    selector: str
    confidence: float  # 0.0 - 1.0
    reason: str


@dataclass 
class DiscoveryResult:
    """要素発見結果"""
    selector: Optional[str]
    candidates: List[SelectorCandidate]
    method: str  # 発見方法


# フォールバック用ハードコードセレクタ（優先度低）
FALLBACK_SELECTORS = {
    "textarea": "#prompt-textarea",
    "send_button": "button[data-testid='send-button']",
    "stop_button": "button[aria-label='Stop streaming']",
    "new_chat_button": "button[data-testid='create-new-chat-button']",
    "assistant_message": "div[data-message-author-role='assistant']",
}


class AdaptiveSelector:
    """
    適応型セレクタ
    
    DOMを解析し、要素を動的に発見する。
    ハードコードセレクタはフォールバックとしてのみ使用。
    """
    
    def __init__(self, page: Page):
        self.page = page
        self._cache = {}  # セレクタキャッシュ
    
    def discover_send_button(self) -> DiscoveryResult:
        """
        送信ボタンを発見
        
        優先順位:
        1. data-testid='send-button'
        2. aria-label に 'send' を含むボタン
        3. SVG送信アイコンを含むボタン
        4. フォールバック
        """
        candidates = []
        
        # 方法1: data-testid (最も安定)
        selector = "button[data-testid='send-button']"
        if self._element_exists(selector):
            candidates.append(SelectorCandidate(selector, 1.0, "data-testid"))
            return DiscoveryResult(selector, candidates, "data-testid")
        
        # 方法2: aria-label
        for label in ["Send", "送信", "Send message", "Send prompt"]:
            selector = f"button[aria-label='{label}']"
            if self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 0.9, f"aria-label='{label}'"))
                return DiscoveryResult(selector, candidates, "aria-label")
        
        # 方法3: ボタン + SVGアイコン解析
        # 入力欄の近くにある送信系アイコンを持つボタン
        textarea = self.page.locator("#prompt-textarea, textarea[placeholder*='Message']")
        if textarea.count() > 0:
            # textareaの親要素内のボタンを探す
            parent = textarea.locator("xpath=ancestor::form | xpath=ancestor::div[contains(@class, 'composer')]")
            if parent.count() > 0:
                buttons = parent.locator("button").all()
                for btn in buttons:
                    # 矢印アイコン（送信）を探す
                    svg = btn.locator("svg")
                    if svg.count() > 0:
                        # 上向き矢印などのパターン
                        path = svg.locator("path")
                        if path.count() > 0:
                            d_attr = path.first.get_attribute("d") or ""
                            # 上向き矢印のパスパターン（簡易判定）
                            if "M" in d_attr and len(d_attr) < 200:
                                # ボタンが有効なら送信ボタンの可能性
                                if not btn.is_disabled():
                                    # 動的セレクタを生成できないので、フォールバックへ
                                    pass
        
        # フォールバック
        fallback = FALLBACK_SELECTORS["send_button"]
        candidates.append(SelectorCandidate(fallback, 0.5, "fallback"))
        return DiscoveryResult(fallback, candidates, "fallback")
    
    def discover_stop_button(self) -> DiscoveryResult:
        """
        ストリーミング停止ボタンを発見
        
        優先順位:
        1. aria-label='Stop streaming' または 'Stop'
        2. data-testid に stop を含む
        3. ■（黒四角）アイコンを持つボタン
        """
        candidates = []
        
        # 方法1: aria-label (複数パターン)
        for label in ["Stop streaming", "Stop", "停止", "Cancel"]:
            selector = f"button[aria-label='{label}']"
            if self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 1.0, f"aria-label='{label}'"))
                return DiscoveryResult(selector, candidates, "aria-label")
        
        # 方法2: data-testid
        for testid in ["stop-button", "stop-streaming-button", "abort-button"]:
            selector = f"button[data-testid='{testid}']"
            if self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 0.95, f"data-testid='{testid}'"))
                return DiscoveryResult(selector, candidates, "data-testid")
        
        # 方法3: 四角アイコンを持つボタン
        # (実装は複雑なので、フォールバックへ)
        
        # フォールバック
        fallback = FALLBACK_SELECTORS["stop_button"]
        candidates.append(SelectorCandidate(fallback, 0.5, "fallback"))
        return DiscoveryResult(fallback, candidates, "fallback")
    
    def discover_new_chat_button(self) -> DiscoveryResult:
        """
        新規チャットボタンを発見
        
        優先順位:
        1. data-testid='create-new-chat-button'
        2. aria-label に 'new chat' を含む
        3. サイドバー内の新規チャットアイコン
        4. ヘッダーのホームリンク
        """
        candidates = []
        
        # 方法1: data-testid (複数パターン - 2026年版追加)
        for testid in [
            "create-new-chat-button", 
            "new-chat-button", 
            "new-conversation-button",
            "composer-new-chat",
            "sidebar-new-chat-button",
        ]:
            selector = f"button[data-testid='{testid}']"
            if self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 1.0, f"data-testid='{testid}'"))
                return DiscoveryResult(selector, candidates, "data-testid")
        
        # 方法2: aria-label (日英対応 + 2026年版パターン)
        for label in [
            "New chat", "新規チャット", "New conversation", 
            "Start new chat", "Create new chat", "新しいチャット",
            "ホーム", "Home",
        ]:
            selector = f"button[aria-label='{label}']"
            if self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 0.9, f"aria-label='{label}'"))
                return DiscoveryResult(selector, candidates, "aria-label")
            # aタグも試す
            selector_a = f"a[aria-label='{label}']"
            if self._element_exists(selector_a):
                candidates.append(SelectorCandidate(selector_a, 0.9, f"a[aria-label='{label}']"))
                return DiscoveryResult(selector_a, candidates, "aria-label-link")
        
        # 方法3: サイドバー内のリンク/ボタン（2026年版対応）
        sidebar_selectors = [
            "a[data-discover='true'][href='/']",  # data-discover属性付きホームリンク
            "a[data-sidebar-item='true'][href='/']",  # サイドバーアイテム
            "nav a[href='/']",  # ナビゲーション内のホームリンク
            "aside a[href='/']",  # サイドバー内のホームリンク
        ]
        for sel in sidebar_selectors:
            if self._element_exists(sel):
                candidates.append(SelectorCandidate(sel, 0.8, "sidebar-link"))
                return DiscoveryResult(sel, candidates, "sidebar-analysis")
        
        # 方法4: ナビゲーション内のボタン（SVG付き）
        nav_btn_selector = "nav button:has(svg)"
        if self._element_exists(nav_btn_selector):
            candidates.append(SelectorCandidate(nav_btn_selector, 0.7, "nav-button-svg"))
            return DiscoveryResult(nav_btn_selector, candidates, "nav-analysis")
        
        # フォールバック
        fallback = FALLBACK_SELECTORS.get("new_chat_button", "button[data-testid='create-new-chat-button']")
        candidates.append(SelectorCandidate(fallback, 0.5, "fallback"))
        return DiscoveryResult(fallback, candidates, "fallback")
    
    def is_generating(self) -> bool:
        """
        生成中かどうかを複数シグナルで判定
        
        Returns:
            True: 生成中、False: 待機中/完了
        """
        # シグナル1: 停止ボタンが表示されている
        stop_result = self.discover_stop_button()
        if stop_result.selector:
            stop_btn = self.page.locator(stop_result.selector)
            if stop_btn.count() > 0 and stop_btn.is_visible():
                return True
        
        # シグナル2: 送信ボタンが非表示または無効
        send_result = self.discover_send_button()
        if send_result.selector:
            send_btn = self.page.locator(send_result.selector)
            if send_btn.count() == 0 or not send_btn.is_visible():
                return True
        
        # シグナル3: ローディングインジケータ
        loading_selectors = [
            "[data-state='loading']",
            ".animate-pulse",
            "[aria-busy='true']",
        ]
        for sel in loading_selectors:
            if self._element_exists(sel):
                return True
        
        return False
    
    def get_latest_assistant_text(self) -> tuple[str, str]:
        """
        最新のアシスタントメッセージを取得
        
        Returns:
            (text, hash): テキストとそのハッシュ
        """
        selector = FALLBACK_SELECTORS["assistant_message"]
        locator = self.page.locator(selector).last
        
        if locator.count() == 0:
            return "", ""
        
        text = locator.inner_text()
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        return text, text_hash
    
    def _element_exists(self, selector: str) -> bool:
        """要素が存在するかチェック"""
        try:
            return self.page.locator(selector).count() > 0
        except Exception:
            return False


class AsyncAdaptiveSelector:
    """
    適応型セレクタ（非同期版）
    """
    
    def __init__(self, page: AsyncPage):
        self.page = page
    
    async def discover_send_button(self) -> DiscoveryResult:
        """送信ボタンを発見"""
        candidates = []
        
        # data-testid
        selector = "button[data-testid='send-button']"
        if await self._element_exists(selector):
            candidates.append(SelectorCandidate(selector, 1.0, "data-testid"))
            return DiscoveryResult(selector, candidates, "data-testid")
        
        # aria-label
        for label in ["Send", "送信", "Send message", "Send prompt"]:
            selector = f"button[aria-label='{label}']"
            if await self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 0.9, f"aria-label='{label}'"))
                return DiscoveryResult(selector, candidates, "aria-label")
        
        # フォールバック
        fallback = FALLBACK_SELECTORS["send_button"]
        candidates.append(SelectorCandidate(fallback, 0.5, "fallback"))
        return DiscoveryResult(fallback, candidates, "fallback")
    
    async def discover_stop_button(self) -> DiscoveryResult:
        """ストリーミング停止ボタンを発見"""
        candidates = []
        
        for label in ["Stop streaming", "Stop", "停止", "Cancel"]:
            selector = f"button[aria-label='{label}']"
            if await self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 1.0, f"aria-label='{label}'"))
                return DiscoveryResult(selector, candidates, "aria-label")
        
        for testid in ["stop-button", "stop-streaming-button", "abort-button"]:
            selector = f"button[data-testid='{testid}']"
            if await self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 0.95, f"data-testid='{testid}'"))
                return DiscoveryResult(selector, candidates, "data-testid")
        
        fallback = FALLBACK_SELECTORS["stop_button"]
        candidates.append(SelectorCandidate(fallback, 0.5, "fallback"))
        return DiscoveryResult(fallback, candidates, "fallback")
    
    async def discover_new_chat_button(self) -> DiscoveryResult:
        """新規チャットボタンを発見"""
        candidates = []
        
        for testid in ["create-new-chat-button", "new-chat-button", "new-conversation-button"]:
            selector = f"button[data-testid='{testid}']"
            if await self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 1.0, f"data-testid='{testid}'"))
                return DiscoveryResult(selector, candidates, "data-testid")
        
        for label in ["New chat", "新規チャット", "New conversation", "Start new chat"]:
            selector = f"button[aria-label='{label}']"
            if await self._element_exists(selector):
                candidates.append(SelectorCandidate(selector, 0.9, f"aria-label='{label}'"))
                return DiscoveryResult(selector, candidates, "aria-label")
        
        # ナビゲーションリンク
        nav_selector = "nav a[href='/']"
        if await self._element_exists(nav_selector):
            candidates.append(SelectorCandidate(nav_selector, 0.8, "nav-link"))
            return DiscoveryResult(nav_selector, candidates, "nav-analysis")
        
        fallback = FALLBACK_SELECTORS.get("new_chat_button", "button[data-testid='create-new-chat-button']")
        candidates.append(SelectorCandidate(fallback, 0.5, "fallback"))
        return DiscoveryResult(fallback, candidates, "fallback")
    
    async def is_generating(self) -> bool:
        """生成中かどうかを複数シグナルで判定"""
        # 停止ボタン
        stop_result = await self.discover_stop_button()
        if stop_result.selector:
            stop_btn = self.page.locator(stop_result.selector)
            if await stop_btn.count() > 0:
                try:
                    if await stop_btn.is_visible():
                        return True
                except Exception:
                    pass
        
        # 送信ボタン非表示
        send_result = await self.discover_send_button()
        if send_result.selector:
            send_btn = self.page.locator(send_result.selector)
            if await send_btn.count() == 0:
                return True
            try:
                if not await send_btn.is_visible():
                    return True
            except Exception:
                pass
        
        # ローディングインジケータ
        for sel in ["[data-state='loading']", ".animate-pulse", "[aria-busy='true']"]:
            if await self._element_exists(sel):
                return True
        
        return False
    
    async def get_latest_assistant_text(self) -> tuple[str, str]:
        """最新のアシスタントメッセージを取得"""
        selector = FALLBACK_SELECTORS["assistant_message"]
        locator = self.page.locator(selector).last
        
        if await locator.count() == 0:
            return "", ""
        
        text = await locator.inner_text()
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        return text, text_hash
    
    async def _element_exists(self, selector: str) -> bool:
        """要素が存在するかチェック"""
        try:
            return await self.page.locator(selector).count() > 0
        except Exception:
            return False
