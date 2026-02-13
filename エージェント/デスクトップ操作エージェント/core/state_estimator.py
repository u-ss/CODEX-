"""
State Estimator（状態推定器）

目的: 「今どの状態か」を正確に推定し、初見タスクでも対応可能にする

ChatGPT 5.2フィードバック（2026-02-05 Round4）より:
「screen_keyで画面識別しても、『今どの状態か』を推定するロジックが薄い」

設計:
- 観測は1種類に頼らず、クロスチェック（矛盾したら確度を下げる）
- 各判断に証拠（Evidence）を必ず残す
- Assertion → Evidence → Freshness → Confidence

観測ソース:
- CDP/DOM: URL、要素存在、テキスト、可視、クリック可能
- UIA: ウィンドウ/コントロール階層、Enable/Visible、フォーカス、Value
- SS: 画面差分、特定領域の見た目、ダイアログの有無
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any
import time


class ObservationSource(Enum):
    """観測ソース"""
    DOM = "dom"           # CDP/Playwright DOM
    UIA = "uia"           # Pywinauto UIA
    SS = "ss"             # スクリーンショット/画像
    MIXED = "mixed"       # 複合


class StateCategory(Enum):
    """状態カテゴリ"""
    READY = "ready"           # 操作可能
    LOADING = "loading"       # 読み込み中
    MODAL = "modal"           # モーダル表示中
    ERROR = "error"           # エラー状態
    TRANSITION = "transition" # 遷移中
    UNKNOWN = "unknown"       # 不明


@dataclass
class Evidence:
    """証拠（観測結果）"""
    source: ObservationSource
    observation_type: str     # url, element_exists, text_content等
    value: Any                # 観測値
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    freshness_ms: int = 0     # 観測からの経過時間
    
    def is_stale(self, max_age_ms: int = 2000) -> bool:
        """古くなったか"""
        return self.freshness_ms > max_age_ms


@dataclass
class Assertion:
    """主張（状態推定結果）"""
    statement: str            # 「ChatGPTの入力欄が表示されている」等
    confidence: float         # 0.0-1.0
    evidences: list[Evidence] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # 矛盾があれば記録
    
    def add_evidence(self, evidence: Evidence, supports: bool = True):
        """証拠を追加"""
        self.evidences.append(evidence)
        if not supports:
            self.conflicts.append(f"{evidence.source.value}: {evidence.observation_type}")
            self.confidence *= 0.5  # 矛盾があれば確度を下げる


@dataclass
class StateEstimate:
    """状態推定結果"""
    category: StateCategory
    screen_key: str
    description: str          # 人間可読な状態説明
    confidence: float         # 全体確度
    assertions: list[Assertion] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 追加情報
    url: Optional[str] = None
    active_element: Optional[str] = None
    modal_type: Optional[str] = None
    error_message: Optional[str] = None
    
    def is_confident(self, threshold: float = 0.7) -> bool:
        """確度が十分か"""
        return self.confidence >= threshold
    
    def needs_reobservation(self) -> bool:
        """再観測が必要か"""
        return self.confidence < 0.5 or self.category == StateCategory.UNKNOWN


class StateEstimator:
    """状態推定器"""
    
    def __init__(self):
        self.observation_history: list[Evidence] = []
        self.state_history: list[StateEstimate] = []
        self.max_history = 50
        
        # 状態パターン
        self.state_patterns = {
            "chatgpt_ready": {
                "indicators": ["#prompt-textarea", "送信", "ChatGPT"],
                "category": StateCategory.READY
            },
            "chatgpt_loading": {
                "indicators": ["Stop", "Thinking", "生成中"],
                "category": StateCategory.LOADING
            },
            "dialog_modal": {
                "indicators": ["dialog", "modal", "確認", "キャンセル"],
                "category": StateCategory.MODAL
            },
            "error_page": {
                "indicators": ["error", "エラー", "404", "500", "問題が発生"],
                "category": StateCategory.ERROR
            },
        }
    
    def observe_dom(self, page: Any) -> list[Evidence]:
        """DOM観測"""
        evidences = []
        now = datetime.now().isoformat()
        
        try:
            # URL
            evidences.append(Evidence(
                source=ObservationSource.DOM,
                observation_type="url",
                value=page.url,
                timestamp=now
            ))
            
            # タイトル
            evidences.append(Evidence(
                source=ObservationSource.DOM,
                observation_type="title",
                value=page.title(),
                timestamp=now
            ))
            
            # 主要要素の存在チェック
            selectors = [
                "#prompt-textarea",
                "[data-testid='send-button']",
                "[role='dialog']",
                ".error",
                "[aria-busy='true']",
            ]
            for sel in selectors:
                try:
                    element = page.query_selector(sel)
                    evidences.append(Evidence(
                        source=ObservationSource.DOM,
                        observation_type=f"element:{sel}",
                        value=element is not None,
                        timestamp=now
                    ))
                except:
                    pass
            
            # ボディテキスト（最初の500文字）
            try:
                body_text = page.inner_text("body")[:500]
                evidences.append(Evidence(
                    source=ObservationSource.DOM,
                    observation_type="body_text_sample",
                    value=body_text,
                    timestamp=now
                ))
            except:
                pass
                
        except Exception as e:
            evidences.append(Evidence(
                source=ObservationSource.DOM,
                observation_type="error",
                value=str(e),
                timestamp=now
            ))
        
        self.observation_history.extend(evidences)
        return evidences
    
    def observe_uia(self, window_title: Optional[str] = None) -> list[Evidence]:
        """UIA観測"""
        evidences = []
        now = datetime.now().isoformat()
        
        try:
            from pywinauto import Desktop
            
            desktop = Desktop(backend="uia")
            windows = desktop.windows()
            
            # アクティブウィンドウ
            if windows:
                active = windows[0]
                evidences.append(Evidence(
                    source=ObservationSource.UIA,
                    observation_type="active_window",
                    value=str(active),
                    timestamp=now
                ))
            
            # 対象ウィンドウの検索
            if window_title:
                for w in windows:
                    if window_title.lower() in str(w).lower():
                        evidences.append(Evidence(
                            source=ObservationSource.UIA,
                            observation_type="target_window_found",
                            value=True,
                            timestamp=now
                        ))
                        break
                else:
                    evidences.append(Evidence(
                        source=ObservationSource.UIA,
                        observation_type="target_window_found",
                        value=False,
                        timestamp=now
                    ))
                    
        except Exception as e:
            evidences.append(Evidence(
                source=ObservationSource.UIA,
                observation_type="error",
                value=str(e),
                timestamp=now
            ))
        
        self.observation_history.extend(evidences)
        return evidences
    
    def estimate_state(
        self,
        dom_evidences: list[Evidence],
        uia_evidences: list[Evidence] = None,
        screen_key: str = ""
    ) -> StateEstimate:
        """状態を推定"""
        
        all_evidences = dom_evidences + (uia_evidences or [])
        
        # 基本情報抽出
        url = None
        body_text = ""
        has_dialog = False
        has_error = False
        is_loading = False
        
        for ev in all_evidences:
            if ev.observation_type == "url":
                url = ev.value
            elif ev.observation_type == "body_text_sample":
                body_text = ev.value
            elif ev.observation_type == "element:[role='dialog']" and ev.value:
                has_dialog = True
            elif ev.observation_type == "element:.error" and ev.value:
                has_error = True
            elif ev.observation_type == "element:[aria-busy='true']" and ev.value:
                is_loading = True
        
        # 状態カテゴリ判定
        category = StateCategory.UNKNOWN
        description = "状態不明"
        confidence = 0.5
        assertions = []
        
        # パターンマッチング
        for pattern_name, pattern in self.state_patterns.items():
            matched = 0
            total = len(pattern["indicators"])
            
            for indicator in pattern["indicators"]:
                if indicator in body_text or indicator in (url or ""):
                    matched += 1
            
            if matched > 0:
                match_ratio = matched / total
                assertion = Assertion(
                    statement=f"パターン'{pattern_name}'に{matched}/{total}一致",
                    confidence=match_ratio
                )
                assertions.append(assertion)
                
                if match_ratio > 0.5 and match_ratio > confidence:
                    category = pattern["category"]
                    description = pattern_name
                    confidence = match_ratio
        
        # 特殊状態の上書き
        if has_dialog:
            category = StateCategory.MODAL
            description = "モーダルダイアログ表示中"
            confidence = 0.9
        elif has_error:
            category = StateCategory.ERROR
            description = "エラー状態"
            confidence = 0.8
        elif is_loading:
            category = StateCategory.LOADING
            description = "読み込み中"
            confidence = 0.85
        
        # クロスチェック（DOM vs UIA）
        if uia_evidences:
            dom_window_found = False
            uia_window_found = False
            
            for ev in dom_evidences:
                if "title" in ev.observation_type and ev.value:
                    dom_window_found = True
            
            for ev in uia_evidences:
                if ev.observation_type == "target_window_found":
                    uia_window_found = ev.value
            
            if dom_window_found != uia_window_found:
                # 矛盾あり
                confidence *= 0.7
                assertions.append(Assertion(
                    statement="DOM/UIA間で矛盾あり",
                    confidence=0.5,
                    conflicts=["window_visibility"]
                ))
        
        estimate = StateEstimate(
            category=category,
            screen_key=screen_key,
            description=description,
            confidence=confidence,
            assertions=assertions,
            url=url
        )
        
        self.state_history.append(estimate)
        if len(self.state_history) > self.max_history:
            self.state_history = self.state_history[-self.max_history:]
        
        return estimate
    
    def get_state_diff(self) -> Optional[dict]:
        """前回との状態差分を取得"""
        if len(self.state_history) < 2:
            return None
        
        prev = self.state_history[-2]
        curr = self.state_history[-1]
        
        diff = {
            "category_changed": prev.category != curr.category,
            "screen_changed": prev.screen_key != curr.screen_key,
            "confidence_delta": curr.confidence - prev.confidence,
            "prev": prev,
            "curr": curr,
        }
        
        return diff
    
    def format_estimate(self, estimate: StateEstimate) -> str:
        """推定結果をフォーマット"""
        icon = {
            StateCategory.READY: "✅",
            StateCategory.LOADING: "⏳",
            StateCategory.MODAL: "📋",
            StateCategory.ERROR: "❌",
            StateCategory.TRANSITION: "🔄",
            StateCategory.UNKNOWN: "❓",
        }[estimate.category]
        
        lines = [
            f"{icon} 状態: {estimate.description}",
            f"   カテゴリ: {estimate.category.value}",
            f"   確度: {estimate.confidence:.1%}",
            f"   URL: {estimate.url or 'N/A'}",
        ]
        
        if estimate.assertions:
            lines.append("   根拠:")
            for a in estimate.assertions:
                lines.append(f"     - {a.statement} (確度:{a.confidence:.1%})")
        
        if estimate.needs_reobservation():
            lines.append("   ⚠️ 再観測推奨")
        
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("State Estimator テスト（モック）")
    print("=" * 60)
    
    estimator = StateEstimator()
    
    # モックDOM証拠
    dom_evidences = [
        Evidence(ObservationSource.DOM, "url", "https://chatgpt.com/c/abc123"),
        Evidence(ObservationSource.DOM, "title", "ChatGPT"),
        Evidence(ObservationSource.DOM, "element:#prompt-textarea", True),
        Evidence(ObservationSource.DOM, "element:[role='dialog']", False),
        Evidence(ObservationSource.DOM, "body_text_sample", "ChatGPT 5.2 Thinking 送信 質問してみましょう"),
    ]
    
    print("\n--- ケース1: ChatGPT準備完了 ---")
    estimate = estimator.estimate_state(dom_evidences, screen_key="chatgpt.com/c/*")
    print(estimator.format_estimate(estimate))
    
    # ケース2: 生成中
    print("\n--- ケース2: 生成中 ---")
    dom_evidences_loading = [
        Evidence(ObservationSource.DOM, "url", "https://chatgpt.com/c/abc123"),
        Evidence(ObservationSource.DOM, "element:[aria-busy='true']", True),
        Evidence(ObservationSource.DOM, "body_text_sample", "Thinking... Stop generating"),
    ]
    estimate2 = estimator.estimate_state(dom_evidences_loading, screen_key="chatgpt.com/c/*")
    print(estimator.format_estimate(estimate2))
    
    # 差分
    print("\n--- 状態差分 ---")
    diff = estimator.get_state_diff()
    if diff:
        print(f"カテゴリ変化: {diff['category_changed']}")
        print(f"確度変化: {diff['confidence_delta']:+.1%}")
    
    print("\n" + "=" * 60)
    print("テスト完了")
