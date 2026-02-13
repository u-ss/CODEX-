# -*- coding: utf-8 -*-
"""
Desktop Control v5.0.0-alpha - ActionSpec Module
アクション仕様の型定義
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Literal, Any


class RiskLevel(str, Enum):
    """リスクレベル"""
    LOW = "low"           # 自動実行OK（検索、閲覧）
    MEDIUM = "medium"     # 警告表示（ファイル保存）
    HIGH = "high"         # 承認必須（送信、削除、購入）


class VerifyType(str, Enum):
    """検証タイプ"""
    NONE = "none"                    # 検証なし
    URL_CONTAINS = "url_contains"    # URLに文字列が含まれる
    ELEMENT_VISIBLE = "element_visible"  # 要素が表示される
    TEXT_MATCHES = "text_matches"    # テキストが一致
    ELEMENT_HIDDEN = "element_hidden"    # 要素が非表示になる


class WaitKind(str, Enum):
    """待機種類"""
    NONE = "none"
    TIMEOUT = "timeout"              # 固定時間待機
    ELEMENT_VISIBLE = "element_visible"  # 要素表示まで待機
    ELEMENT_HIDDEN = "element_hidden"    # 要素非表示まで待機
    NETWORK_IDLE = "network_idle"    # ネットワーク安定まで待機


LayerType = Literal["cdp", "uia", "pixel", "vlm"]
ActionType = Literal[
    # Browser (CDP)
    "navigate", "fill", "click", "press", "scroll", "wait", "screenshot",
    # UIA (Electron/Native)
    "uia_focus", "uia_click", "uia_type", "uia_invoke",
]


@dataclass(frozen=True)
class VerifyCheck:
    """成功検証条件"""
    type: VerifyType = VerifyType.NONE
    target: str = ""           # セレクタ or URL部分文字列
    expected: str = ""         # 期待値（text_matches用）
    timeout_ms: int = 5000


@dataclass(frozen=True)
class WaitSpec:
    """待機仕様"""
    kind: WaitKind = WaitKind.NONE
    target: str = ""           # セレクタ（element系の場合）
    timeout_ms: int = 30000
    poll_ms: int = 500


@dataclass(frozen=True)
class RiskSpec:
    """リスク仕様"""
    level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ActionSpec:
    """
    実行可能なアクション仕様
    
    Plannerが生成し、Executorが実行する。
    """
    # 必須
    layer: LayerType
    action_type: ActionType
    
    # ターゲット（セレクタヒント or URL）
    target: str = ""
    
    # アクション固有パラメータ
    params: dict[str, Any] = field(default_factory=dict)
    
    # 待機・検証・リスク
    wait: WaitSpec = field(default_factory=WaitSpec)
    verify: VerifyCheck = field(default_factory=VerifyCheck)
    risk: RiskSpec = field(default_factory=RiskSpec)
    
    # メタ情報
    description: str = ""      # 人間向け説明
    fallback_layer: Optional[LayerType] = None  # 失敗時のフォールバック


# よく使うActionSpecのファクトリ関数
def navigate_action(url: str, wait_for: str = "") -> ActionSpec:
    """ナビゲーションアクション"""
    return ActionSpec(
        layer="cdp",
        action_type="navigate",
        target=url,
        wait=WaitSpec(
            kind=WaitKind.NETWORK_IDLE if not wait_for else WaitKind.ELEMENT_VISIBLE,
            target=wait_for,
            timeout_ms=30000,
        ),
        verify=VerifyCheck(
            type=VerifyType.URL_CONTAINS,
            target=url.split("/")[2] if "/" in url else url,  # ドメイン抽出
        ),
        description=f"Navigate to {url}",
    )


def fill_action(selector: str, text: str, clear_first: bool = True) -> ActionSpec:
    """テキスト入力アクション"""
    return ActionSpec(
        layer="cdp",
        action_type="fill",
        target=selector,
        params={"text": text, "clear_first": clear_first},
        wait=WaitSpec(
            kind=WaitKind.ELEMENT_VISIBLE,
            target=selector,
            timeout_ms=10000,
        ),
        description=f"Fill '{text[:20]}...' into {selector}",
    )


def click_action(selector: str, wait_after: str = "") -> ActionSpec:
    """クリックアクション"""
    return ActionSpec(
        layer="cdp",
        action_type="click",
        target=selector,
        wait=WaitSpec(
            kind=WaitKind.ELEMENT_VISIBLE,
            target=selector,
            timeout_ms=10000,
        ),
        verify=VerifyCheck(
            type=VerifyType.ELEMENT_VISIBLE if wait_after else VerifyType.NONE,
            target=wait_after,
        ),
        description=f"Click {selector}",
    )


def press_action(key: str) -> ActionSpec:
    """キー押下アクション"""
    return ActionSpec(
        layer="cdp",
        action_type="press",
        params={"key": key},
        description=f"Press {key}",
    )


def wait_action(timeout_ms: int = 1000) -> ActionSpec:
    """待機アクション"""
    return ActionSpec(
        layer="cdp",
        action_type="wait",
        wait=WaitSpec(
            kind=WaitKind.TIMEOUT,
            timeout_ms=timeout_ms,
        ),
        description=f"Wait {timeout_ms}ms",
    )
