# -*- coding: utf-8 -*-
"""
Pack Contracts - アプリパックの共通契約

各パック（Excel/Outlook/freee等）が実装すべきインターフェースを定義。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class LocatorCandidate:
    """
    Locator候補。
    複数のセレクタ方式を優先度付きで保持。
    """
    id: str                          # 一意ID（例: "excel_cell_a1"）
    intent: str                      # 操作意図（例: "click", "type"）
    priority: int = 0                # 優先度（高いほど先に試行）
    
    # セレクタ（使える方式を全部記載）
    css: Optional[str] = None        # CDP用CSSセレクタ
    role: Optional[str] = None       # Playwright role
    label: Optional[str] = None      # Playwright getByLabel
    text: Optional[str] = None       # Playwright getByText
    automation_id: Optional[str] = None  # UIA AutomationId
    name: Optional[str] = None       # UIA Name
    class_name: Optional[str] = None # UIA ClassName
    control_type: Optional[str] = None  # UIA ControlType
    
    # Pixel用（最終手段）
    pixel_region: Optional[tuple] = None  # (x1, y1, x2, y2)
    pixel_template: Optional[str] = None  # 画像テンプレートパス
    
    # メタ情報
    version: str = "v1"
    compat_versions: List[str] = field(default_factory=list)  # 対応アプリバージョン


@dataclass
class RecoveryStep:
    """回復ステップ"""
    action: str     # "press_escape", "click_cancel", "wait_ms", ...
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecoveryPlan:
    """回復プラン"""
    fail_type: str
    steps: List[RecoveryStep] = field(default_factory=list)


class AppPack(ABC):
    """
    アプリパックの抽象基底クラス。
    各アプリは必ずこれを継承して実装する。
    """
    
    @property
    @abstractmethod
    def app_id(self) -> str:
        """アプリ識別子（例: "excel", "outlook"）"""
        pass
    
    @property
    @abstractmethod
    def display_name(self) -> str:
        """表示名（例: "Microsoft Excel"）"""
        pass
    
    @abstractmethod
    def detect(self, context: Dict[str, Any]) -> bool:
        """
        このパックが対象アプリかどうかを判定。
        
        Args:
            context: {window_title, process_name, url, ...}
        
        Returns:
            対象アプリならTrue
        """
        pass
    
    @abstractmethod
    def get_locators(self, screen_key: str, intent: str) -> List[LocatorCandidate]:
        """
        指定画面/操作意図に対応するlocator候補を返す。
        
        Args:
            screen_key: 画面キー
            intent: 操作意図（例: "submit_form", "open_file"）
        
        Returns:
            locator候補リスト（優先度順）
        """
        pass
    
    @abstractmethod
    def get_recovery(self, fail_type: str) -> Optional[RecoveryPlan]:
        """
        失敗タイプに対応する回復プランを返す。
        
        Args:
            fail_type: 失敗タイプ（例: "MODAL_DIALOG", "WRONG_STATE"）
        
        Returns:
            回復プラン（なければNone）
        """
        pass
