"""
Verifier - 成功判定（Verify）の標準化

目的: 誤成功・誤失敗を防ぎ、学習とCircuit Breakerを正しく動作させる

ChatGPT 5.2フィードバック（2026-02-05 Round3）より:
「execution_contextが整っても、Outcomeがブレると全てが崩れます」

合格ライン:
- すべてのActionがexpected_state（最小期待）を持ち、実行後に必ず検証される
- 検証器はDOM/UIA/画像/テキストの優先順位を持ち、層が変わっても同じ基準でOutcomeを返す
- "クリック成功"ではなく「状態一致成功」（例：URL、要素存在、ラベル一致、入力値一致）
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any
import re


class VerifyResult(Enum):
    """検証結果"""
    SUCCESS = "success"       # 状態一致
    PARTIAL = "partial"       # 部分一致（警告レベル）
    FAILURE = "failure"       # 状態不一致
    TIMEOUT = "timeout"       # タイムアウト
    SKIPPED = "skipped"       # スキップ（検証条件なし）


class VerifyMethod(Enum):
    """検証方法（優先順）"""
    DOM = "dom"               # DOM要素チェック（最優先）
    UIA = "uia"               # UIA要素チェック
    TEXT = "text"             # テキスト一致
    IMAGE = "image"           # 画像マッチング
    COMPOSITE = "composite"   # 複合条件


@dataclass
class ExpectedState:
    """期待状態（Actionごとに持つ）"""
    
    # 必須: 何を検証するか
    condition_type: str       # url, element_exists, text_contains, value_equals等
    
    # 検証対象
    target_selector: Optional[str] = None  # DOMセレクタ or UIAパス
    expected_value: Optional[str] = None   # 期待する値
    
    # オプション
    timeout_ms: int = 3000
    allow_partial: bool = False
    
    # 代替検証（フォールバック）
    fallback_method: Optional[VerifyMethod] = None


@dataclass
class VerifyOutcome:
    """検証結果詳細"""
    result: VerifyResult
    method_used: VerifyMethod
    condition_type: str
    expected: Optional[str] = None
    actual: Optional[str] = None
    message: str = ""
    duration_ms: int = 0
    
    def is_success(self) -> bool:
        return self.result == VerifyResult.SUCCESS


class Verifier:
    """統合検証器"""
    
    def __init__(self):
        self.condition_handlers: dict[str, Callable] = {
            "url": self._verify_url,
            "element_exists": self._verify_element_exists,
            "element_not_exists": self._verify_element_not_exists,
            "text_contains": self._verify_text_contains,
            "text_equals": self._verify_text_equals,
            "value_equals": self._verify_value_equals,
            "attribute_equals": self._verify_attribute_equals,
            "screen_changed": self._verify_screen_changed,
        }
    
    def verify(
        self,
        expected: ExpectedState,
        page_or_window: Any,
        method: VerifyMethod = VerifyMethod.DOM
    ) -> VerifyOutcome:
        """検証実行"""
        
        import time
        start = time.time()
        
        # 条件ハンドラを取得
        handler = self.condition_handlers.get(expected.condition_type)
        if not handler:
            return VerifyOutcome(
                result=VerifyResult.SKIPPED,
                method_used=method,
                condition_type=expected.condition_type,
                message=f"Unknown condition type: {expected.condition_type}"
            )
        
        try:
            result = handler(expected, page_or_window, method)
            result.duration_ms = int((time.time() - start) * 1000)
            return result
        except Exception as e:
            return VerifyOutcome(
                result=VerifyResult.FAILURE,
                method_used=method,
                condition_type=expected.condition_type,
                message=f"Verification error: {e}",
                duration_ms=int((time.time() - start) * 1000)
            )
    
    def _verify_url(
        self, expected: ExpectedState, page: Any, method: VerifyMethod
    ) -> VerifyOutcome:
        """URL検証"""
        try:
            actual_url = page.url
            expected_pattern = expected.expected_value or ""
            
            # 正規表現またはワイルドカードマッチ
            if "*" in expected_pattern:
                pattern = expected_pattern.replace("*", ".*")
                match = bool(re.match(pattern, actual_url))
            else:
                match = expected_pattern in actual_url
            
            return VerifyOutcome(
                result=VerifyResult.SUCCESS if match else VerifyResult.FAILURE,
                method_used=method,
                condition_type="url",
                expected=expected_pattern,
                actual=actual_url,
                message="URL match" if match else "URL mismatch"
            )
        except:
            return VerifyOutcome(
                result=VerifyResult.FAILURE,
                method_used=method,
                condition_type="url",
                message="Failed to get URL"
            )
    
    def _verify_element_exists(
        self, expected: ExpectedState, page: Any, method: VerifyMethod
    ) -> VerifyOutcome:
        """要素存在検証"""
        selector = expected.target_selector
        if not selector:
            return VerifyOutcome(
                result=VerifyResult.SKIPPED,
                method_used=method,
                condition_type="element_exists",
                message="No selector provided"
            )
        
        try:
            if method == VerifyMethod.DOM:
                element = page.query_selector(selector)
                exists = element is not None
            else:
                # UIAの場合は別ロジック（placeholder）
                exists = False
            
            return VerifyOutcome(
                result=VerifyResult.SUCCESS if exists else VerifyResult.FAILURE,
                method_used=method,
                condition_type="element_exists",
                expected=selector,
                actual="exists" if exists else "not found",
                message=f"Element {'found' if exists else 'not found'}: {selector}"
            )
        except Exception as e:
            return VerifyOutcome(
                result=VerifyResult.FAILURE,
                method_used=method,
                condition_type="element_exists",
                message=f"Error checking element: {e}"
            )
    
    def _verify_element_not_exists(
        self, expected: ExpectedState, page: Any, method: VerifyMethod
    ) -> VerifyOutcome:
        """要素非存在検証"""
        result = self._verify_element_exists(expected, page, method)
        # 結果を反転
        if result.result == VerifyResult.SUCCESS:
            result.result = VerifyResult.FAILURE
            result.message = f"Element should not exist: {expected.target_selector}"
        elif result.result == VerifyResult.FAILURE:
            result.result = VerifyResult.SUCCESS
            result.message = f"Element correctly absent: {expected.target_selector}"
        return result
    
    def _verify_text_contains(
        self, expected: ExpectedState, page: Any, method: VerifyMethod
    ) -> VerifyOutcome:
        """テキスト含有検証"""
        selector = expected.target_selector
        expected_text = expected.expected_value or ""
        
        try:
            if selector:
                element = page.query_selector(selector)
                actual_text = element.inner_text() if element else ""
            else:
                actual_text = page.inner_text("body")
            
            contains = expected_text.lower() in actual_text.lower()
            
            return VerifyOutcome(
                result=VerifyResult.SUCCESS if contains else VerifyResult.FAILURE,
                method_used=method,
                condition_type="text_contains",
                expected=expected_text,
                actual=actual_text[:100] + "..." if len(actual_text) > 100 else actual_text,
                message="Text found" if contains else "Text not found"
            )
        except Exception as e:
            return VerifyOutcome(
                result=VerifyResult.FAILURE,
                method_used=method,
                condition_type="text_contains",
                message=f"Error checking text: {e}"
            )
    
    def _verify_text_equals(
        self, expected: ExpectedState, page: Any, method: VerifyMethod
    ) -> VerifyOutcome:
        """テキスト完全一致検証"""
        selector = expected.target_selector
        expected_text = expected.expected_value or ""
        
        try:
            element = page.query_selector(selector)
            actual_text = element.inner_text().strip() if element else ""
            
            equals = actual_text == expected_text
            
            return VerifyOutcome(
                result=VerifyResult.SUCCESS if equals else VerifyResult.FAILURE,
                method_used=method,
                condition_type="text_equals",
                expected=expected_text,
                actual=actual_text,
                message="Text match" if equals else "Text mismatch"
            )
        except Exception as e:
            return VerifyOutcome(
                result=VerifyResult.FAILURE,
                method_used=method,
                condition_type="text_equals",
                message=f"Error: {e}"
            )
    
    def _verify_value_equals(
        self, expected: ExpectedState, page: Any, method: VerifyMethod
    ) -> VerifyOutcome:
        """入力値検証（input/textareaのvalue）"""
        selector = expected.target_selector
        expected_value = expected.expected_value or ""
        
        try:
            element = page.query_selector(selector)
            if not element:
                return VerifyOutcome(
                    result=VerifyResult.FAILURE,
                    method_used=method,
                    condition_type="value_equals",
                    message=f"Element not found: {selector}"
                )
            
            actual_value = element.input_value()
            equals = actual_value == expected_value
            
            return VerifyOutcome(
                result=VerifyResult.SUCCESS if equals else VerifyResult.FAILURE,
                method_used=method,
                condition_type="value_equals",
                expected=expected_value,
                actual=actual_value,
                message="Value match" if equals else "Value mismatch"
            )
        except Exception as e:
            return VerifyOutcome(
                result=VerifyResult.FAILURE,
                method_used=method,
                condition_type="value_equals",
                message=f"Error: {e}"
            )
    
    def _verify_attribute_equals(
        self, expected: ExpectedState, page: Any, method: VerifyMethod
    ) -> VerifyOutcome:
        """属性値検証"""
        # selector|attribute_name 形式
        selector = expected.target_selector or ""
        expected_value = expected.expected_value or ""
        
        try:
            if "|" in selector:
                sel, attr = selector.rsplit("|", 1)
            else:
                sel, attr = selector, "value"
            
            element = page.query_selector(sel)
            if not element:
                return VerifyOutcome(
                    result=VerifyResult.FAILURE,
                    method_used=method,
                    condition_type="attribute_equals",
                    message=f"Element not found: {sel}"
                )
            
            actual = element.get_attribute(attr) or ""
            equals = actual == expected_value
            
            return VerifyOutcome(
                result=VerifyResult.SUCCESS if equals else VerifyResult.FAILURE,
                method_used=method,
                condition_type="attribute_equals",
                expected=expected_value,
                actual=actual,
                message=f"Attribute {attr} {'match' if equals else 'mismatch'}"
            )
        except Exception as e:
            return VerifyOutcome(
                result=VerifyResult.FAILURE,
                method_used=method,
                condition_type="attribute_equals",
                message=f"Error: {e}"
            )
    
    def _verify_screen_changed(
        self, expected: ExpectedState, page: Any, method: VerifyMethod
    ) -> VerifyOutcome:
        """画面変化検証（placeholder - 実際はscreen_key比較）"""
        return VerifyOutcome(
            result=VerifyResult.PARTIAL,
            method_used=method,
            condition_type="screen_changed",
            message="Screen change verification requires pre/post comparison"
        )
    
    def format_outcome(self, outcome: VerifyOutcome) -> str:
        """結果をフォーマット"""
        icon = {
            VerifyResult.SUCCESS: "✅",
            VerifyResult.PARTIAL: "⚠️",
            VerifyResult.FAILURE: "❌",
            VerifyResult.TIMEOUT: "⏱️",
            VerifyResult.SKIPPED: "⏭️",
        }[outcome.result]
        
        lines = [
            f"{icon} [{outcome.condition_type}] {outcome.message}",
            f"    method: {outcome.method_used.value}, duration: {outcome.duration_ms}ms"
        ]
        if outcome.expected:
            lines.append(f"    expected: {outcome.expected}")
        if outcome.actual:
            lines.append(f"    actual: {outcome.actual}")
        
        return "\n".join(lines)


# テスト（モック）
if __name__ == "__main__":
    print("=" * 60)
    print("Verifier テスト（モック）")
    print("=" * 60)
    
    verifier = Verifier()
    
    # モックページ
    class MockPage:
        url = "https://chatgpt.com/c/abc123"
        
        def query_selector(self, selector):
            if selector == "#prompt-textarea":
                return MockElement("テスト入力", "テスト入力")
            if selector == ".not-exists":
                return None
            return MockElement("sample text", "")
        
        def inner_text(self, selector):
            return "Hello World! This is sample text."
    
    class MockElement:
        def __init__(self, text, value):
            self._text = text
            self._value = value
        
        def inner_text(self):
            return self._text
        
        def input_value(self):
            return self._value
        
        def get_attribute(self, name):
            return "test-class" if name == "class" else None
    
    page = MockPage()
    
    # テストケース
    tests = [
        ("URL検証", ExpectedState(
            condition_type="url",
            expected_value="chatgpt.com"
        )),
        ("要素存在", ExpectedState(
            condition_type="element_exists",
            target_selector="#prompt-textarea"
        )),
        ("要素非存在", ExpectedState(
            condition_type="element_not_exists",
            target_selector=".not-exists"
        )),
        ("テキスト含有", ExpectedState(
            condition_type="text_contains",
            expected_value="Hello"
        )),
        ("入力値一致", ExpectedState(
            condition_type="value_equals",
            target_selector="#prompt-textarea",
            expected_value="テスト入力"
        )),
    ]
    
    for name, expected in tests:
        print(f"\n--- {name} ---")
        outcome = verifier.verify(expected, page)
        print(verifier.format_outcome(outcome))
    
    print("\n" + "=" * 60)
    print("テスト完了")
