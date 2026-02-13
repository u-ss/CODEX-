# conditions.py - 事前・事後条件（Condition）の実装
# ChatGPT 5.2相談（ラリー2）に基づく実装

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, Tuple, Optional
import re


@dataclass
class CheckResult:
    """条件チェック結果"""
    ok: bool
    reason: str = ""
    observed: Dict[str, Any] = field(default_factory=dict)


class Condition(Protocol):
    """条件インターフェース"""
    name: str
    def check(self, ctx: Any) -> CheckResult: ...


# ============================================================
# 複合条件（AllOf / AnyOf）
# ============================================================

@dataclass
class AllOf:
    """全条件が満たされることを確認"""
    conds: Tuple[Condition, ...]
    name: str = "all_of"
    
    def check(self, ctx: Any) -> CheckResult:
        observed = {}
        for c in self.conds:
            r = c.check(ctx)
            observed[c.name] = {"ok": r.ok, "reason": r.reason, "observed": r.observed}
            if not r.ok:
                return CheckResult(False, reason=f"FAILED:{c.name}:{r.reason}", observed=observed)
        return CheckResult(True, reason="OK", observed=observed)


@dataclass
class AnyOf:
    """いずれかの条件が満たされることを確認"""
    conds: Tuple[Condition, ...]
    name: str = "any_of"
    
    def check(self, ctx: Any) -> CheckResult:
        observed = {}
        for c in self.conds:
            r = c.check(ctx)
            observed[c.name] = {"ok": r.ok, "reason": r.reason, "observed": r.observed}
            if r.ok:
                return CheckResult(True, reason=f"OK:{c.name}", observed=observed)
        return CheckResult(False, reason="NONE_MATCHED", observed=observed)


# ============================================================
# DOM条件（Playwright）
# ============================================================

@dataclass
class DomExists:
    """DOM要素が存在することを確認"""
    selector: str
    name: str = "dom_exists"
    
    def check(self, ctx: Any) -> CheckResult:
        page = getattr(ctx, "page", None)
        if not page:
            return CheckResult(False, "page_missing")
        try:
            el = page.query_selector(self.selector)
            exists = el is not None
            return CheckResult(exists, "not_found" if not exists else "found",
                             {"selector": self.selector, "exists": exists})
        except Exception as e:
            return CheckResult(False, "dom_check_exception",
                             {"selector": self.selector, "exc": repr(e)})


@dataclass
class DomNotExists:
    """DOM要素が存在しないことを確認（ダイアログ消失等）"""
    selector: str
    name: str = "dom_not_exists"
    
    def check(self, ctx: Any) -> CheckResult:
        page = getattr(ctx, "page", None)
        if not page:
            return CheckResult(True, "page_missing_treat_as_ok")
        try:
            el = page.query_selector(self.selector)
            not_exists = el is None
            return CheckResult(not_exists, "still_exists" if not not_exists else "gone",
                             {"selector": self.selector, "not_exists": not_exists})
        except Exception as e:
            return CheckResult(False, "dom_check_exception",
                             {"selector": self.selector, "exc": repr(e)})


@dataclass
class DomVisibleEnabled:
    """DOM要素が可視かつ有効であることを確認"""
    selector: str
    name: str = "dom_visible_enabled"
    
    def check(self, ctx: Any) -> CheckResult:
        page = getattr(ctx, "page", None)
        if not page:
            return CheckResult(False, "page_missing")
        try:
            el = page.query_selector(self.selector)
            if el is None:
                return CheckResult(False, "not_found", {"selector": self.selector})
            visible = el.is_visible()
            enabled = el.is_enabled()
            ok = visible and enabled
            return CheckResult(ok, "not_visible_or_disabled" if not ok else "ok",
                             {"selector": self.selector, "visible": visible, "enabled": enabled})
        except Exception as e:
            return CheckResult(False, "dom_check_exception",
                             {"selector": self.selector, "exc": repr(e)})


@dataclass
class DomTextContains:
    """DOM要素のテキストに指定文字列が含まれることを確認"""
    selector: str
    text: str
    name: str = "dom_text_contains"
    
    def check(self, ctx: Any) -> CheckResult:
        page = getattr(ctx, "page", None)
        if not page:
            return CheckResult(False, "page_missing")
        try:
            el = page.query_selector(self.selector)
            if el is None:
                return CheckResult(False, "not_found", {"selector": self.selector})
            inner = el.inner_text() or ""
            ok = self.text in inner
            return CheckResult(ok, "text_not_found" if not ok else "found",
                             {"selector": self.selector, "expected": self.text, "actual": inner[:200]})
        except Exception as e:
            return CheckResult(False, "dom_check_exception",
                             {"selector": self.selector, "exc": repr(e)})


# ============================================================
# URL条件
# ============================================================

@dataclass
class UrlContains:
    """URLに指定文字列が含まれることを確認"""
    substring: str
    name: str = "url_contains"
    
    def check(self, ctx: Any) -> CheckResult:
        page = getattr(ctx, "page", None)
        if not page:
            return CheckResult(False, "page_missing")
        url = getattr(page, "url", "") or ""
        ok = self.substring in url
        return CheckResult(ok, "not_found" if not ok else "found",
                         {"expected": self.substring, "url": url[:200]})


@dataclass
class UrlMatches:
    """URLが正規表現にマッチすることを確認"""
    pattern: str
    name: str = "url_matches"
    
    def check(self, ctx: Any) -> CheckResult:
        page = getattr(ctx, "page", None)
        if not page:
            return CheckResult(False, "page_missing")
        url = getattr(page, "url", "") or ""
        ok = re.search(self.pattern, url) is not None
        return CheckResult(ok, "url_not_match" if not ok else "match",
                         {"pattern": self.pattern, "url": url[:200]})


@dataclass
class UrlChanged:
    """URLが変わったことを確認"""
    original_url: str
    name: str = "url_changed"
    
    def check(self, ctx: Any) -> CheckResult:
        page = getattr(ctx, "page", None)
        if not page:
            return CheckResult(False, "page_missing")
        url = getattr(page, "url", "") or ""
        ok = url != self.original_url
        return CheckResult(ok, "unchanged" if not ok else "changed",
                         {"original": self.original_url[:100], "current": url[:100]})


@dataclass
class TitleContains:
    """ページタイトルに指定文字列が含まれることを確認"""
    substring: str
    name: str = "title_contains"
    
    def check(self, ctx: Any) -> CheckResult:
        page = getattr(ctx, "page", None)
        if not page:
            return CheckResult(False, "page_missing")
        title = page.title() or ""
        ok = self.substring in title
        return CheckResult(ok, "not_found" if not ok else "found",
                         {"expected": self.substring, "title": title[:100]})


# ============================================================
# UIA条件（pywinauto）
# ============================================================

@dataclass
class UiaExists:
    """UIA要素が存在することを確認"""
    spec: Dict[str, Any]
    timeout_s: float = 0.2
    name: str = "uia_exists"
    
    def check(self, ctx: Any) -> CheckResult:
        uia = getattr(ctx, "uia", None)
        if not uia:
            return CheckResult(False, "uia_missing")
        try:
            el = uia.child_window(**self.spec)
            exists = el.exists(timeout=self.timeout_s)
            return CheckResult(exists, "not_exists" if not exists else "exists",
                             {"spec": self.spec})
        except Exception as e:
            return CheckResult(False, "uia_check_exception",
                             {"spec": self.spec, "exc": repr(e)})


@dataclass
class UiaExistsEnabled:
    """UIA要素が存在かつ有効であることを確認"""
    spec: Dict[str, Any]
    timeout_s: float = 0.2
    name: str = "uia_exists_enabled"
    
    def check(self, ctx: Any) -> CheckResult:
        uia = getattr(ctx, "uia", None)
        if not uia:
            return CheckResult(False, "uia_missing")
        try:
            el = uia.child_window(**self.spec)
            exists = el.exists(timeout=self.timeout_s)
            if not exists:
                return CheckResult(False, "not_exists", {"spec": self.spec})
            enabled = bool(getattr(el, "is_enabled", lambda: True)())
            visible = bool(getattr(el, "is_visible", lambda: True)())
            ok = enabled and visible
            return CheckResult(ok, "disabled_or_hidden" if not ok else "ok",
                             {"spec": self.spec, "enabled": enabled, "visible": visible})
        except Exception as e:
            return CheckResult(False, "uia_check_exception",
                             {"spec": self.spec, "exc": repr(e)})


@dataclass
class NoModalDialog:
    """モーダルダイアログが存在しないことを確認"""
    dialog_spec: Dict[str, Any] = field(default_factory=lambda: {"class_name": "#32770"})
    name: str = "no_modal_dialog"
    
    def check(self, ctx: Any) -> CheckResult:
        uia = getattr(ctx, "uia", None)
        if not uia:
            return CheckResult(True, "uia_missing_treat_as_ok")
        try:
            dlg = uia.child_window(**self.dialog_spec)
            exists = dlg.exists(timeout=0.05)
            return CheckResult(not exists, "modal_exists" if exists else "no_modal",
                             {"dialog_spec": self.dialog_spec, "exists": exists})
        except Exception as e:
            return CheckResult(False, "dialog_check_exception",
                             {"exc": repr(e)})
