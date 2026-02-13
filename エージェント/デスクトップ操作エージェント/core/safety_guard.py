# safety_guard.py - 安全設計（Human-in-the-loop + 機密マスク）
# ChatGPT 5.2相談（ラリー3）に基づく実装

from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable, Dict, List
import re
import hashlib


class RiskLevel(IntEnum):
    """リスクレベル"""
    LOW = 10
    MED = 50
    HIGH = 90


# 破壊的操作を示すキーワード
DESTRUCTIVE_WORDS = [
    "delete", "remove", "destroy", "erase", "drop",
    "購入", "決済", "支払い", "送信", "submit", "confirm", "確定",
    "退会", "解約", "削除", "消去", "破棄", "フォーマット",
    "install", "uninstall", "reset", "clear", "terminate",
]


def risk_from_action(ac: Any) -> RiskLevel:
    """アクションから明示的なリスクヒントを取得"""
    hint = ac.params.get("risk_hint")
    if hint in ("HIGH", RiskLevel.HIGH):
        return RiskLevel.HIGH
    if hint in ("MED", RiskLevel.MED):
        return RiskLevel.MED
    return RiskLevel.LOW


def risk_heuristic(ctx: Any, ac: Any) -> RiskLevel:
    """ロケータやラベルから破壊的操作を推定"""
    s = ""
    p = ac.params
    
    # ロケータ情報を収集
    if "selector" in p.get("locator", {}):
        s += p["locator"]["selector"] + " "
    if "uia" in p.get("locator", {}):
        s += str(p["locator"]["uia"]) + " "
    
    # DOMからテキスト/aria-labelを取得（失敗しても続行）
    try:
        page = getattr(ctx, "page", None)
        if page and "locator" in p and "selector" in p["locator"]:
            loc = page.locator(p["locator"]["selector"])
            s += (loc.inner_text() or "") + " "
            s += (loc.get_attribute("aria-label") or "") + " "
    except Exception:
        pass
    
    s_low = s.lower()
    if any(w in s_low for w in DESTRUCTIVE_WORDS):
        return RiskLevel.HIGH
    return RiskLevel.LOW


# ============================================================
# Human-in-the-loop
# ============================================================

@dataclass
class ConfirmRequest:
    """確認リクエスト"""
    reason: str
    summary: str
    detail: Dict[str, Any]


class HumanConfirmer:
    """
    Human-in-the-loopインターフェース
    
    実装例:
    - Discord通知→承認待ち
    - デスクトップ通知→クリック待ち
    - コンソール入力待ち
    """
    
    def request(self, req: ConfirmRequest) -> str:
        """確認をリクエストし、リクエストIDを返す"""
        raise NotImplementedError
    
    def wait_approve(self, request_id: str, timeout_s: float = 120.0) -> bool:
        """承認を待つ"""
        raise NotImplementedError


class ConsoleConfirmer(HumanConfirmer):
    """コンソールベースの確認（開発/テスト用）"""
    
    def __init__(self, auto_approve: bool = False):
        self.auto_approve = auto_approve
        self._counter = 0
    
    def request(self, req: ConfirmRequest) -> str:
        self._counter += 1
        rid = f"req_{self._counter}"
        print(f"\n[CONFIRM] {req.reason}: {req.summary}")
        print(f"  Detail: {req.detail}")
        return rid
    
    def wait_approve(self, request_id: str, timeout_s: float = 120.0) -> bool:
        if self.auto_approve:
            print(f"  [AUTO-APPROVED] {request_id}")
            return True
        
        try:
            ans = input("  Approve? (y/n): ")
            return ans.strip().lower() in ("y", "yes")
        except Exception:
            return False


class SafetyGuardError(Exception):
    """安全ガードエラー"""
    pass


def safety_guard(ctx: Any, ac: Any) -> None:
    """
    高リスク操作をブロックし、ユーザー確認を要求
    
    使い方: Runner実行直前に呼び出す
    """
    r = max(risk_from_action(ac), risk_heuristic(ctx, ac))
    if r < RiskLevel.HIGH:
        return
    
    confirmer: HumanConfirmer = getattr(ctx, "confirmer", None)
    if not confirmer:
        raise SafetyGuardError("HIGH_RISK action blocked: confirmer not configured")
    
    req = ConfirmRequest(
        reason="destructive_action",
        summary=f"High risk action: {ac.kind}",
        detail={"params": ac.params, "screen_key": getattr(ctx, "screen_key", "unknown")},
    )
    rid = confirmer.request(req)
    approved = confirmer.wait_approve(rid, timeout_s=120.0)
    if not approved:
        raise SafetyGuardError("User rejected HIGH_RISK action")


# ============================================================
# 機密情報マスク
# ============================================================

# 機密パターン
SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9]{20,})"),                                      # OpenAI API key
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*[A-Za-z0-9\-_]{8,})"),              # 一般的なAPIキー
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+[A-Za-z0-9.\-_]+)"),     # Bearerトークン
    re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"),           # メールアドレス
    re.compile(r"(?i)(password\s*[:=]\s*\S+)"),                                  # パスワード
    re.compile(r"(?i)(secret\s*[:=]\s*\S+)"),                                    # シークレット
]


def mask_text(s: str) -> str:
    """機密情報をマスク"""
    out = s
    for pat in SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def sanitize_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """辞書内の機密情報を再帰的にマスク"""
    def _sanitize(x):
        if isinstance(x, str):
            return mask_text(x)
        if isinstance(x, dict):
            return {k: _sanitize(v) for k, v in x.items()}
        if isinstance(x, list):
            return [_sanitize(v) for v in x]
        return x
    return _sanitize(d)


def redact_type_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """typeアクションのテキストを安全にマスク"""
    if "text" not in params:
        return params
    text = params["text"]
    return {
        **params,
        "text": "[REDACTED]",
        "text_len": len(text),
        "text_sha1": hashlib.sha1(text.encode("utf-8")).hexdigest()[:10],
    }


def safe_trace_log(trace: Any, **evt) -> None:
    """機密をマスクしてトレースログに記録"""
    evt = sanitize_dict(evt)
    trace.log(**evt)
