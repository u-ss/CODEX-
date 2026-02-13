"""
ExecutionContext - モジュール間の「契約」

目的: 12モジュール間の入出力スキーマを統一し、呼び順の不変条件を保証

ChatGPT 5.2フィードバック（2026-02-05 Round2）より:
「モジュール間の"契約"が未固定だと、運用で必ず崩れる」

含まれる情報:
- screen_key（画面識別）
- action_id（アクション識別）
- layer（現在のレイヤー）
- フォーカス情報
- 入力/出力データ
- タイムスタンプ
- エラー情報
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any
from enum import Enum
import uuid


class ExecutionPhase(Enum):
    """実行フェーズ"""
    INIT = "init"           # 初期化
    PERCEIVE = "perceive"   # 観測
    DECIDE = "decide"       # 決定
    ACT = "act"             # 実行
    VERIFY = "verify"       # 検証
    RECOVER = "recover"     # 回復


class ContextState(Enum):
    """コンテキスト状態"""
    VALID = "valid"         # 有効
    STALE = "stale"         # 古い（再観測必要）
    INVALID = "invalid"     # 無効（エラー発生）


@dataclass
class ScreenInfo:
    """画面情報"""
    screen_key: str
    app_id: str
    window_title: Optional[str] = None
    url: Optional[str] = None
    is_modal: bool = False
    is_foreground: bool = True
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ActionPlan:
    """アクション計画"""
    action_id: str
    action_type: str          # Click, TypeText, Wait, etc
    target: dict              # セレクタ、座標等
    layer: str                # layer2+, layer3, layer1, layer0
    expected_result: Optional[str] = None
    timeout_ms: int = 5000
    allow_fallback: bool = True


@dataclass
class ActionResult:
    """アクション結果"""
    action_id: str
    success: bool
    duration_ms: int
    actual_layer: str         # 実際に使用したレイヤー
    error: Optional[str] = None
    fallback_used: bool = False
    output: Optional[dict] = None


@dataclass
class ExecutionContext:
    """
    実行コンテキスト - 全モジュール間で共有される「契約」
    
    使用順序:
    1. init() でコンテキスト作成
    2. update_screen() で画面情報更新
    3. set_plan() でアクション計画設定
    4. set_result() で結果記録
    5. 次のアクションへ
    """
    
    # 識別子
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    
    # 現在の状態
    phase: ExecutionPhase = ExecutionPhase.INIT
    state: ContextState = ContextState.VALID
    
    # 画面情報
    screen: Optional[ScreenInfo] = None
    previous_screen: Optional[ScreenInfo] = None
    
    # アクション
    current_plan: Optional[ActionPlan] = None
    last_result: Optional[ActionResult] = None
    action_history: list[ActionResult] = field(default_factory=list)
    
    # Guard結果
    guard_results: list[dict] = field(default_factory=list)
    
    # Circuit Breaker状態
    circuit_state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    failure_count: int = 0
    
    # メタデータ
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # エラー情報
    last_error: Optional[str] = None
    error_stack: list[str] = field(default_factory=list)
    
    def update_screen(self, screen: ScreenInfo) -> None:
        """画面情報を更新"""
        self.previous_screen = self.screen
        self.screen = screen
        self.updated_at = datetime.now().isoformat()
        self._check_staleness()
    
    def set_plan(self, plan: ActionPlan) -> None:
        """アクション計画を設定"""
        self.current_plan = plan
        self.phase = ExecutionPhase.DECIDE
        self.updated_at = datetime.now().isoformat()
    
    def set_result(self, result: ActionResult) -> None:
        """結果を記録"""
        self.last_result = result
        self.action_history.append(result)
        
        if result.success:
            self.failure_count = 0
            if self.circuit_state == "HALF_OPEN":
                self.circuit_state = "CLOSED"
        else:
            self.failure_count += 1
            self.error_stack.append(result.error or "Unknown error")
            if self.failure_count >= 3:
                self.circuit_state = "OPEN"
        
        self.phase = ExecutionPhase.VERIFY
        self.updated_at = datetime.now().isoformat()
    
    def set_guard_results(self, results: list[dict]) -> None:
        """Guardチェック結果を記録"""
        self.guard_results = results
    
    def mark_stale(self) -> None:
        """古いとマーク（再観測必要）"""
        self.state = ContextState.STALE
    
    def mark_invalid(self, error: str) -> None:
        """無効とマーク"""
        self.state = ContextState.INVALID
        self.last_error = error
    
    def _check_staleness(self) -> None:
        """新旧画面の変化をチェック"""
        if self.previous_screen and self.screen:
            if self.previous_screen.screen_key != self.screen.screen_key:
                # 画面が変わった
                self.phase = ExecutionPhase.PERCEIVE
    
    def is_valid(self) -> bool:
        """実行可能か"""
        return self.state == ContextState.VALID
    
    def is_circuit_open(self) -> bool:
        """Circuit Breakerが開いているか"""
        return self.circuit_state == "OPEN"
    
    def get_summary(self) -> dict:
        """サマリ取得"""
        return {
            "run_id": self.run_id,
            "phase": self.phase.value,
            "state": self.state.value,
            "screen_key": self.screen.screen_key if self.screen else None,
            "circuit_state": self.circuit_state,
            "failure_count": self.failure_count,
            "action_count": len(self.action_history),
            "success_count": sum(1 for a in self.action_history if a.success),
        }


class ContextManager:
    """コンテキスト管理"""
    
    def __init__(self):
        self.current: Optional[ExecutionContext] = None
        self.history: list[ExecutionContext] = []
    
    def create(self) -> ExecutionContext:
        """新しいコンテキストを作成"""
        if self.current:
            self.history.append(self.current)
        self.current = ExecutionContext()
        return self.current
    
    def get_current(self) -> Optional[ExecutionContext]:
        """現在のコンテキストを取得"""
        return self.current
    
    def require_valid_context(self) -> ExecutionContext:
        """有効なコンテキストを要求（なければ例外）"""
        if not self.current:
            raise RuntimeError("No active context")
        if not self.current.is_valid():
            raise RuntimeError(f"Context is {self.current.state.value}")
        return self.current


# グローバルインスタンス
_context_manager = ContextManager()


def get_context() -> Optional[ExecutionContext]:
    """現在のコンテキストを取得"""
    return _context_manager.get_current()


def create_context() -> ExecutionContext:
    """新しいコンテキストを作成"""
    return _context_manager.create()


def require_context() -> ExecutionContext:
    """有効なコンテキストを要求"""
    return _context_manager.require_valid_context()


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("ExecutionContext テスト")
    print("=" * 60)
    
    # コンテキスト作成
    ctx = create_context()
    print(f"\n1. 作成: run_id={ctx.run_id}")
    
    # 画面情報更新
    screen = ScreenInfo(
        screen_key="brave.exe|chatgpt.com/c/*",
        app_id="brave.exe",
        window_title="ChatGPT",
        url="https://chatgpt.com/c/abc123"
    )
    ctx.update_screen(screen)
    print(f"2. 画面更新: {ctx.screen.screen_key}")
    
    # アクション計画
    plan = ActionPlan(
        action_id="act_001",
        action_type="TypeText",
        target={"selector": "#prompt-textarea", "text": "テスト"},
        layer="layer2+"
    )
    ctx.set_plan(plan)
    print(f"3. 計画設定: {plan.action_type}")
    
    # 結果記録
    result = ActionResult(
        action_id="act_001",
        success=True,
        duration_ms=150,
        actual_layer="layer2+"
    )
    ctx.set_result(result)
    print(f"4. 結果記録: success={result.success}")
    
    # サマリ
    print(f"\nサマリ: {ctx.get_summary()}")
    
    # 失敗テスト
    print("\n--- 失敗テスト（Circuit Breaker）---")
    for i in range(4):
        fail_result = ActionResult(
            action_id=f"act_{i+2:03d}",
            success=False,
            duration_ms=5000,
            actual_layer="layer2+",
            error="Timeout"
        )
        ctx.set_result(fail_result)
        print(f"失敗{i+1}: circuit={ctx.circuit_state}, failures={ctx.failure_count}")
    
    print("\n" + "=" * 60)
    print("テスト完了")
