"""
Explore Mode（探索モード）

目的: 初見タスクで「基本導線」を安全に発見する

ChatGPT 5.2フィードバック（2026-02-05 Round5）より:
「初見タスクで足りなくなるのは最初の足がかりを作る仕組み」

設計:
- 破壊的操作は禁止（guard_systemの'探索プロファイル'）
- 探索は観測を増やすためだけに行う（操作数・時間に上限）
- 主要なナビ要素（検索/戻る/メニュー/設定）を数ステップで見つける
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any, Callable
import re


class ExploreTarget(Enum):
    """探索対象"""
    NAVIGATION = "navigation"     # ナビゲーション要素
    INPUT = "input"               # 入力要素
    ACTION = "action"             # アクション要素（ボタン等）
    MENU = "menu"                 # メニュー
    SEARCH = "search"             # 検索
    SETTINGS = "settings"         # 設定


class AffordanceType(Enum):
    """アフォーダンス（操作可能点）タイプ"""
    BUTTON = "button"
    LINK = "link"
    TEXTBOX = "textbox"
    DROPDOWN = "dropdown"
    CHECKBOX = "checkbox"
    MENU = "menu"
    SEARCH = "search"
    SUBMIT = "submit"
    CANCEL = "cancel"
    NAVIGATION = "navigation"


@dataclass
class Affordance:
    """操作可能点"""
    type: AffordanceType
    selector: str
    label: str
    importance: float           # 0.0-1.0（重要度）
    position: Optional[tuple] = None  # (x, y)
    role: Optional[str] = None  # aria role
    is_visible: bool = True
    is_enabled: bool = True
    
    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "selector": self.selector,
            "label": self.label,
            "importance": self.importance,
        }


@dataclass
class ExploreResult:
    """探索結果"""
    target: ExploreTarget
    affordances: list[Affordance] = field(default_factory=list)
    explored_steps: int = 0
    duration_ms: int = 0
    success: bool = False
    message: str = ""
    
    def get_top_candidates(self, n: int = 5) -> list[Affordance]:
        """重要度上位n件を取得"""
        sorted_affords = sorted(self.affordances, key=lambda a: a.importance, reverse=True)
        return sorted_affords[:n]


class ExploreConfig:
    """探索設定"""
    
    # 操作上限
    MAX_STEPS = 10
    MAX_DURATION_MS = 30000
    
    # 破壊的操作キーワード（探索中は禁止）
    DESTRUCTIVE_KEYWORDS = [
        "delete", "remove", "cancel", "close", "exit",
        "削除", "キャンセル", "閉じる", "終了",
        "purchase", "buy", "submit", "send",
        "購入", "送信", "確定", "実行",
    ]
    
    # 重要要素キーワード（優先探索）
    IMPORTANT_KEYWORDS = {
        "navigation": ["menu", "nav", "sidebar", "header", "メニュー", "ナビ"],
        "search": ["search", "find", "query", "検索", "探す"],
        "settings": ["settings", "config", "option", "preference", "設定", "オプション"],
        "input": ["input", "text", "textarea", "入力", "テキスト"],
        "action": ["button", "submit", "save", "ok", "ボタン", "保存", "送信"],
    }


class AffordanceDiscovery:
    """アフォーダンス（操作可能点）発見"""
    
    def __init__(self):
        self.role_to_type = {
            "button": AffordanceType.BUTTON,
            "link": AffordanceType.LINK,
            "textbox": AffordanceType.TEXTBOX,
            "combobox": AffordanceType.DROPDOWN,
            "checkbox": AffordanceType.CHECKBOX,
            "menuitem": AffordanceType.MENU,
            "searchbox": AffordanceType.SEARCH,
        }
    
    def discover_from_dom(self, page: Any) -> list[Affordance]:
        """DOMからアフォーダンスを発見"""
        affordances = []
        
        # 主要インタラクティブ要素を検索
        selectors = [
            ("button", AffordanceType.BUTTON),
            ("a[href]", AffordanceType.LINK),
            ("input[type='text']", AffordanceType.TEXTBOX),
            ("input[type='search']", AffordanceType.SEARCH),
            ("textarea", AffordanceType.TEXTBOX),
            ("select", AffordanceType.DROPDOWN),
            ("input[type='checkbox']", AffordanceType.CHECKBOX),
            ("[role='button']", AffordanceType.BUTTON),
            ("[role='link']", AffordanceType.LINK),
            ("[role='menuitem']", AffordanceType.MENU),
            ("[role='searchbox']", AffordanceType.SEARCH),
        ]
        
        for selector, aff_type in selectors:
            try:
                elements = page.query_selector_all(selector)
                for elem in elements[:20]:  # 各タイプ最大20件
                    try:
                        label = self._extract_label(elem)
                        if not label:
                            continue
                        
                        importance = self._calculate_importance(elem, label, aff_type)
                        
                        affordances.append(Affordance(
                            type=aff_type,
                            selector=selector,
                            label=label,
                            importance=importance,
                            role=elem.get_attribute("role"),
                            is_visible=elem.is_visible(),
                            is_enabled=elem.is_enabled() if hasattr(elem, "is_enabled") else True,
                        ))
                    except:
                        pass
            except:
                pass
        
        return affordances
    
    def _extract_label(self, element: Any) -> str:
        """要素からラベルを抽出"""
        try:
            # テキストコンテンツ
            text = element.inner_text()
            if text and len(text) < 100:
                return text.strip()
            
            # aria-label
            aria = element.get_attribute("aria-label")
            if aria:
                return aria.strip()
            
            # placeholder
            placeholder = element.get_attribute("placeholder")
            if placeholder:
                return placeholder.strip()
            
            # title
            title = element.get_attribute("title")
            if title:
                return title.strip()
            
            # name
            name = element.get_attribute("name")
            if name:
                return name
            
            return ""
        except:
            return ""
    
    def _calculate_importance(self, element: Any, label: str, aff_type: AffordanceType) -> float:
        """重要度を計算"""
        score = 0.5
        
        # タイプによる基本スコア
        type_scores = {
            AffordanceType.SEARCH: 0.8,
            AffordanceType.SUBMIT: 0.75,
            AffordanceType.BUTTON: 0.6,
            AffordanceType.TEXTBOX: 0.65,
            AffordanceType.LINK: 0.4,
            AffordanceType.MENU: 0.7,
        }
        score = type_scores.get(aff_type, 0.5)
        
        # キーワードマッチでブースト
        label_lower = label.lower()
        for category, keywords in ExploreConfig.IMPORTANT_KEYWORDS.items():
            for kw in keywords:
                if kw in label_lower:
                    score += 0.15
                    break
        
        # 破壊的キーワードでペナルティ
        for kw in ExploreConfig.DESTRUCTIVE_KEYWORDS:
            if kw in label_lower:
                score -= 0.3
                break
        
        # 短いラベルは具体的で重要な可能性高い
        if 2 <= len(label) <= 15:
            score += 0.1
        
        return min(1.0, max(0.0, score))


class ExploreMode:
    """探索モード管理"""
    
    def __init__(self):
        self.discovery = AffordanceDiscovery()
        self.current_target: Optional[ExploreTarget] = None
        self.explored_count = 0
        self.start_time: Optional[float] = None
        self.results: list[ExploreResult] = []
    
    def start_exploration(self, target: ExploreTarget) -> None:
        """探索開始"""
        self.current_target = target
        self.explored_count = 0
        self.start_time = datetime.now().timestamp()
    
    def can_continue(self) -> bool:
        """探索継続可能か"""
        if self.explored_count >= ExploreConfig.MAX_STEPS:
            return False
        
        if self.start_time:
            elapsed = (datetime.now().timestamp() - self.start_time) * 1000
            if elapsed > ExploreConfig.MAX_DURATION_MS:
                return False
        
        return True
    
    def is_safe_action(self, label: str) -> bool:
        """安全なアクションか（探索中に実行可能か）"""
        label_lower = label.lower()
        for kw in ExploreConfig.DESTRUCTIVE_KEYWORDS:
            if kw in label_lower:
                return False
        return True
    
    def explore(self, page: Any, target: Optional[ExploreTarget] = None) -> ExploreResult:
        """探索実行"""
        import time
        start = time.time()
        
        target = target or self.current_target or ExploreTarget.NAVIGATION
        self.start_exploration(target)
        
        # アフォーダンス発見
        affordances = self.discovery.discover_from_dom(page)
        
        # ターゲットに関連するものをフィルタ・ソート
        filtered = self._filter_by_target(affordances, target)
        
        duration_ms = int((time.time() - start) * 1000)
        self.explored_count += 1
        
        result = ExploreResult(
            target=target,
            affordances=filtered,
            explored_steps=self.explored_count,
            duration_ms=duration_ms,
            success=len(filtered) > 0,
            message=f"{len(filtered)}件のアフォーダンスを発見"
        )
        
        self.results.append(result)
        return result
    
    def _filter_by_target(self, affordances: list[Affordance], target: ExploreTarget) -> list[Affordance]:
        """ターゲットに関連するアフォーダンスをフィルタ"""
        keywords = ExploreConfig.IMPORTANT_KEYWORDS.get(target.value, [])
        
        if not keywords:
            return sorted(affordances, key=lambda a: a.importance, reverse=True)
        
        # キーワードマッチするものを優先
        matched = []
        others = []
        
        for aff in affordances:
            label_lower = aff.label.lower()
            is_match = any(kw in label_lower for kw in keywords)
            
            if is_match:
                matched.append(aff)
            else:
                others.append(aff)
        
        matched.sort(key=lambda a: a.importance, reverse=True)
        others.sort(key=lambda a: a.importance, reverse=True)
        
        return matched + others[:10]  # マッチ全部 + その他上位10件
    
    def get_best_candidate(self, target: ExploreTarget = None) -> Optional[Affordance]:
        """最良の候補を取得"""
        if not self.results:
            return None
        
        latest = self.results[-1]
        candidates = latest.get_top_candidates(1)
        return candidates[0] if candidates else None
    
    def format_result(self, result: ExploreResult) -> str:
        """結果をフォーマット"""
        lines = [
            f"探索結果 [{result.target.value}]",
            f"  発見: {len(result.affordances)}件 ({result.duration_ms}ms)",
            f"  ステップ: {result.explored_steps}/{ExploreConfig.MAX_STEPS}",
            "",
            "上位候補:"
        ]
        
        for i, aff in enumerate(result.get_top_candidates(5), 1):
            safe = "✅" if self.is_safe_action(aff.label) else "⚠️"
            lines.append(f"  {i}. {safe} [{aff.type.value}] {aff.label} (重要度:{aff.importance:.0%})")
        
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("Explore Mode テスト（モック）")
    print("=" * 60)
    
    # モック
    class MockElement:
        def __init__(self, text, role=None, visible=True):
            self._text = text
            self._role = role
            self._visible = visible
        
        def inner_text(self):
            return self._text
        
        def get_attribute(self, name):
            if name == "role":
                return self._role
            return None
        
        def is_visible(self):
            return self._visible
    
    class MockPage:
        def query_selector_all(self, selector):
            if "button" in selector:
                return [
                    MockElement("検索", "button"),
                    MockElement("設定", "button"),
                    MockElement("送信", "button"),
                    MockElement("削除", "button"),
                ]
            elif "input" in selector:
                return [
                    MockElement("テキスト入力", "textbox"),
                ]
            elif "a[href]" in selector:
                return [
                    MockElement("ホーム", "link"),
                    MockElement("ヘルプ", "link"),
                ]
            return []
    
    explorer = ExploreMode()
    page = MockPage()
    
    # ナビゲーション探索
    print("\n--- ナビゲーション探索 ---")
    result = explorer.explore(page, ExploreTarget.NAVIGATION)
    print(explorer.format_result(result))
    
    # 検索探索
    print("\n--- 検索探索 ---")
    result2 = explorer.explore(page, ExploreTarget.SEARCH)
    print(explorer.format_result(result2))
    
    # 安全性チェック
    print("\n--- 安全性チェック ---")
    print(f"'検索': 安全={explorer.is_safe_action('検索')}")
    print(f"'削除': 安全={explorer.is_safe_action('削除')}")
    print(f"'購入': 安全={explorer.is_safe_action('購入')}")
    
    print("\n" + "=" * 60)
    print("テスト完了")
