# -*- coding: utf-8 -*-
"""
Stealth Fetcher — BOT判定回避機能付きWebフェッチャー

Playwright + playwright-stealth を使用し、以下のカモフラージュ技術を実装:
  1. navigator.webdriver 無効化
  2. User-Agent ローテーション
  3. ヒューマンシミュレーション（ランダム遅延、スクロール）
  4. Cookie/セッション永続化
  5. Viewport ランダム化
  6. レートリミッティング
  7. 全アクセスのログ記録

使用例:
    async with StealthFetcher() as fetcher:
        html = await fetcher.fetch("https://example.com")
        text = await fetcher.fetch_as_text("https://example.com")
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# playwright-stealth（BOT検出回避）
try:
    from playwright_stealth import stealth_async
except ImportError:
    # フォールバック: stealth無しでも動作
    async def stealth_async(page):  # type: ignore
        pass

logger = logging.getLogger(__name__)

# --- User-Agent プール ---
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# --- Viewport プール ---
_VIEWPORT_POOL = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
]


@dataclass
class FetchResult:
    """フェッチ結果のデータクラス"""
    url: str
    status: int
    content_type: str
    html: str
    text: str
    elapsed_ms: int
    user_agent: str
    bot_detected: bool = False
    error: Optional[str] = None


@dataclass
class AccessLog:
    """アクセスログエントリ"""
    timestamp: str
    url: str
    status: int
    content_type: str
    bytes_fetched: int
    elapsed_ms: int
    user_agent: str
    bot_detected: bool
    error: Optional[str] = None


class StealthFetcher:
    """
    BOT判定回避機能付きWebフェッチャー

    カモフラージュ機能:
    - Playwright stealth plugin による自動検出回避
    - UA/Viewport ランダム化
    - ヒューマンシミュレーション（遅延、スクロール）
    - レートリミッティング
    - アクセスログ自動記録
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = 30000,
        max_chars: int = 15000,
        min_delay_sec: float = 1.0,
        max_delay_sec: float = 3.0,
        rate_limit_per_min: int = 20,
        log_dir: Optional[Path] = None,
    ):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.max_chars = max_chars
        self.min_delay_sec = min_delay_sec
        self.max_delay_sec = max_delay_sec
        self.rate_limit_per_min = rate_limit_per_min
        self.log_dir = log_dir

        # 内部状態
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._access_log: List[AccessLog] = []
        self._request_times: List[float] = []
        self._ua: str = random.choice(_UA_POOL)
        self._viewport: Dict[str, int] = random.choice(_VIEWPORT_POOL)

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self) -> None:
        """ブラウザを起動"""
        self._playwright = await async_playwright().start()

        # Chromiumをheadlessで起動（stealth対応）
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
            ],
        )

        # コンテキスト作成（UA/Viewport設定）
        self._context = await self._browser.new_context(
            user_agent=self._ua,
            viewport=self._viewport,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            # Cookie永続化用
            java_script_enabled=True,
            ignore_https_errors=True,
        )

        # ページ作成 + stealth適用
        self._page = await self._context.new_page()
        await stealth_async(self._page)

        logger.info(
            "StealthFetcher起動: UA=%s, viewport=%s, headless=%s",
            self._ua, self._viewport, self.headless
        )

    async def close(self) -> None:
        """ブラウザを終了し、ログを保存"""
        if self.log_dir and self._access_log:
            self._save_access_log()

        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass  # Event loop closed等は無視

        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass  # Event loop closed等は無視

        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

    # --- パブリックAPI ---

    async def fetch(self, url: str) -> FetchResult:
        """
        URLからHTMLを取得（BOT回避付き）

        Returns:
            FetchResult: 取得結果（HTML, テキスト, ステータス等）
        """
        if not self._page:
            raise RuntimeError("StealthFetcherが未起動。start()を先に呼ぶか async with を使用")

        # レートリミッティング
        await self._rate_limit()

        # ヒューマンシミュレーション: ランダム遅延
        delay = random.uniform(self.min_delay_sec, self.max_delay_sec)
        await asyncio.sleep(delay)

        start = time.monotonic()
        error_msg = None
        status = 0
        content_type = ""
        html = ""
        text = ""
        bot_detected = False

        try:
            # ページ遷移
            response = await self._page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )

            if response:
                status = response.status
                content_type = response.headers.get("content-type", "")

            # ヒューマンシミュレーション: スクロール
            await self._human_scroll()

            # HTML取得
            html = await self._page.content()

            # BOT検出チェック
            bot_detected = self._check_bot_detection(html, status)

            # テキスト抽出
            text = self._extract_text(html)

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)[:200]}"
            logger.warning("フェッチ失敗: url=%s, error=%s", url, error_msg)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # アクセスログ記録
        log_entry = AccessLog(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            url=url,
            status=status,
            content_type=content_type,
            bytes_fetched=len(html),
            elapsed_ms=elapsed_ms,
            user_agent=self._ua,
            bot_detected=bot_detected,
            error=error_msg,
        )
        self._access_log.append(log_entry)

        return FetchResult(
            url=url,
            status=status,
            content_type=content_type,
            html=html,
            text=text[:self.max_chars],
            elapsed_ms=elapsed_ms,
            user_agent=self._ua,
            bot_detected=bot_detected,
            error=error_msg,
        )

    async def fetch_as_text(self, url: str) -> str:
        """URLからテキストのみ取得（便利メソッド）"""
        result = await self.fetch(url)
        return result.text

    async def rotate_identity(self) -> None:
        """UA/Viewportを変更して新しいコンテキストを作成"""
        self._ua = random.choice(_UA_POOL)
        self._viewport = random.choice(_VIEWPORT_POOL)

        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()

        self._context = await self._browser.new_context(
            user_agent=self._ua,
            viewport=self._viewport,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        self._page = await self._context.new_page()
        await stealth_async(self._page)

        logger.info("ID回転: UA=%s, viewport=%s", self._ua, self._viewport)

    def get_access_log(self) -> List[Dict[str, Any]]:
        """アクセスログをdict形式で取得"""
        return [
            {
                "timestamp": e.timestamp,
                "url": e.url,
                "status": e.status,
                "content_type": e.content_type,
                "bytes_fetched": e.bytes_fetched,
                "elapsed_ms": e.elapsed_ms,
                "user_agent": e.user_agent,
                "bot_detected": e.bot_detected,
                "error": e.error,
            }
            for e in self._access_log
        ]

    # --- プライベートメソッド ---

    async def _rate_limit(self) -> None:
        """レートリミッティング: 1分あたりのリクエスト数を制限"""
        now = time.monotonic()
        # 1分以上前のタイムスタンプを削除
        self._request_times = [t for t in self._request_times if now - t < 60]

        if len(self._request_times) >= self.rate_limit_per_min:
            wait = 60 - (now - self._request_times[0])
            if wait > 0:
                logger.info("レートリミット: %.1f秒待機", wait)
                await asyncio.sleep(wait)

        self._request_times.append(time.monotonic())

    async def _human_scroll(self) -> None:
        """ヒューマンシミュレーション: 自然なスクロール"""
        try:
            # ランダムに2-4回スクロール
            scroll_count = random.randint(2, 4)
            for _ in range(scroll_count):
                scroll_y = random.randint(100, 400)
                await self._page.mouse.wheel(0, scroll_y)
                await asyncio.sleep(random.uniform(0.2, 0.6))
        except Exception:
            pass  # スクロール失敗は無視

    def _check_bot_detection(self, html: str, status: int) -> bool:
        """BOT検出のチェック（偽陽性を最小化）"""
        # ステータスコード403/429はBOT検出の可能性
        if status in (403, 429):
            return True

        # HTMLが極端に小さい場合（ブロックページの可能性）
        if 0 < len(html) < 2000 and status == 200:
            lower = html.lower()
            if "captcha" in lower or "verify" in lower:
                return True

        # 厳密なBOT検出パターン（ページ全体ではなく、特定の構造を検出）
        lower = html.lower()
        # CAPTCHAチャレンジのDOM要素（具体的なものだけ）
        strict_patterns = [
            "id=\"captcha",
            "class=\"captcha",
            "class=\"g-recaptcha",
            "cf-browser-verification",
            "challenge-platform",
            "hcaptcha-box",
            "please verify you are human",
            "checking your browser before",
            "id=\"challenge-running",
        ]
        for pattern in strict_patterns:
            if pattern in lower:
                logger.warning("BOT検出パターン発見: '%s'", pattern)
                return True

        return False

    def _extract_text(self, html: str) -> str:
        """HTMLからテキストを抽出"""
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")

        # 不要なタグを削除
        for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe", "nav", "footer", "header"]):
            tag.decompose()

        chunks: List[str] = []

        # タイトル
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        if title:
            chunks.append(f"# {title}")

        # メインコンテンツ
        for node in soup.select("h1, h2, h3, h4, p, li, td, th, dt, dd, article, section"):
            t = node.get_text(" ", strip=True)
            if t and len(t) > 10:  # 短すぎるテキストは除外
                chunks.append(t)

        merged = "\n".join(chunks)
        # 連続空白・改行を整理
        import re
        merged = re.sub(r"[ \t]+", " ", merged)
        merged = re.sub(r"\n{3,}", "\n\n", merged).strip()
        return merged

    def _save_access_log(self) -> None:
        """アクセスログをJSONLに保存"""
        if not self.log_dir:
            return

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.log_dir / f"access_log_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

        with open(log_file, "w", encoding="utf-8") as f:
            for entry in self._access_log:
                line = json.dumps(
                    {
                        "timestamp": entry.timestamp,
                        "url": entry.url,
                        "status": entry.status,
                        "content_type": entry.content_type,
                        "bytes_fetched": entry.bytes_fetched,
                        "elapsed_ms": entry.elapsed_ms,
                        "user_agent": entry.user_agent,
                        "bot_detected": entry.bot_detected,
                        "error": entry.error,
                    },
                    ensure_ascii=False,
                )
                f.write(line + "\n")

        logger.info("アクセスログ保存: %s (%d件)", log_file, len(self._access_log))


# --- 同期ラッパー（便利関数）---

def fetch_sync(url: str, **kwargs) -> FetchResult:
    """同期的にフェッチ（スクリプト用）"""
    async def _run():
        async with StealthFetcher(**kwargs) as fetcher:
            return await fetcher.fetch(url)
    return asyncio.run(_run())
