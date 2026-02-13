# -*- coding: utf-8 -*-
"""
Desktop Control v5.0.0 - Executor Module

ActionSpecを実際に実行し、成功/失敗に応じて自動でlearn()を呼ぶ。
Plannerと既存スクリプトの橋渡し役。

使い方:
    executor = Executor()
    success = executor.execute("Perplexityでtest検索")
    # → Planner経由でActionSpec取得 → 実行 → 自動学習
"""

from __future__ import annotations
import time
import subprocess
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path

# レイヤー別ツール
import pyautogui

try:
    from pywinauto import Desktop
    HAS_PYWINAUTO = True
except ImportError:
    HAS_PYWINAUTO = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from .planner import (
    Planner, Observation, PlanResult, ActionSpec,
    IntentVerb, TargetApp,
)


@dataclass
class ExecutionResult:
    """実行結果"""
    success: bool
    actions_executed: int
    actions_total: int
    error: Optional[str] = None
    learned: bool = False
    from_template: bool = False


class Executor:
    """
    ActionSpec実行エンジン
    
    Plannerから取得したActionSpecを実際に実行し、
    成功/失敗に応じて自動でlearn()を呼ぶ。
    """
    
    def __init__(self, planner: Optional[Planner] = None):
        """
        Args:
            planner: 使用するPlanner（Noneの場合は新規作成）
        """
        self.planner = planner or Planner(use_learning=True)
        self._current_observation = Observation()
    
    def execute(self, instruction: str) -> ExecutionResult:
        """
        自然言語指示を実行
        
        1. Plannerで計画（ActionSpec取得）
        2. 各ActionSpecを実行
        3. 成功/失敗に応じてlearn()
        
        Args:
            instruction: 自然言語指示（例: "Perplexityでtest検索"）
            
        Returns:
            ExecutionResult: 実行結果
        """
        # 観測更新
        self._update_observation()
        
        # 計画取得
        result = self.planner.plan_next(instruction, self._current_observation)
        
        if not result.actions:
            return ExecutionResult(
                success=False,
                actions_executed=0,
                actions_total=0,
                error="計画なし（ActionSpecが空）",
            )
        
        from_template = self.planner.from_learned
        actions_executed = 0
        
        # 各アクションを実行
        for action in result.actions:
            try:
                success = self._execute_action(action)
                if not success:
                    # 失敗 → 学習して終了
                    self.planner.learn(success=False)
                    return ExecutionResult(
                        success=False,
                        actions_executed=actions_executed,
                        actions_total=len(result.actions),
                        error=f"Action失敗: {action.action_type} on {action.target}",
                        learned=True,
                        from_template=from_template,
                    )
                actions_executed += 1
            except Exception as e:
                self.planner.learn(success=False)
                return ExecutionResult(
                    success=False,
                    actions_executed=actions_executed,
                    actions_total=len(result.actions),
                    error=str(e),
                    learned=True,
                    from_template=from_template,
                )
        
        # 全アクション成功 → 学習
        self.planner.learn(success=True)
        
        return ExecutionResult(
            success=True,
            actions_executed=actions_executed,
            actions_total=len(result.actions),
            learned=True,
            from_template=from_template,
        )
    
    def _update_observation(self) -> None:
        """現在の観測状態を更新"""
        # 簡易実装: アクティブウィンドウのタイトルを取得
        if HAS_PYWINAUTO:
            try:
                desktop = Desktop(backend="uia")
                windows = desktop.windows()
                if windows:
                    self._current_observation.title = windows[0].window_text()
            except Exception:
                pass
    
    def _execute_action(self, action: ActionSpec) -> bool:
        """
        単一のActionSpecを実行
        
        レイヤーとアクションタイプに応じて適切なツールを使用。
        """
        layer = action.layer
        action_type = action.action_type
        target = action.target
        params = action.params or {}
        
        try:
            # レイヤー別実行
            if layer == "uia":
                return self._execute_uia(action_type, target, params)
            elif layer == "cdp":
                return self._execute_cdp(action_type, target, params)
            elif layer == "pixel":
                return self._execute_pixel(action_type, target, params)
            else:
                # デフォルト: pixel
                return self._execute_pixel(action_type, target, params)
        except Exception as e:
            print(f"[Executor] Error: {e}")
            return False
    
    def _execute_uia(self, action_type: str, target: str, params: dict) -> bool:
        """UIAレイヤーで実行（Pywinauto）"""
        if not HAS_PYWINAUTO:
            print("[Executor] Pywinauto not available, falling back to pixel")
            return self._execute_pixel(action_type, target, params)
        
        if action_type == "launch":
            # アプリ起動
            args = params.get("args", [])
            cmd = [target] + args
            subprocess.Popen(cmd)
            time.sleep(2)  # 起動待機
            return True
        
        elif action_type == "focus":
            # ウィンドウフォーカス
            desktop = Desktop(backend="uia")
            windows = [w for w in desktop.windows() if target.lower() in w.window_text().lower()]
            if windows:
                windows[0].set_focus()
                time.sleep(0.5)
                return True
            return False
        
        return False
    
    def _execute_cdp(self, action_type: str, target: str, params: dict) -> bool:
        """CDPレイヤーで実行（Playwright）"""
        if not HAS_PLAYWRIGHT:
            print("[Executor] Playwright not available, falling back to pixel")
            return self._execute_pixel(action_type, target, params)
        
        # CDP接続（簡易実装）
        # 実際にはcdp_port_broker経由で接続管理が必要
        cdp_url = params.get("cdp_url", "http://127.0.0.1:9222")
        
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else None
                
                if not page:
                    return False
                
                if action_type == "navigate":
                    page.goto(target, timeout=30000)
                    return True
                
                elif action_type == "fill":
                    value = params.get("value", "")
                    page.locator(target).fill(value)
                    return True
                
                elif action_type == "click":
                    page.locator(target).click()
                    return True
                
                elif action_type == "press":
                    key = params.get("key", "Enter")
                    page.keyboard.press(key)
                    return True
                
            except Exception as e:
                print(f"[Executor] CDP error: {e}")
                return False
        
        return False
    
    def _execute_pixel(self, action_type: str, target: str, params: dict) -> bool:
        """Pixelレイヤーで実行（PyAutoGUI）"""
        if action_type == "type":
            text = params.get("text", target)
            pyautogui.typewrite(text, interval=0.05)
            return True
        
        elif action_type == "fill":
            value = params.get("value", "")
            pyautogui.typewrite(value, interval=0.05)
            return True
        
        elif action_type == "press":
            key = params.get("key", "enter")
            pyautogui.press(key.lower())
            return True
        
        elif action_type == "click":
            x = params.get("x")
            y = params.get("y")
            if x is not None and y is not None:
                pyautogui.click(x, y)
                return True
            return False
        
        elif action_type == "wait":
            duration = params.get("duration_ms", 1000) / 1000
            time.sleep(duration)
            return True
        
        return False


# === 便利関数 ===

def execute_instruction(instruction: str) -> ExecutionResult:
    """
    自然言語指示を実行するショートカット
    
    Args:
        instruction: 例: "Perplexityでtest検索"
        
    Returns:
        ExecutionResult
    """
    executor = Executor()
    return executor.execute(instruction)
