# action_contract.py - Action Contract（事前・事後条件付きアクション）
# ChatGPT 5.2相談（ラリー1）に基づく実装

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Any, Dict, List, Optional, Tuple
import time


# 型エイリアス
Check = Callable[[], bool]


class ActionFailed(Exception):
    """アクション失敗例外"""
    def __init__(self, message: str, fail_type: str = "UNKNOWN", detail: Dict[str, Any] = None):
        super().__init__(message)
        self.fail_type = fail_type
        self.detail = detail or {}


class PreconditionFailed(ActionFailed):
    """事前条件失敗"""
    def __init__(self, message: str, detail: Dict[str, Any] = None):
        super().__init__(message, fail_type="PRECONDITION", detail=detail)


class PostconditionFailed(ActionFailed):
    """事後条件失敗（タイムアウト）"""
    def __init__(self, message: str, detail: Dict[str, Any] = None):
        super().__init__(message, fail_type="POSTCONDITION_TIMEOUT", detail=detail)


@dataclass(frozen=True)
class Action:
    """
    事前・事後条件付きアクション（Contract）
    
    例:
        Action(
            kind="click",
            params={"selector": "#submit-btn"},
            pre=(lambda: page.query_selector("#submit-btn") is not None,),
            post=(lambda: "success" in page.url,),
        )
    """
    kind: str
    params: Dict[str, Any] = field(default_factory=dict)
    pre: Tuple[Check, ...] = ()
    post: Tuple[Check, ...] = ()
    timeout_s: float = 8.0
    poll_interval_s: float = 0.2
    description: str = ""


@dataclass
class ActionResult:
    """アクション実行結果"""
    success: bool
    action: Action
    elapsed_ms: int
    fail_type: Optional[str] = None
    fail_message: Optional[str] = None
    retry_count: int = 0


class ActionRunner:
    """
    Actionを実行し、Contract（pre/post条件）を検証する。
    """
    
    def __init__(self, executor: Any, trace: Any = None):
        """
        Args:
            executor: 実際のアクション実行器（execute(kind, **params)メソッド必須）
            trace: オプションのTraceオブジェクト（log()メソッド）
        """
        self.executor = executor
        self.trace = trace
    
    def run(self, action: Action, max_retries: int = 0) -> ActionResult:
        """
        アクションを実行し、結果を返す。
        
        Args:
            action: 実行するAction
            max_retries: リトライ回数
        
        Returns:
            ActionResult
        """
        start_time = time.time()
        retry_count = 0
        last_error = None
        
        while retry_count <= max_retries:
            try:
                self._run_once(action)
                elapsed_ms = int((time.time() - start_time) * 1000)
                
                if self.trace:
                    self.trace.log(
                        action=action.kind,
                        params=action.params,
                        success=True,
                        elapsed_ms=elapsed_ms,
                        retry_count=retry_count,
                    )
                
                return ActionResult(
                    success=True,
                    action=action,
                    elapsed_ms=elapsed_ms,
                    retry_count=retry_count,
                )
            
            except ActionFailed as e:
                last_error = e
                retry_count += 1
                if retry_count <= max_retries:
                    time.sleep(0.5 * retry_count)  # バックオフ
        
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        if self.trace:
            self.trace.log(
                action=action.kind,
                params=action.params,
                success=False,
                fail_type=last_error.fail_type if last_error else "UNKNOWN",
                fail_message=str(last_error) if last_error else "",
                elapsed_ms=elapsed_ms,
                retry_count=retry_count - 1,
            )
        
        return ActionResult(
            success=False,
            action=action,
            elapsed_ms=elapsed_ms,
            fail_type=last_error.fail_type if last_error else "UNKNOWN",
            fail_message=str(last_error) if last_error else "",
            retry_count=retry_count - 1,
        )
    
    def _run_once(self, action: Action) -> None:
        """1回のアクション実行（リトライなし）"""
        
        # 事前条件チェック
        for i, chk in enumerate(action.pre):
            if not chk():
                raise PreconditionFailed(
                    f"Precondition {i} failed for {action.kind}",
                    detail={"action": action.kind, "precondition_index": i},
                )
        
        # アクション実行
        self.executor.execute(action.kind, **action.params)
        
        # 事後条件チェック（ポーリング）
        if not action.post:
            return  # 事後条件なしなら即成功
        
        t0 = time.time()
        while time.time() - t0 < action.timeout_s:
            if all(chk() for chk in action.post):
                return  # 全事後条件満足
            time.sleep(action.poll_interval_s)
        
        # タイムアウト
        raise PostconditionFailed(
            f"Postcondition timeout for {action.kind}",
            detail={"action": action.kind, "timeout_s": action.timeout_s},
        )


# ============================================================
# 事後条件ファクトリ（よく使うパターン）
# ============================================================

def url_contains(page, substring: str) -> Check:
    """URLに指定文字列が含まれることを確認"""
    return lambda: substring in page.url


def url_changed(page, original_url: str) -> Check:
    """URLが変わったことを確認"""
    return lambda: page.url != original_url


def element_exists(page, selector: str) -> Check:
    """要素が存在することを確認"""
    return lambda: page.query_selector(selector) is not None


def element_not_exists(page, selector: str) -> Check:
    """要素が存在しないことを確認（ダイアログ消失等）"""
    return lambda: page.query_selector(selector) is None


def element_visible(page, selector: str) -> Check:
    """要素が可視であることを確認"""
    def check():
        el = page.query_selector(selector)
        if el is None:
            return False
        return el.is_visible()
    return check


def text_contains(page, selector: str, text: str) -> Check:
    """要素のテキストに指定文字列が含まれることを確認"""
    def check():
        el = page.query_selector(selector)
        if el is None:
            return False
        return text in (el.inner_text() or "")
    return check


def title_contains(page, substring: str) -> Check:
    """ページタイトルに指定文字列が含まれることを確認"""
    return lambda: substring in page.title()
