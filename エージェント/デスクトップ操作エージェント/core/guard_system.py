"""
Guard System（危険検知前段ガード）

目的: Unsafe/Askを乱発させずに、安全を担保する

チェック内容:
- 今フォーカスは正しいか
- 対象アプリは想定のものか
- 入力先はパスワード欄ではないか
- 破壊的操作（削除/送信/購入）が近いか

ChatGPT 5.2フィードバック（2026-02-05）より
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable
import re


class GuardResult(Enum):
    """ガードチェック結果"""
    PASS = "pass"           # 通過、実行OK
    WARN = "warn"           # 警告、続行可能だが注意
    BLOCK = "block"         # ブロック、実行不可（ユーザー確認必要）
    ABORT = "abort"         # 中止、絶対に実行しない


@dataclass
class GuardCheckResult:
    """ガードチェック結果詳細"""
    result: GuardResult
    guard_name: str           # どのガードか
    message: str              # 理由
    suggestion: Optional[str] = None  # 対処法
    details: Optional[dict] = None    # 追加情報


@dataclass
class ExecutionContext:
    """実行コンテキスト"""
    
    # ウィンドウ情報
    expected_app: str           # 期待するアプリ名
    current_app: Optional[str] = None   # 現在のアプリ
    expected_window_title: Optional[str] = None
    current_window_title: Optional[str] = None
    
    # フォーカス
    is_foreground: bool = True
    
    # 入力先
    target_element_type: Optional[str] = None  # text, password, button等
    target_element_name: Optional[str] = None
    
    # アクション
    action_type: str = ""         # Click, TypeText, etc
    action_target_text: Optional[str] = None  # ボタンのテキスト等
    
    # 状態
    screen_key: str = ""
    modal_present: bool = False


class Guard:
    """ガードの基底クラス"""
    
    name: str = "BaseGuard"
    description: str = ""
    
    def check(self, ctx: ExecutionContext) -> GuardCheckResult:
        """チェック実行（サブクラスでオーバーライド）"""
        raise NotImplementedError


class FocusGuard(Guard):
    """フォーカスガード - 正しいウィンドウにフォーカスがあるか"""
    
    name = "FocusGuard"
    description = "対象ウィンドウにフォーカスがあるか確認"
    
    def check(self, ctx: ExecutionContext) -> GuardCheckResult:
        if not ctx.is_foreground:
            return GuardCheckResult(
                result=GuardResult.WARN,
                guard_name=self.name,
                message="対象ウィンドウがフォアグラウンドにありません",
                suggestion="UIA/pyautoguiでフォーカスを戻してから再試行"
            )
        
        if ctx.current_app and ctx.expected_app:
            if ctx.current_app.lower() != ctx.expected_app.lower():
                return GuardCheckResult(
                    result=GuardResult.BLOCK,
                    guard_name=self.name,
                    message=f"期待するアプリ({ctx.expected_app})と現在のアプリ({ctx.current_app})が異なります",
                    suggestion="正しいウィンドウに切り替えてください"
                )
        
        return GuardCheckResult(
            result=GuardResult.PASS,
            guard_name=self.name,
            message="フォーカスOK"
        )


class PasswordFieldGuard(Guard):
    """パスワード欄ガード - パスワード欄への入力を検出"""
    
    name = "PasswordFieldGuard"
    description = "パスワード欄への自動入力を防止"
    
    # パスワード欄を示唆するパターン
    PASSWORD_PATTERNS = [
        r"password",
        r"passwd",
        r"pwd",
        r"秘密",
        r"パスワード",
        r"暗証",
    ]
    
    def check(self, ctx: ExecutionContext) -> GuardCheckResult:
        # TypeText以外は関係ない
        if ctx.action_type.lower() != "typetext":
            return GuardCheckResult(
                result=GuardResult.PASS,
                guard_name=self.name,
                message="入力アクションではない"
            )
        
        # 要素タイプがpassword
        if ctx.target_element_type and ctx.target_element_type.lower() == "password":
            return GuardCheckResult(
                result=GuardResult.ABORT,
                guard_name=self.name,
                message="パスワード欄への自動入力は禁止されています",
                suggestion="パスワードは手動で入力してください"
            )
        
        # 要素名にパスワード系の文字が含まれる
        if ctx.target_element_name:
            for pattern in self.PASSWORD_PATTERNS:
                if re.search(pattern, ctx.target_element_name, re.IGNORECASE):
                    return GuardCheckResult(
                        result=GuardResult.BLOCK,
                        guard_name=self.name,
                        message=f"パスワード欄の可能性があります: {ctx.target_element_name}",
                        suggestion="本当にこの欄に入力しますか？確認してください"
                    )
        
        return GuardCheckResult(
            result=GuardResult.PASS,
            guard_name=self.name,
            message="パスワード欄ではない"
        )


class DestructiveActionGuard(Guard):
    """破壊的操作ガード - 削除/送信/購入等を検出"""
    
    name = "DestructiveActionGuard"
    description = "破壊的または取り消し不能な操作を検出"
    
    # 破壊的操作のパターン
    DESTRUCTIVE_PATTERNS = {
        "delete": ["delete", "remove", "削除", "消去", "取り消し"],
        "send": ["send", "submit", "送信", "投稿", "公開"],
        "purchase": ["purchase", "buy", "pay", "購入", "支払い", "決済", "注文"],
        "confirm": ["confirm", "execute", "実行", "確定", "完了"],
        "close": ["close account", "アカウント削除", "退会"],
    }
    
    # 高リスク操作（ABORT）
    HIGH_RISK = ["purchase", "close"]
    
    # 中リスク操作（BLOCK）
    MEDIUM_RISK = ["delete", "send", "confirm"]
    
    def check(self, ctx: ExecutionContext) -> GuardCheckResult:
        # Clickアクションのみ対象
        if ctx.action_type.lower() != "click":
            return GuardCheckResult(
                result=GuardResult.PASS,
                guard_name=self.name,
                message="クリックアクションではない"
            )
        
        target_text = (ctx.action_target_text or "").lower()
        
        for category, patterns in self.DESTRUCTIVE_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in target_text:
                    if category in self.HIGH_RISK:
                        return GuardCheckResult(
                            result=GuardResult.ABORT,
                            guard_name=self.name,
                            message=f"高リスク操作を検出: {category} ({ctx.action_target_text})",
                            suggestion="この操作は自動実行できません。手動で確認してください",
                            details={"category": category, "pattern": pattern}
                        )
                    elif category in self.MEDIUM_RISK:
                        return GuardCheckResult(
                            result=GuardResult.BLOCK,
                            guard_name=self.name,
                            message=f"破壊的操作を検出: {category} ({ctx.action_target_text})",
                            suggestion="実行前にユーザー確認が必要です",
                            details={"category": category, "pattern": pattern}
                        )
        
        return GuardCheckResult(
            result=GuardResult.PASS,
            guard_name=self.name,
            message="破壊的操作ではない"
        )


class ModalDialogGuard(Guard):
    """モーダルダイアログガード - モーダル状態を検出"""
    
    name = "ModalDialogGuard"
    description = "モーダルダイアログが表示されている状態を検出"
    
    def check(self, ctx: ExecutionContext) -> GuardCheckResult:
        if ctx.modal_present:
            return GuardCheckResult(
                result=GuardResult.WARN,
                guard_name=self.name,
                message="モーダルダイアログが表示されています",
                suggestion="ダイアログを処理してから続行してください"
            )
        
        return GuardCheckResult(
            result=GuardResult.PASS,
            guard_name=self.name,
            message="モーダルなし"
        )


class GuardSystem:
    """ガードシステム統合"""
    
    def __init__(self):
        self.guards: list[Guard] = [
            FocusGuard(),
            PasswordFieldGuard(),
            DestructiveActionGuard(),
            ModalDialogGuard(),
        ]
    
    def check_all(self, ctx: ExecutionContext) -> list[GuardCheckResult]:
        """全ガードを実行"""
        return [guard.check(ctx) for guard in self.guards]
    
    def can_execute(self, ctx: ExecutionContext) -> tuple[bool, list[GuardCheckResult]]:
        """実行可能か判定"""
        results = self.check_all(ctx)
        
        # ABORTがあれば即NG
        for r in results:
            if r.result == GuardResult.ABORT:
                return False, results
        
        # BLOCKがあればユーザー確認必要
        for r in results:
            if r.result == GuardResult.BLOCK:
                return False, results
        
        return True, results
    
    def get_worst_result(self, results: list[GuardCheckResult]) -> GuardResult:
        """最も重大な結果を取得"""
        priority = [GuardResult.ABORT, GuardResult.BLOCK, GuardResult.WARN, GuardResult.PASS]
        for level in priority:
            if any(r.result == level for r in results):
                return level
        return GuardResult.PASS
    
    def format_results(self, results: list[GuardCheckResult]) -> str:
        """結果をフォーマット"""
        lines = ["Guard System チェック結果:"]
        
        for r in results:
            icon = {
                GuardResult.PASS: "✅",
                GuardResult.WARN: "⚠️",
                GuardResult.BLOCK: "🚫",
                GuardResult.ABORT: "❌",
            }[r.result]
            
            lines.append(f"  {icon} [{r.guard_name}] {r.message}")
            if r.suggestion:
                lines.append(f"      → {r.suggestion}")
        
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("Guard System テスト")
    print("=" * 60)
    
    guard_system = GuardSystem()
    
    # テストケース1: 正常なクリック
    print("\n--- ケース1: 正常なクリック ---")
    ctx1 = ExecutionContext(
        expected_app="chrome.exe",
        current_app="chrome.exe",
        is_foreground=True,
        action_type="Click",
        action_target_text="Next"
    )
    can_exec, results = guard_system.can_execute(ctx1)
    print(f"実行可能: {can_exec}")
    print(guard_system.format_results(results))
    
    # テストケース2: パスワード欄への入力
    print("\n--- ケース2: パスワード欄への入力 ---")
    ctx2 = ExecutionContext(
        expected_app="chrome.exe",
        current_app="chrome.exe",
        is_foreground=True,
        action_type="TypeText",
        target_element_type="password",
        target_element_name="user-password"
    )
    can_exec, results = guard_system.can_execute(ctx2)
    print(f"実行可能: {can_exec}")
    print(guard_system.format_results(results))
    
    # テストケース3: 破壊的操作（購入）
    print("\n--- ケース3: 購入ボタンクリック ---")
    ctx3 = ExecutionContext(
        expected_app="chrome.exe",
        current_app="chrome.exe",
        is_foreground=True,
        action_type="Click",
        action_target_text="購入する"
    )
    can_exec, results = guard_system.can_execute(ctx3)
    print(f"実行可能: {can_exec}")
    print(guard_system.format_results(results))
    
    # テストケース4: フォーカス違い
    print("\n--- ケース4: 別アプリにフォーカス ---")
    ctx4 = ExecutionContext(
        expected_app="chrome.exe",
        current_app="notepad.exe",
        is_foreground=True,
        action_type="TypeText"
    )
    can_exec, results = guard_system.can_execute(ctx4)
    print(f"実行可能: {can_exec}")
    print(guard_system.format_results(results))
    
    # テストケース5: 送信ボタン
    print("\n--- ケース5: 送信ボタン ---")
    ctx5 = ExecutionContext(
        expected_app="chrome.exe",
        current_app="chrome.exe",
        is_foreground=True,
        action_type="Click",
        action_target_text="送信"
    )
    can_exec, results = guard_system.can_execute(ctx5)
    print(f"実行可能: {can_exec}")
    print(guard_system.format_results(results))
    
    print("\n" + "=" * 60)
    print("テスト完了")
