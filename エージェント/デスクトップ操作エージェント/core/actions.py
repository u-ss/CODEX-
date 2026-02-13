"""
Desktop Control Core: 統一Action型システム
-----------------------------------------
ChatGPT壁打ちRally 1で設計した統一Action型。
Click/TypeText/WaitUntilなどを共通インターフェースで扱う。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional, Protocol, Callable
import time


# =============================================================================
# Target Types（操作対象）
# =============================================================================

@dataclass(frozen=True)
class CoordTarget:
    """座標ベースのターゲット（Layer 1: PyAutoGUI）"""
    x: int
    y: int
    
    def __str__(self) -> str:
        return f"Coord({self.x}, {self.y})"


@dataclass(frozen=True)
class UIATarget:
    """UIA要素ベースのターゲット（Layer 3: Pywinauto）"""
    # 検索条件（いずれか1つ以上を指定）
    auto_id: Optional[str] = None
    name: Optional[str] = None
    control_type: Optional[str] = None
    class_name: Optional[str] = None
    
    def __str__(self) -> str:
        parts = []
        if self.auto_id:
            parts.append(f"id={self.auto_id}")
        if self.name:
            parts.append(f"name={self.name}")
        if self.control_type:
            parts.append(f"type={self.control_type}")
        return f"UIA({', '.join(parts)})"


@dataclass(frozen=True)
class SelectorTarget:
    """CSSセレクタベースのターゲット（Layer 2+: Playwright/CDP）"""
    selector: str
    frame: Optional[str] = None  # iframe対応
    
    def __str__(self) -> str:
        return f"Selector({self.selector})"


# ターゲット型のユニオン
Target = CoordTarget | UIATarget | SelectorTarget | None


# =============================================================================
# ActionResult（実行結果）
# =============================================================================

@dataclass
class ActionResult:
    """アクションの実行結果"""
    ok: bool
    executor: str  # 使用した実行エンジン（playwright, pywinauto, pyautoguiなど）
    total_attempts: int
    elapsed_s: float
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    
    def __bool__(self) -> bool:
        return self.ok


# =============================================================================
# Action Interface（プロトコル）
# =============================================================================

class Action(ABC):
    """すべてのActionの基底クラス"""
    
    # デフォルト設定
    max_retries: int = 2
    retry_delay_s: float = 0.5
    success_timeout_s: float = 5.0
    poll_interval_s: float = 0.3
    
    @property
    @abstractmethod
    def name(self) -> str:
        """アクション名（ログ/識別用）"""
        ...
    
    @property
    def target(self) -> Target:
        """操作対象（オプション）"""
        return None
    
    @property
    def success_check(self) -> Optional[Callable[[], bool]]:
        """成功判定関数（オプション）"""
        return None
    
    def signature(self) -> str:
        """アクションのシグネチャ（screen_keyと組み合わせてスコープ決定）"""
        target_str = str(self.target)[:50] if self.target else "none"
        return f"{self.name}:{target_str}"


# =============================================================================
# Concrete Actions（具体的なアクション）
# =============================================================================

@dataclass
class Click(Action):
    """クリックアクション"""
    target_: Target
    double: bool = False
    right: bool = False
    
    @property
    def name(self) -> str:
        prefix = "double_" if self.double else ("right_" if self.right else "")
        return f"{prefix}click"
    
    @property
    def target(self) -> Target:
        return self.target_


@dataclass
class TypeText(Action):
    """テキスト入力アクション"""
    target_: Target
    text: str
    clear_first: bool = False
    submit: bool = False  # Enter送信
    
    @property
    def name(self) -> str:
        return "type_text"
    
    @property
    def target(self) -> Target:
        return self.target_


@dataclass
class WaitUntil(Action):
    """条件待機アクション"""
    condition: Callable[[], bool]
    description: str = "condition"
    timeout_s: float = 10.0
    
    @property
    def name(self) -> str:
        return f"wait_until:{self.description}"
    
    @property
    def success_check(self) -> Optional[Callable[[], bool]]:
        return self.condition


@dataclass
class Navigate(Action):
    """ナビゲーションアクション（ブラウザ用）"""
    url: str
    wait_until: str = "domcontentloaded"  # load, domcontentloaded, networkidle
    
    @property
    def name(self) -> str:
        return "navigate"


@dataclass
class KeyPress(Action):
    """キー押下アクション"""
    key: str  # Enter, Tab, Escape, Ctrl+Aなど
    
    @property
    def name(self) -> str:
        return f"key:{self.key}"


@dataclass  
class Focus(Action):
    """フォーカスアクション（ウィンドウ/要素）"""
    target_: Target
    
    @property
    def name(self) -> str:
        return "focus"
    
    @property
    def target(self) -> Target:
        return self.target_


# =============================================================================
# Executor Protocol（実行エンジンのインターフェース）
# =============================================================================

class Executor(Protocol):
    """アクション実行エンジンのプロトコル"""
    
    @property
    def name(self) -> str:
        """実行エンジン名"""
        ...
    
    def can_handle(self, action: Action) -> bool:
        """このアクションを処理できるか"""
        ...
    
    def execute(self, action: Action) -> ActionResult:
        """アクションを実行"""
        ...


# =============================================================================
# ActionRunner（オーケストレーター）
# =============================================================================

class ActionRunner:
    """複数ExecutorからAction実行をオーケストレート"""
    
    def __init__(self, executors: list[Executor]):
        self.executors = executors
    
    def run(self, action: Action) -> ActionResult:
        """アクションを実行（適切なExecutorを選択）"""
        t0 = time.monotonic()
        
        # 適切なExecutorを探す
        for executor in self.executors:
            if executor.can_handle(action):
                result = executor.execute(action)
                result.elapsed_s = time.monotonic() - t0
                return result
        
        # どのExecutorも対応できない
        return ActionResult(
            ok=False,
            executor="none",
            total_attempts=0,
            elapsed_s=time.monotonic() - t0,
            error=f"No executor can handle action: {action.name}"
        )
