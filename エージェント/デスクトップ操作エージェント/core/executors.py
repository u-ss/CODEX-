# executors.py - 各レイヤーのExecutor実装
# ChatGPT 5.2相談（ラリー5）に基づく実装

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Tuple
import time


class ExecError(RuntimeError):
    """実行エラーベース"""
    pass


class ElementNotFound(ExecError):
    """要素が見つからない"""
    pass


class ActionNotSupported(ExecError):
    """サポートされていないアクション"""
    pass


class Executor(Protocol):
    """Executor統一インターフェース"""
    layer: str
    def execute(self, ctx: Any, action_kind: str, params: Dict[str, Any]) -> None: ...


# ============================================================
# CDPExecutor (Playwright)
# ============================================================

@dataclass
class CDPExecutor:
    """Playwright/CDP経由のExecutor"""
    layer: str = "CDP"
    
    def execute(self, ctx: Any, action_kind: str, params: Dict[str, Any]) -> None:
        page = getattr(ctx, "page", None)
        if page is None:
            raise ExecError("CDP page is missing")
        
        locator = params.get("locator") or {}
        selector = locator.get("selector")
        
        if action_kind in ("click", "type", "wait_visible", "wait_hidden") and not selector:
            raise ExecError("CDP locator.selector is required")
        
        if action_kind == "click":
            try:
                page.locator(selector).click(timeout=params.get("timeout_ms", 3000))
            except Exception as e:
                raise ExecError(f"CDP click failed: {e}")
        
        elif action_kind == "type":
            text = params.get("text", "")
            clear = params.get("clear", True)
            try:
                loc = page.locator(selector)
                if clear:
                    loc.fill("")
                loc.fill(text)
            except Exception as e:
                raise ExecError(f"CDP type failed: {e}")
        
        elif action_kind == "press":
            key = params.get("key")
            if not key:
                raise ExecError("press requires params.key")
            try:
                page.keyboard.press(key)
            except Exception as e:
                raise ExecError(f"CDP press failed: {e}")
        
        elif action_kind == "wait_visible":
            timeout_ms = params.get("timeout_ms", 5000)
            try:
                page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
            except Exception as e:
                raise ExecError(f"CDP wait_visible failed: {e}")
        
        elif action_kind == "wait_hidden":
            timeout_ms = params.get("timeout_ms", 5000)
            try:
                page.wait_for_selector(selector, state="hidden", timeout=timeout_ms)
            except Exception as e:
                raise ExecError(f"CDP wait_hidden failed: {e}")
        
        elif action_kind == "navigate":
            url = params.get("url")
            if not url:
                raise ExecError("navigate requires params.url")
            try:
                page.goto(url)
            except Exception as e:
                raise ExecError(f"CDP navigate failed: {e}")
        
        else:
            raise ActionNotSupported(f"CDP does not support: {action_kind}")


# ============================================================
# UIAExecutor (pywinauto/UIA)
# ============================================================

@dataclass
class UIAExecutor:
    """pywinauto/UIAutomation経由のExecutor"""
    layer: str = "UIA"
    exists_timeout_s: float = 0.2
    
    def execute(self, ctx: Any, action_kind: str, params: Dict[str, Any]) -> None:
        root = getattr(ctx, "uia", None)
        if root is None:
            raise ExecError("UIA root/window is missing")
        
        locator = params.get("locator") or {}
        spec = locator.get("uia")
        
        if action_kind in ("click", "type", "wait_exists", "wait_gone") and not spec:
            raise ExecError("UIA locator.uia is required")
        
        def find():
            return root.child_window(**spec)
        
        if action_kind == "click":
            el = find()
            if not el.exists(timeout=self.exists_timeout_s):
                raise ElementNotFound(f"UIA element not found: {spec}")
            try:
                el.click_input()
            except Exception as e:
                raise ExecError(f"UIA click failed: {e}")
        
        elif action_kind == "type":
            text = params.get("text", "")
            clear = params.get("clear", True)
            el = find()
            if not el.exists(timeout=self.exists_timeout_s):
                raise ElementNotFound(f"UIA element not found: {spec}")
            try:
                if clear and hasattr(el, "set_edit_text"):
                    el.set_edit_text("")
                if hasattr(el, "set_edit_text"):
                    el.set_edit_text(text)
                else:
                    el.type_keys(text, with_spaces=True, set_foreground=True)
            except Exception as e:
                raise ExecError(f"UIA type failed: {e}")
        
        elif action_kind == "wait_exists":
            timeout_s = float(params.get("timeout_s", 5.0))
            t0 = time.time()
            while time.time() - t0 < timeout_s:
                try:
                    if find().exists(timeout=0.05):
                        return
                except Exception:
                    pass
                time.sleep(0.1)
            raise ExecError(f"UIA wait_exists timeout: {spec}")
        
        elif action_kind == "wait_gone":
            timeout_s = float(params.get("timeout_s", 5.0))
            t0 = time.time()
            while time.time() - t0 < timeout_s:
                try:
                    if not find().exists(timeout=0.05):
                        return
                except Exception:
                    return
                time.sleep(0.1)
            raise ExecError(f"UIA wait_gone timeout: {spec}")
        
        else:
            raise ActionNotSupported(f"UIA does not support: {action_kind}")


# ============================================================
# PixelExecutor (PyAutoGUI)
# ============================================================

@dataclass
class PixelExecutor:
    """PyAutoGUI経由のExecutor（画像認識/座標ベース）"""
    layer: str = "PIXEL"
    
    def execute(self, ctx: Any, action_kind: str, params: Dict[str, Any]) -> None:
        import pyautogui
        
        locator = params.get("locator") or {}
        xy: Optional[Tuple[int, int]] = locator.get("xy")
        image: Optional[str] = locator.get("image")
        confidence: float = float(locator.get("confidence", 0.9))
        
        def resolve_xy() -> Tuple[int, int]:
            if xy:
                return int(xy[0]), int(xy[1])
            if image:
                pt = pyautogui.locateCenterOnScreen(image, confidence=confidence)
                if pt is None:
                    raise ElementNotFound(f"Pixel template not found: {image}")
                return int(pt.x), int(pt.y)
            raise ExecError("Pixel requires locator.xy or locator.image")
        
        if action_kind == "click":
            x, y = resolve_xy()
            pyautogui.click(x, y)
        
        elif action_kind == "type":
            text = params.get("text", "")
            interval = float(params.get("interval", 0.0))
            pyautogui.write(text, interval=interval)
        
        elif action_kind == "hotkey":
            keys = params.get("keys")
            if not keys or not isinstance(keys, (list, tuple)):
                raise ExecError("hotkey requires params.keys = ['ctrl', 'c'] etc")
            pyautogui.hotkey(*keys)
        
        else:
            raise ActionNotSupported(f"PIXEL does not support: {action_kind}")
