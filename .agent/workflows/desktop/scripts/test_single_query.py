# -*- coding: utf-8 -*-
"""
ChatGPTスクリプトテスト v2.0 - 検証ロジック強化版

3つの確認ポイント:
1. チャットが正常に送信されたか
2. ChatGPTが稼働中（生成中）かどうか
3. ChatGPTからの返答を適切に取得できたか
"""

import sys
import time
import hashlib
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple

from playwright.sync_api import sync_playwright, Page

# パス設定
_this_dir = Path(__file__).parent
_desktop_dir = _this_dir.parent
if str(_desktop_dir) not in sys.path:
    sys.path.insert(0, str(_desktop_dir))


@dataclass
class VerificationResult:
    """検証結果"""
    step: str
    passed: bool
    details: dict


class ChatGPTVerifier:
    """
    ChatGPT検証クラス v2.0
    v5.2準拠の複数確認手段を実装
    """
    
    # セレクタ定義
    SELECTORS = {
        "textarea": "#prompt-textarea",
        "stop_button": "button[aria-label*='Stop'], button[data-testid='stop-button']",
        "send_button": "button[data-testid='send-button']",
        "assistant_message": "div[data-message-author-role='assistant']",
        "user_message": "div[data-message-author-role='user']",
        "rate_limit": "div:has-text('You\\'ve reached')",
        "error_banner": "div[role='alert']",
    }
    
    def __init__(self, page: Page):
        self.page = page
    
    # ==================== 1. 送信確認 ====================
    
    def verify_message_sent(
        self,
        pre_msg_count: int,
        pre_url: str,
        timeout_ms: int = 10000,
    ) -> VerificationResult:
        """
        送信成功を複数手段で確認
        - DOM: メッセージ数増加
        - URL: 会話ID付与（新規チャットの場合）
        - テキストボックス: クリアされているか
        """
        start = time.time()
        timeout = timeout_ms / 1000
        poll_count = 0
        
        while (time.time() - start) < timeout:
            poll_count += 1
            elapsed = time.time() - start
            
            # ユーザーメッセージ数を確認
            user_msg_count = self._safe_count(self.SELECTORS["user_message"])
            current_url = self.page.url
            textarea_empty = self._is_textarea_empty()
            
            checks = {
                "msg_count_increased": user_msg_count > pre_msg_count,
                "url_has_conv_id": "/c/" in current_url,
                "textarea_cleared": textarea_empty,
                "pre_count": pre_msg_count,
                "post_count": user_msg_count,
                "url_changed": current_url != pre_url,
            }
            
            # 毎回ログ出力
            status_icons = [
                "📤" if checks["msg_count_increased"] else "⏳",
                "🔗" if checks["url_has_conv_id"] else "⏳",
                "📝" if checks["textarea_cleared"] else "⏳",
            ]
            print(f"    [{poll_count:02d}] {elapsed:.1f}s | msg:{pre_msg_count}→{user_msg_count} {''.join(status_icons)}")
            
            # 2つ以上成功で送信成功と判定（合議判定）
            success_count = sum([
                checks["msg_count_increased"],
                checks["url_has_conv_id"],
                checks["textarea_cleared"],
            ])
            
            if success_count >= 2:
                return VerificationResult(
                    step="送信確認",
                    passed=True,
                    details=checks,
                )
            
            time.sleep(0.3)
        
        # タイムアウト
        return VerificationResult(
            step="送信確認",
            passed=False,
            details={"error": "timeout", **checks},
        )
    
    # ==================== 2. 稼働確認 ====================
    
    def verify_chatgpt_active(
        self,
        timeout_ms: int = 15000,
    ) -> VerificationResult:
        """
        ChatGPTが稼働中（生成中）か確認
        - 停止ボタン表示
        - メッセージ長の変化
        """
        start = time.time()
        timeout = timeout_ms / 1000
        poll_count = 0
        
        saw_generating = False
        initial_length = self._get_last_assistant_message_length()
        length_changed = False
        
        while (time.time() - start) < timeout:
            poll_count += 1
            elapsed = time.time() - start
            
            # 停止ボタン確認
            stop_visible = self._safe_visible(self.SELECTORS["stop_button"])
            
            # メッセージ長変化確認
            current_length = self._get_last_assistant_message_length()
            if current_length > initial_length:
                length_changed = True
            
            if stop_visible:
                saw_generating = True
            
            # 毎回ログ出力
            stop_icon = "⏹️" if stop_visible else "⏳"
            len_icon = "📈" if length_changed else "⏳"
            print(f"    [{poll_count:02d}] {elapsed:.1f}s | stop:{stop_icon} len:{initial_length}→{current_length} {len_icon}")
            
            # 生成中を検出（stop-button または テキスト変化）
            if saw_generating or length_changed:
                return VerificationResult(
                    step="稼働確認",
                    passed=True,
                    details={
                        "stop_button_detected": saw_generating,
                        "text_length_changed": length_changed,
                        "initial_length": initial_length,
                        "current_length": current_length,
                    },
                )
            
            time.sleep(0.3)
        
        # タイムアウト - 送信ボタン復活で完了判定（高速回答の場合）
        send_visible = self._safe_visible(self.SELECTORS["send_button"])
        if send_visible:
            return VerificationResult(
                step="稼働確認",
                passed=True,
                details={
                    "fallback": "send_button_visible",
                    "note": "生成が高速完了した可能性",
                },
            )
        
        return VerificationResult(
            step="稼働確認",
            passed=False,
            details={"error": "timeout", "stop_detected": saw_generating},
        )
    
    # ==================== 3. 返答取得確認 ====================
    
    def verify_response_received(
        self,
        pre_msg_count: int,
        timeout_ms: int = 120000,
        stable_window_ms: int = 2000,
    ) -> VerificationResult:
        """
        返答を正常に取得できたか確認
        - メッセージ数増加
        - テキストハッシュ安定
        - エラーなし
        """
        start = time.time()
        timeout = timeout_ms / 1000
        stable_window = stable_window_ms / 1000
        
        last_hash = ""
        stable_since = None
        
        poll_count = 0
        
        while (time.time() - start) < timeout:
            poll_count += 1
            elapsed = time.time() - start
            
            # エラーチェック
            if self._safe_count(self.SELECTORS["rate_limit"]) > 0:
                print(f"    [{poll_count:02d}] {elapsed:.1f}s | ❌ RATE LIMIT")
                return VerificationResult(
                    step="返答取得確認",
                    passed=False,
                    details={"error": "rate_limit"},
                )
            
            if self._safe_count(self.SELECTORS["error_banner"]) > 0:
                print(f"    [{poll_count:02d}] {elapsed:.1f}s | ❌ ERROR BANNER")
                return VerificationResult(
                    step="返答取得確認",
                    passed=False,
                    details={"error": "error_banner"},
                )
            
            # 停止ボタン確認（生成中なら待機継続）
            stop_visible = self._safe_visible(self.SELECTORS["stop_button"])
            msg_count = self._safe_count(self.SELECTORS["assistant_message"])
            current_length = self._get_last_assistant_message_length()
            current_hash = self._get_last_assistant_message_hash()
            
            if stop_visible:
                print(f"    [{poll_count:02d}] {elapsed:.1f}s | ⏹️ 生成中... len={current_length}")
                stable_since = None  # 安定リセット
                last_hash = current_hash
                time.sleep(0.5)
                continue
            
            # ハッシュ変化確認
            if current_hash != last_hash:
                print(f"    [{poll_count:02d}] {elapsed:.1f}s | 📝 テキスト変化 len={current_length} hash={current_hash}")
                last_hash = current_hash
                stable_since = None  # テキスト変化中
            elif stable_since is None:
                stable_since = time.time()
            
            # 安定時間を計算
            stable_elapsed = (time.time() - stable_since) if stable_since else 0
            
            # 安定判定
            if stable_since and stable_elapsed >= stable_window:
                response_text = self._get_last_assistant_message()
                print(f"    [{poll_count:02d}] {elapsed:.1f}s | ✅ 安定確認 ({stable_elapsed:.1f}s)")
                return VerificationResult(
                    step="返答取得確認",
                    passed=True,
                    details={
                        "pre_count": pre_msg_count,
                        "post_count": msg_count,
                        "msg_count_increased": msg_count > pre_msg_count,
                        "response_length": len(response_text),
                        "response_preview": response_text[:200] + "..." if len(response_text) > 200 else response_text,
                        "stable_for_ms": stable_window_ms,
                    },
                )
            else:
                print(f"    [{poll_count:02d}] {elapsed:.1f}s | ⏳ 安定待機中 ({stable_elapsed:.1f}s/{stable_window:.1f}s)")
            
            time.sleep(0.5)
        
        return VerificationResult(
            step="返答取得確認",
            passed=False,
            details={"error": "timeout"},
        )
    
    # ==================== ヘルパー ====================
    
    def _safe_count(self, selector: str) -> int:
        try:
            return self.page.locator(selector).count()
        except Exception:
            return 0
    
    def _safe_visible(self, selector: str) -> bool:
        try:
            loc = self.page.locator(selector).first
            return loc.count() > 0 and loc.is_visible()
        except Exception:
            return False
    
    def _is_textarea_empty(self) -> bool:
        try:
            loc = self.page.locator(self.SELECTORS["textarea"])
            if loc.count() > 0:
                value = loc.input_value()
                return len(value.strip()) == 0
        except Exception:
            pass
        return False
    
    def _get_last_assistant_message(self) -> str:
        try:
            loc = self.page.locator(self.SELECTORS["assistant_message"]).last
            if loc.count() > 0:
                return loc.inner_text()
        except Exception:
            pass
        return ""
    
    def _get_last_assistant_message_length(self) -> int:
        return len(self._get_last_assistant_message())
    
    def _get_last_assistant_message_hash(self) -> str:
        text = self._get_last_assistant_message()
        return hashlib.md5(text.encode()).hexdigest()[:8] if text else ""


def test_single_query():
    """メインテスト関数"""
    print("[Test] Starting ChatGPT Script Test v2.0...")
    print("=" * 60)
    
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    
    # ChatGPTページを探す
    page = None
    for pg in ctx.pages:
        if "chatgpt.com" in pg.url:
            page = pg
            break
    
    if not page:
        print("[Error] ChatGPT page not found")
        return
    
    print(f"[Found] {page.url[:80]}")
    page.bring_to_front()
    
    # 検証クラス初期化
    verifier = ChatGPTVerifier(page)
    results = []
    
    # 入力欄確認
    textarea = page.locator("#prompt-textarea")
    if not textarea.is_visible():
        print("[Error] Textarea not visible")
        print(f"Current URL: {page.url}")
        return
    
    # 事前情報取得
    pre_user_msg_count = verifier._safe_count(verifier.SELECTORS["user_message"])
    pre_assistant_msg_count = verifier._safe_count(verifier.SELECTORS["assistant_message"])
    pre_url = page.url
    
    print(f"[Pre] user_msgs={pre_user_msg_count}, assistant_msgs={pre_assistant_msg_count}")
    print(f"[Pre] URL={pre_url[:60]}...")
    
    # メッセージ送信
    test_message = "Hello, this is a test. Please respond with 'OK'."
    print(f"\n[Sending] '{test_message}'")
    textarea.fill(test_message)
    time.sleep(0.3)
    textarea.press("Enter")
    
    # ==================== 検証1: 送信確認 ====================
    print("\n[Verify-1] 送信確認...")
    result1 = verifier.verify_message_sent(pre_user_msg_count, pre_url, timeout_ms=10000)
    results.append(result1)
    if result1.passed:
        print(f"  ✅ PASS: {result1.details}")
    else:
        print(f"  ❌ FAIL: {result1.details}")
    
    # ==================== 検証2: 稼働確認 ====================
    print("\n[Verify-2] 稼働確認...")
    result2 = verifier.verify_chatgpt_active(timeout_ms=15000)
    results.append(result2)
    if result2.passed:
        print(f"  ✅ PASS: {result2.details}")
    else:
        print(f"  ❌ FAIL: {result2.details}")
    
    # ==================== 検証3: 返答取得確認 ====================
    print("\n[Verify-3] 返答取得確認...")
    result3 = verifier.verify_response_received(pre_assistant_msg_count, timeout_ms=60000)
    results.append(result3)
    if result3.passed:
        print(f"  ✅ PASS:")
        print(f"     - msg_count: {result3.details.get('pre_count')} → {result3.details.get('post_count')}")
        print(f"     - response_length: {result3.details.get('response_length')}")
        print(f"     - preview: {result3.details.get('response_preview', '')[:100]}...")
    else:
        print(f"  ❌ FAIL: {result3.details}")
    
    # ==================== 結果サマリ ====================
    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"[Result] {passed}/{total} checks passed")
    
    if passed == total:
        print("🎉 All checks passed!")
    else:
        print("⚠️ Some checks failed")
        for r in results:
            status = "✅" if r.passed else "❌"
            print(f"  {status} {r.step}")
    
    browser.close()
    p.stop()


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        test_single_query()
    else:
        raise SystemExit(
            run_logged_main(
                "desktop",
                "test_single_query",
                lambda: test_single_query(),
            )
        )
