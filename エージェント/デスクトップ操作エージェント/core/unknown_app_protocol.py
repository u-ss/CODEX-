"""
Unknown App Protocol - 未知アプリ対応プロトコル

状態遷移:
Monitor → UnknownDetected → FreezeAct → SurveyMode → AcquireUI
→ BuildHypothesis → AskUser → AllowOperate → Execute → Verify → Recover

ChatGPT相談（Rally 2）で設計した状態遷移を実装
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ProtocolState(Enum):
    """Unknown App Protocolの状態"""
    MONITOR = auto()           # 通常運用：既知アプリ/既知画面
    UNKNOWN_DETECTED = auto()  # 未知突入を確定
    FREEZE_ACT = auto()        # Act禁止・安全停止
    SURVEY_MODE = auto()       # 調査モード
    ACQUIRE_UI = auto()        # UI情報取得
    BUILD_HYPOTHESIS = auto()  # 操作候補の根拠化
    ASK_USER = auto()          # ユーザー承認
    ALLOW_OPERATE = auto()     # 操作許可：制限付き
    EXECUTE = auto()           # 1手だけ実行
    VERIFY = auto()            # 軽い検証
    ESCALATE = auto()          # 手段昇格/別手段
    COORD_CLICK_LAST = auto()  # 座標クリック最終手段
    BLOCK = auto()             # 自動操作禁止/中断
    RECOVER = auto()           # 復帰：既知運用へ


@dataclass
class UnknownDetectionResult:
    """未知判定結果"""
    is_unknown: bool
    reason: str
    confidence: float  # 0.0-1.0
    process_name: Optional[str] = None
    window_title: Optional[str] = None
    screen_key: Optional[str] = None


@dataclass
class UIEvidence:
    """UI情報の証拠"""
    source: str  # "UIA", "DOM", "SS_DIFF"
    elements: List[Dict[str, Any]] = field(default_factory=list)
    hierarchy: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ActionHypothesis:
    """操作候補の仮説"""
    action_type: str  # "click", "type", "scroll"
    target: Dict[str, Any]
    evidence: UIEvidence
    confidence: float
    expected_result: str
    risk_level: str  # "low", "medium", "high"


@dataclass
class UserApprovalRequest:
    """ユーザー承認リクエスト"""
    goal: str
    action: str
    target: str
    evidence: str
    expected_result: str
    risk: str
    options: List[str] = field(default_factory=lambda: ["承認", "拒否", "手動で実行", "中断"])


class UnknownAppProtocol:
    """未知アプリ対応プロトコル"""
    
    # 既知プロセスのAllowlist
    KNOWN_PROCESSES = {
        "chrome.exe", "msedge.exe", "firefox.exe",  # ブラウザ
        "notepad.exe", "explorer.exe",  # システム
        "code.exe", "idea64.exe",  # IDE
    }
    
    # 既知画面キーのRegistry（動的に更新）
    known_screen_keys: Dict[str, List[str]] = {}
    
    def __init__(self):
        self.state = ProtocolState.MONITOR
        self.ui_evidence: Optional[UIEvidence] = None
        self.hypothesis: Optional[ActionHypothesis] = None
        self.consecutive_failures = 0
        self.max_failures = 3
        self._state_history: List[tuple] = []
    
    def _transition(self, new_state: ProtocolState, reason: str = ""):
        """状態遷移"""
        old_state = self.state
        self.state = new_state
        self._state_history.append((datetime.now(), old_state, new_state, reason))
        logger.info(f"State: {old_state.name} → {new_state.name} ({reason})")
    
    def detect_unknown(
        self,
        process_name: str,
        window_title: str,
        screen_key: str,
        focus_changed_unexpectedly: bool = False,
        uia_elements_count: int = 0
    ) -> UnknownDetectionResult:
        """
        未知判定
        
        確定条件（いずれかで確定）:
        (U1) process_name が Allowlist に存在しない（初見プロセス）
        (U2) 既知プロセスだが screen_key が未登録（初見画面）
        (U3) 直近のアクション後に「想定外のフォーカス移動」
        (U4) 解決不能な不整合（UIAで要素探索が0件）
        """
        reasons = []
        confidence = 0.0
        
        # (U1) 初見プロセス
        if process_name not in self.KNOWN_PROCESSES:
            reasons.append(f"初見プロセス: {process_name}")
            confidence = max(confidence, 0.9)
        
        # (U2) 初見画面
        known_keys = self.known_screen_keys.get(process_name, [])
        if process_name in self.KNOWN_PROCESSES and screen_key not in known_keys:
            reasons.append(f"初見画面: {screen_key}")
            confidence = max(confidence, 0.7)
        
        # (U3) 想定外のフォーカス移動
        if focus_changed_unexpectedly:
            reasons.append("想定外のフォーカス移動")
            confidence = max(confidence, 0.8)
        
        # (U4) 解決不能な不整合
        if uia_elements_count == 0:
            reasons.append("UIAで要素探索が0件")
            confidence = max(confidence, 0.6)
        
        is_unknown = confidence >= 0.6
        
        return UnknownDetectionResult(
            is_unknown=is_unknown,
            reason=" / ".join(reasons) if reasons else "既知",
            confidence=confidence,
            process_name=process_name,
            window_title=window_title,
            screen_key=screen_key
        )
    
    def on_unknown_detected(self, result: UnknownDetectionResult):
        """未知検出時の処理"""
        if self.state != ProtocolState.MONITOR:
            return
        
        self._transition(ProtocolState.UNKNOWN_DETECTED, result.reason)
        self._transition(ProtocolState.FREEZE_ACT, "Act封印")
        self._transition(ProtocolState.SURVEY_MODE, "調査開始")
    
    def acquire_ui(
        self,
        uia_getter: Callable[[], List[Dict]],
        dom_getter: Optional[Callable[[], Dict]] = None,
        ss_differ: Optional[Callable[[], float]] = None
    ) -> Optional[UIEvidence]:
        """
        UI情報取得
        
        優先順位: UIA → DOM → SS差分
        """
        if self.state != ProtocolState.SURVEY_MODE:
            logger.warning(f"Invalid state for acquire_ui: {self.state}")
            return None
        
        self._transition(ProtocolState.ACQUIRE_UI, "UI取得開始")
        
        # UIA優先
        try:
            elements = uia_getter()
            if elements:
                self.ui_evidence = UIEvidence(
                    source="UIA",
                    elements=elements
                )
                self._transition(ProtocolState.BUILD_HYPOTHESIS, "UIA根拠取得")
                return self.ui_evidence
        except Exception as e:
            logger.warning(f"UIA取得失敗: {e}")
        
        # DOM fallback
        if dom_getter:
            try:
                dom = dom_getter()
                if dom:
                    self.ui_evidence = UIEvidence(
                        source="DOM",
                        hierarchy=dom
                    )
                    self._transition(ProtocolState.BUILD_HYPOTHESIS, "DOM根拠取得")
                    return self.ui_evidence
            except Exception as e:
                logger.warning(f"DOM取得失敗: {e}")
        
        # SS差分 fallback
        if ss_differ:
            try:
                diff = ss_differ()
                self.ui_evidence = UIEvidence(
                    source="SS_DIFF",
                    elements=[{"diff_percent": diff}]
                )
                if diff > 10:
                    self._transition(ProtocolState.ESCALATE, "UI取得不足")
                else:
                    self._transition(ProtocolState.BUILD_HYPOTHESIS, "SS差分で根拠構築")
                return self.ui_evidence
            except Exception as e:
                logger.warning(f"SS差分取得失敗: {e}")
        
        self._transition(ProtocolState.ESCALATE, "UI取得失敗")
        return None
    
    def build_hypothesis(
        self,
        goal: str,
        action_type: str,
        target: Dict[str, Any],
        expected_result: str
    ) -> Optional[ActionHypothesis]:
        """操作候補の根拠化"""
        if self.state != ProtocolState.BUILD_HYPOTHESIS:
            logger.warning(f"Invalid state for build_hypothesis: {self.state}")
            return None
        
        if not self.ui_evidence:
            self._transition(ProtocolState.ESCALATE, "証拠なし")
            return None
        
        # リスクレベル判定
        risk_level = "low"
        if action_type in ["submit", "purchase", "delete"]:
            risk_level = "high"
        elif action_type in ["click", "type"]:
            risk_level = "medium"
        
        self.hypothesis = ActionHypothesis(
            action_type=action_type,
            target=target,
            evidence=self.ui_evidence,
            confidence=0.7,  # 未知アプリなので控えめ
            expected_result=expected_result,
            risk_level=risk_level
        )
        
        # 初回操作または高リスク操作はAskUser
        if risk_level == "high":
            self._transition(ProtocolState.ASK_USER, "高リスク操作要承認")
        else:
            self._transition(ProtocolState.ASK_USER, "初回操作要承認")
        
        return self.hypothesis
    
    def create_approval_request(self) -> Optional[UserApprovalRequest]:
        """ユーザー承認リクエスト作成"""
        if self.state != ProtocolState.ASK_USER or not self.hypothesis:
            return None
        
        return UserApprovalRequest(
            goal=f"未知アプリで {self.hypothesis.action_type} を実行",
            action=f"{self.hypothesis.action_type}: {self.hypothesis.target}",
            target=str(self.hypothesis.target),
            evidence=f"{self.hypothesis.evidence.source}: {len(self.hypothesis.evidence.elements)}要素",
            expected_result=self.hypothesis.expected_result,
            risk=self.hypothesis.risk_level
        )
    
    def on_user_approval(self, approved: bool):
        """ユーザー承認結果"""
        if self.state != ProtocolState.ASK_USER:
            return
        
        if approved:
            self._transition(ProtocolState.ALLOW_OPERATE, "ユーザー承認")
        else:
            self._transition(ProtocolState.BLOCK, "ユーザー拒否")
    
    def execute_action(self, executor: Callable[[], bool]) -> bool:
        """1手だけ実行"""
        if self.state != ProtocolState.ALLOW_OPERATE:
            logger.warning(f"Invalid state for execute: {self.state}")
            return False
        
        self._transition(ProtocolState.EXECUTE, "1手実行")
        
        try:
            success = executor()
            self._transition(ProtocolState.VERIFY, "即時検証")
            return success
        except Exception as e:
            logger.error(f"Execute failed: {e}")
            self._transition(ProtocolState.ESCALATE, f"実行失敗: {e}")
            return False
    
    def verify_result(
        self,
        success_checker: Callable[[], bool],
        stable_checker: Optional[Callable[[], bool]] = None
    ) -> bool:
        """軽い検証"""
        if self.state != ProtocolState.VERIFY:
            return False
        
        success = success_checker()
        
        if success:
            is_stable = stable_checker() if stable_checker else True
            if is_stable:
                self._transition(ProtocolState.RECOVER, "成功＋安定")
                self.consecutive_failures = 0
                return True
            else:
                self._transition(ProtocolState.ALLOW_OPERATE, "成功だが継続は制限")
                return True
        else:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.max_failures:
                self._transition(ProtocolState.ESCALATE, f"連続失敗{self.consecutive_failures}回")
            else:
                self._transition(ProtocolState.ESCALATE, "失敗/不確実")
            return False
    
    def try_coord_click_last_resort(
        self,
        has_anchor: bool,
        has_verify_method: bool
    ) -> bool:
        """座標クリック最終手段の発動条件チェック"""
        if self.state != ProtocolState.ESCALATE:
            return False
        
        # 許可条件：アンカー画像/固定領域があり、検証方法がある
        if has_anchor and has_verify_method:
            self._transition(ProtocolState.COORD_CLICK_LAST, "最終手段条件満たす")
            return True
        else:
            self._transition(ProtocolState.BLOCK, "座標クリック許可条件満たさず")
            return False
    
    def recover(self, screen_key: str, process_name: str):
        """復帰：既知運用へ"""
        if self.state == ProtocolState.RECOVER:
            # 今回成功した画面を既知として登録
            if process_name not in self.known_screen_keys:
                self.known_screen_keys[process_name] = []
            if screen_key not in self.known_screen_keys[process_name]:
                self.known_screen_keys[process_name].append(screen_key)
                logger.info(f"新規画面登録: {process_name} / {screen_key}")
            
            self._transition(ProtocolState.MONITOR, "既知運用復帰")
    
    def get_state_history(self) -> List[tuple]:
        """状態遷移履歴を取得"""
        return self._state_history.copy()


# 使用例
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    protocol = UnknownAppProtocol()
    
    # 1. 未知判定
    result = protocol.detect_unknown(
        process_name="unknown_app.exe",
        window_title="Unknown Window",
        screen_key="unknown_screen_001",
        uia_elements_count=5
    )
    print(f"未知判定: {result}")
    
    if result.is_unknown:
        # 2. 未知検出時の処理
        protocol.on_unknown_detected(result)
        
        # 3. UI情報取得
        def mock_uia_getter():
            return [{"name": "Button1", "type": "Button"}]
        
        evidence = protocol.acquire_ui(uia_getter=mock_uia_getter)
        
        # 4. 仮説構築
        hypothesis = protocol.build_hypothesis(
            goal="ボタンをクリック",
            action_type="click",
            target={"name": "Button1"},
            expected_result="画面遷移"
        )
        
        # 5. 承認リクエスト作成
        request = protocol.create_approval_request()
        print(f"承認リクエスト: {request}")
        
        # 6. 承認（シミュレート）
        protocol.on_user_approval(approved=True)
        
        # 7. 実行
        def mock_executor():
            return True
        
        protocol.execute_action(mock_executor)
        
        # 8. 検証
        def mock_success_checker():
            return True
        
        protocol.verify_result(mock_success_checker)
        
        # 9. 復帰
        protocol.recover("unknown_screen_001", "unknown_app.exe")
    
    # 履歴表示
    print("\n状態遷移履歴:")
    for ts, old, new, reason in protocol.get_state_history():
        print(f"  {ts.strftime('%H:%M:%S')} {old.name} → {new.name}: {reason}")
