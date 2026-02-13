# failure_taxonomy.py - 失敗分類体系と回復戦略
# ChatGPT 5.2相談（ラリー3）に基づく実装

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import re
import time


class FailType(str, Enum):
    """失敗タイプの分類"""
    TRANSIENT = "TRANSIENT"           # 一時的（待てば治る）
    MISCLICK = "MISCLICK"             # クリック誤り（要素は見えたが遷移しない）
    MODAL_DIALOG = "MODAL_DIALOG"     # モーダルダイアログがブロック
    PERMISSION = "PERMISSION"         # 権限不足/UAC
    UI_UPDATE = "UI_UPDATE"           # UI更新によるセレクタ無効化
    NETWORK = "NETWORK"               # ネットワーク問題
    LOCATOR_STALE = "LOCATOR_STALE"   # ロケータが古い
    WRONG_STATE = "WRONG_STATE"       # 期待と異なる状態
    UNKNOWN = "UNKNOWN"               # 不明


@dataclass
class FailureEvent:
    """失敗イベント"""
    ts: float
    fail_type: FailType
    layer: str                   # "CDP" / "UIA" / "PIXEL"
    action_kind: str             # "click" / "type" / "wait"
    screen_key: str
    locator_version: str
    message: str
    exc_repr: str = ""
    observed: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)


class FailureClassifier:
    """
    失敗を分類する。
    
    分類順序:
    1. モーダル/UAC検出（最優先）
    2. 例外文字列パターン
    3. 観測（post条件）から推定
    """
    
    NETWORK_PAT = re.compile(
        r"(net::|ECONNRESET|ETIMEDOUT|Timeout|DNS|ERR_CONNECTION|ERR_NETWORK)",
        re.I
    )
    UI_UPDATE_PAT = re.compile(
        r"(stale|detached|not attached|element is not|UIA.*COM|NoSuchWindow|window handle)",
        re.I
    )
    PERMISSION_PAT = re.compile(
        r"(access is denied|permission denied|elevation|required admin|UAC)",
        re.I
    )
    
    def classify(
        self,
        ctx: Any,
        layer: str,
        action_kind: str,
        screen_key: str,
        locator_version: str,
        message: str,
        exc_repr: str,
        observed: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> FailureEvent:
        """失敗を分類してFailureEventを返す"""
        
        # 1) モーダル最優先
        if getattr(ctx, "has_uac_prompt", lambda: False)():
            ft = FailType.PERMISSION
        elif getattr(ctx, "has_modal_dialog", lambda: False)():
            ft = FailType.MODAL_DIALOG
        else:
            # 2) 例外パターン
            if self.PERMISSION_PAT.search(message) or self.PERMISSION_PAT.search(exc_repr):
                ft = FailType.PERMISSION
            elif self.NETWORK_PAT.search(message) or self.NETWORK_PAT.search(exc_repr):
                ft = FailType.NETWORK
            elif self.UI_UPDATE_PAT.search(message) or self.UI_UPDATE_PAT.search(exc_repr):
                ft = FailType.UI_UPDATE
            else:
                # 3) 観測から推定
                ft = self._from_observed(layer, observed, meta)
        
        return FailureEvent(
            ts=time.time(),
            fail_type=ft,
            layer=layer,
            action_kind=action_kind,
            screen_key=screen_key,
            locator_version=locator_version,
            message=message,
            exc_repr=exc_repr,
            observed=observed,
            meta=meta,
        )
    
    def _from_observed(
        self,
        layer: str,
        observed: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> FailType:
        """観測結果から失敗タイプを推定"""
        if not observed:
            return FailType.UNKNOWN
        
        url_ok = self._cond_ok(observed, "url_matches")
        dom_ok = self._cond_ok(observed, "dom_visible_enabled")
        uia_ok = self._cond_ok(observed, "uia_exists_enabled")
        
        # URLが変わっていない & 要素は見える → MISCLICK
        if (url_ok is False) and (dom_ok is True or uia_ok is True):
            return FailType.MISCLICK
        
        # 要素が見つからない → LOCATOR_STALE
        if (dom_ok is False) and (uia_ok is False):
            return FailType.LOCATOR_STALE
        
        # 画面は変わったが目的に到達しない → WRONG_STATE
        if url_ok is False and (dom_ok is False and uia_ok is True):
            return FailType.WRONG_STATE
        
        # それ以外は TRANSIENT
        return FailType.TRANSIENT
    
    @staticmethod
    def _cond_ok(observed: Dict[str, Any], key: str) -> Optional[bool]:
        x = observed.get(key)
        if not isinstance(x, dict):
            return None
        return x.get("ok")


# ============================================================
# 回復戦略（Tactic）
# ============================================================

Tactic = Callable[[Any, Any, Any], None]  # (ctx, action_contract, step_failure) -> None


def backoff_sleep_tactic(base: float = 0.4, factor: float = 2.0, cap: float = 5.0) -> Tactic:
    """指数バックオフで待機"""
    def _t(ctx, ac, f):
        n = getattr(f, "attempt", 1)
        s = min(cap, base * (factor ** max(0, n - 1)))
        time.sleep(s)
    return _t


def invalidate_locator_tactic() -> Tactic:
    """ロケータをパージ"""
    def _t(ctx, ac, f):
        lk = ac.params.get("locator_key")
        if lk and getattr(ctx, "locator_bank", None):
            ctx.locator_bank.purge(lk)
    return _t


def reresolve_locator_tactic() -> Tactic:
    """ロケータを再解決"""
    def _t(ctx, ac, f):
        lk = ac.params.get("locator_key")
        sk = getattr(ctx, "screen_key", None) or ac.params.get("screen_key")
        if lk and sk and getattr(ctx, "resolver", None):
            ac.params["locator"] = ctx.resolver.resolve(ctx, locator_key=lk, screen_key=sk)
    return _t


def reload_page_tactic() -> Tactic:
    """ページをリロード"""
    def _t(ctx, ac, f):
        if getattr(ctx, "page", None):
            ctx.page.reload()
    return _t


def handle_modal_tactic() -> Tactic:
    """モーダルダイアログを処理"""
    def _t(ctx, ac, f):
        if not getattr(ctx, "uia", None):
            return
        try:
            dlg = ctx.uia.child_window(class_name="#32770")
            if dlg.exists(timeout=0.1):
                for title in ("OK", "閉じる", "Close", "はい", "Yes"):
                    btn = dlg.child_window(title=title, control_type="Button")
                    if btn.exists(timeout=0.1):
                        btn.click_input()
                        return
        except Exception:
            pass
    return _t


def ask_user_permission_tactic() -> Tactic:
    """ユーザーに許可を求める"""
    def _t(ctx, ac, f):
        confirm = getattr(ctx, "confirm", None)
        if confirm:
            confirm.request(
                reason="permission_required",
                detail={"action": ac.kind, "params": ac.params}
            )
    return _t


# 失敗タイプ別の回復戦略
RECOVERY_PLAYBOOK: Dict[FailType, List[Tactic]] = {
    FailType.TRANSIENT:     [backoff_sleep_tactic()],
    FailType.NETWORK:       [backoff_sleep_tactic(), reload_page_tactic(), backoff_sleep_tactic()],
    FailType.MISCLICK:      [reresolve_locator_tactic(), invalidate_locator_tactic(), reresolve_locator_tactic()],
    FailType.LOCATOR_STALE: [invalidate_locator_tactic(), reresolve_locator_tactic()],
    FailType.UI_UPDATE:     [invalidate_locator_tactic(), backoff_sleep_tactic(), reresolve_locator_tactic()],
    FailType.MODAL_DIALOG:  [handle_modal_tactic(), backoff_sleep_tactic()],
    FailType.PERMISSION:    [ask_user_permission_tactic()],
    FailType.WRONG_STATE:   [reresolve_locator_tactic()],
    FailType.UNKNOWN:       [backoff_sleep_tactic()],
}
