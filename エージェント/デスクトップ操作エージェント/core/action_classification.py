"""
Action Classification - Allow/Ask/Block分類

ChatGPT相談（Rally 3）で設計したアクション分類を実装

分類:
- Allow: 自動実行OK（低リスク、読み取り系）
- Ask: ユーザー承認必須（初回操作、重要操作、送信/購入）
- Block: 絶対禁止（危険操作、BOT判定リスク高）
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ActionPermission(Enum):
    """アクション許可レベル"""
    ALLOW = auto()   # 自動実行OK
    ASK = auto()     # ユーザー承認必須
    BLOCK = auto()   # 絶対禁止


class ActionCategory(Enum):
    """アクションカテゴリ"""
    # Allow系
    READ = "read"           # 読み取り
    SCROLL = "scroll"       # スクロール
    FOCUS = "focus"         # フォーカス移動
    NAVIGATE = "navigate"   # ページ遷移（同一ドメイン）
    
    # Ask系
    CLICK = "click"         # クリック
    TYPE = "type"           # 文字入力
    SUBMIT = "submit"       # 送信
    UPLOAD = "upload"       # ファイルアップロード
    DOWNLOAD = "download"   # ファイルダウンロード
    
    # Block系
    PURCHASE = "purchase"   # 購入
    DELETE = "delete"       # 削除
    ACCOUNT = "account"     # アカウント操作
    LOGIN = "login"         # ログイン
    EXTERNAL = "external"   # 外部サイト遷移


@dataclass
class ActionContext:
    """アクションのコンテキスト"""
    action_type: ActionCategory
    target: Dict[str, Any]
    app_name: str
    screen_key: str
    is_unknown_app: bool = False
    is_first_time: bool = False
    has_side_effect: bool = False
    risk_level: str = "low"  # low, medium, high
    evidence_source: str = ""  # UIA, DOM, SS
    additional_info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AskRequest:
    """ユーザー承認リクエスト（Askカード）"""
    goal: str
    action: str
    target: str
    app_name: str
    screen_key: str
    evidence: str
    expected_result: str
    risk: str
    verify_method: str
    options: List[str] = field(default_factory=lambda: [
        "承認(1回)", "拒否", "手動で実行", "中断"
    ])
    created_at: datetime = field(default_factory=datetime.now)
    timeout_seconds: int = 60


class ActionClassifier:
    """アクション分類器"""
    
    # Allow: 自動実行OK
    ALLOW_ACTIONS: Set[ActionCategory] = {
        ActionCategory.READ,
        ActionCategory.SCROLL,
        ActionCategory.FOCUS,
    }
    
    # Block: 絶対禁止
    BLOCK_ACTIONS: Set[ActionCategory] = {
        ActionCategory.PURCHASE,
        ActionCategory.DELETE,
        ActionCategory.ACCOUNT,
    }
    
    # 高リスクキーワード（要Block）
    HIGH_RISK_KEYWORDS: Set[str] = {
        "purchase", "buy", "order", "checkout", "payment",
        "delete", "remove", "destroy", "clear all",
        "unsubscribe", "deactivate", "close account",
        "confirm delete", "確認", "削除", "購入", "注文",
    }
    
    # 外部サイトドメイン（要Ask）
    EXTERNAL_DOMAINS: Set[str] = {
        "paypal.com", "stripe.com", "apple.com",
        "google.com/accounts",
    }
    
    def __init__(self):
        self._ask_history: List[AskRequest] = []
        self._pending_ask: Optional[AskRequest] = None
    
    def classify(self, context: ActionContext) -> ActionPermission:
        """
        アクションを分類
        
        ルール:
        1. Block対象は即Block
        2. 未知アプリの初回操作はAsk
        3. 高リスクキーワード含むはBlock
        4. Allow対象はAllow
        5. それ以外はAsk
        """
        # 1. Block対象チェック
        if context.action_type in self.BLOCK_ACTIONS:
            logger.info(f"BLOCK: {context.action_type.value} は禁止カテゴリ")
            return ActionPermission.BLOCK
        
        # 2. 高リスクキーワードチェック
        target_text = str(context.target).lower()
        for keyword in self.HIGH_RISK_KEYWORDS:
            if keyword in target_text:
                logger.info(f"BLOCK: 高リスクキーワード '{keyword}' 検出")
                return ActionPermission.BLOCK
        
        # 3. 未知アプリの初回操作
        if context.is_unknown_app and context.is_first_time:
            logger.info(f"ASK: 未知アプリ '{context.app_name}' の初回操作")
            return ActionPermission.ASK
        
        # 4. Allow対象チェック
        if context.action_type in self.ALLOW_ACTIONS:
            if context.risk_level == "low":
                logger.info(f"ALLOW: {context.action_type.value} は自動許可カテゴリ")
                return ActionPermission.ALLOW
        
        # 5. 副作用ありはAsk
        if context.has_side_effect:
            logger.info(f"ASK: 副作用あり")
            return ActionPermission.ASK
        
        # 6. デフォルトはAsk
        logger.info(f"ASK: デフォルト（安全側に倒す）")
        return ActionPermission.ASK
    
    def create_ask_request(
        self,
        context: ActionContext,
        goal: str,
        expected_result: str,
        verify_method: str
    ) -> AskRequest:
        """Askリクエスト（ユーザー承認カード）作成"""
        request = AskRequest(
            goal=goal,
            action=f"{context.action_type.value}: {context.target}",
            target=str(context.target),
            app_name=context.app_name,
            screen_key=context.screen_key,
            evidence=f"{context.evidence_source}: {context.additional_info}",
            expected_result=expected_result,
            risk=context.risk_level,
            verify_method=verify_method
        )
        self._pending_ask = request
        self._ask_history.append(request)
        return request
    
    def process_user_response(self, response: str) -> ActionPermission:
        """ユーザーレスポンス処理"""
        if not self._pending_ask:
            return ActionPermission.BLOCK
        
        response = response.lower()
        self._pending_ask = None
        
        if response in ["承認", "承認(1回)", "yes", "approve"]:
            return ActionPermission.ALLOW
        elif response in ["拒否", "no", "reject"]:
            return ActionPermission.BLOCK
        elif response in ["手動で実行", "manual"]:
            # 手動実行→観測モードに移行
            return ActionPermission.BLOCK
        elif response in ["中断", "abort", "cancel"]:
            return ActionPermission.BLOCK
        else:
            # 不明なレスポンスは安全側
            return ActionPermission.BLOCK
    
    def on_timeout(self) -> ActionPermission:
        """承認タイムアウト時の処理"""
        logger.warning("ASK timeout → Safe Idle")
        self._pending_ask = None
        return ActionPermission.BLOCK
    
    def get_ask_history(self) -> List[AskRequest]:
        """Ask履歴取得"""
        return self._ask_history.copy()


# サーキットブレーカ閾値設定（Rally 3より）
@dataclass
class CircuitBreakerConfig:
    """サーキットブレーカ設定"""
    # 局所（同一要素）
    same_element_max_failures: int = 2
    # 局所（同一手段）
    same_method_max_failures: int = 3
    # 座標クリック
    coord_click_max_failures: int = 1
    # 全体（Unknown状態）
    unknown_total_max_failures: int = 5
    # タイムウィンドウ
    time_window_seconds: int = 60
    time_window_max_failures: int = 3


# 使用例
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    classifier = ActionClassifier()
    
    # テスト1: 読み取り（Allow）
    ctx1 = ActionContext(
        action_type=ActionCategory.READ,
        target={"element": "text_area"},
        app_name="notepad.exe",
        screen_key="main",
        risk_level="low"
    )
    result1 = classifier.classify(ctx1)
    print(f"Test 1 - READ: {result1.name}")
    
    # テスト2: 未知アプリ初回クリック（Ask）
    ctx2 = ActionContext(
        action_type=ActionCategory.CLICK,
        target={"button": "Submit"},
        app_name="unknown.exe",
        screen_key="form",
        is_unknown_app=True,
        is_first_time=True
    )
    result2 = classifier.classify(ctx2)
    print(f"Test 2 - Unknown App Click: {result2.name}")
    
    # テスト3: 購入（Block）
    ctx3 = ActionContext(
        action_type=ActionCategory.PURCHASE,
        target={"button": "Buy Now"},
        app_name="shop.exe",
        screen_key="checkout"
    )
    result3 = classifier.classify(ctx3)
    print(f"Test 3 - Purchase: {result3.name}")
    
    # テスト4: 削除キーワード検出（Block）
    ctx4 = ActionContext(
        action_type=ActionCategory.CLICK,
        target={"button": "Delete All Files"},
        app_name="file_manager.exe",
        screen_key="main"
    )
    result4 = classifier.classify(ctx4)
    print(f"Test 4 - Delete keyword: {result4.name}")
    
    # テスト5: Askリクエスト作成
    ctx5 = ActionContext(
        action_type=ActionCategory.SUBMIT,
        target={"form": "contact_form"},
        app_name="browser",
        screen_key="contact_page",
        has_side_effect=True,
        evidence_source="DOM"
    )
    result5 = classifier.classify(ctx5)
    print(f"Test 5 - Submit: {result5.name}")
    
    if result5 == ActionPermission.ASK:
        ask_req = classifier.create_ask_request(
            ctx5,
            goal="お問い合わせフォームを送信",
            expected_result="送信完了画面に遷移",
            verify_method="URL変更を確認"
        )
        print(f"Ask Request: {ask_req.goal}")
    
    print("\n✅ 全テスト完了")
