# -*- coding: utf-8 -*-
"""
Host Policy Engine — ホスト状態管理と取得不能分類
v3.0: 「エラー」と「取得不能」を分離し、代替探索フォールバックを支援
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional
from urllib.parse import urlparse


class HostState(Enum):
    """ホストの現在の状態"""
    OK = "ok"                   # 正常取得可能
    THROTTLED = "throttled"     # レート制限中（暫定的）
    BLOCKED = "blocked"         # アクセス拒否（永続的）
    JS_REQUIRED = "js_required" # JavaScript必須


@dataclass
class HostRecord:
    """ホストごとの履歴レコード"""
    host: str
    state: HostState = HostState.OK
    consecutive_403: int = 0
    total_attempts: int = 0
    total_success: int = 0
    total_blocked: int = 0
    last_status: int = 0
    last_attempt_time: float = 0.0
    block_reason: str = ""


@dataclass
class PolicyDecision:
    """ポリシー判定結果"""
    allowed: bool
    host: str
    state: HostState
    reason: str = ""


# 既知の取得困難ホスト（初期ブラックリスト）
KNOWN_BLOCKED_HOSTS = {
    "zhihu.com",
    "zhuanlan.zhihu.com",
    "www.zhihu.com",
    "dxy.cn",
    "www.dxy.cn",
    "weibo.com",
    "www.weibo.com",
    "mp.weixin.qq.com",
    "baidu.com",
    "www.baidu.com",
    "tieba.baidu.com",
}

# JS必須の既知ホスト
KNOWN_JS_REQUIRED_HOSTS = {
    "twitter.com",
    "x.com",
    "linkedin.com",
    "www.linkedin.com",
    "instagram.com",
    "www.instagram.com",
}


class HostPolicyEngine:
    """
    ホスト状態を追跡し、取得可否を判定するポリシーエンジン。

    - 403が3連続 → blocked
    - 429 → throttled（一定時間後にOK復帰）
    - 既知ブラックリスト → 初期blocked
    - JS必須ホスト → js_required
    """

    def __init__(
        self,
        block_threshold: int = 3,
        throttle_cooldown_sec: float = 60.0,
    ):
        self.block_threshold = block_threshold
        self.throttle_cooldown_sec = throttle_cooldown_sec
        self._hosts: Dict[str, HostRecord] = {}

    def _get_host(self, url: str) -> str:
        """URLからホスト名を抽出"""
        return urlparse(url).netloc.lower() if url else "unknown"

    def _get_record(self, host: str) -> HostRecord:
        """ホストレコードを取得（なければ作成）"""
        if host not in self._hosts:
            # 初期状態判定
            state = HostState.OK
            reason = ""
            if host in KNOWN_BLOCKED_HOSTS:
                state = HostState.BLOCKED
                reason = "known_blocked_host"
            elif host in KNOWN_JS_REQUIRED_HOSTS:
                state = HostState.JS_REQUIRED
                reason = "known_js_required"
            self._hosts[host] = HostRecord(
                host=host,
                state=state,
                block_reason=reason,
            )
        return self._hosts[host]

    def check(self, url: str) -> PolicyDecision:
        """
        URL取得前のポリシーチェック。

        Returns:
            PolicyDecision: 取得可否と理由
        """
        host = self._get_host(url)
        record = self._get_record(host)

        # blocked → 拒否
        if record.state == HostState.BLOCKED:
            return PolicyDecision(
                allowed=False,
                host=host,
                state=HostState.BLOCKED,
                reason=f"host_blocked: {record.block_reason or 'consecutive_403'}",
            )

        # js_required → JS未対応なら拒否
        if record.state == HostState.JS_REQUIRED:
            return PolicyDecision(
                allowed=False,
                host=host,
                state=HostState.JS_REQUIRED,
                reason="js_required",
            )

        # throttled → クールダウン期間中なら拒否
        if record.state == HostState.THROTTLED:
            elapsed = time.time() - record.last_attempt_time
            if elapsed < self.throttle_cooldown_sec:
                return PolicyDecision(
                    allowed=False,
                    host=host,
                    state=HostState.THROTTLED,
                    reason=f"throttled: {self.throttle_cooldown_sec - elapsed:.0f}s残",
                )
            # クールダウン完了 → OKに戻す
            record.state = HostState.OK
            record.consecutive_403 = 0

        return PolicyDecision(
            allowed=True,
            host=host,
            state=HostState.OK,
            reason="ok",
        )

    def report_result(self, url: str, status_code: int) -> None:
        """
        フェッチ結果をポリシーエンジンに報告。

        Args:
            url: フェッチしたURL
            status_code: HTTPステータスコード
        """
        host = self._get_host(url)
        record = self._get_record(host)
        record.total_attempts += 1
        record.last_status = status_code
        record.last_attempt_time = time.time()

        if status_code == 403:
            record.consecutive_403 += 1
            record.total_blocked += 1
            # 連続403がしきい値を超えたらblocked
            if record.consecutive_403 >= self.block_threshold:
                record.state = HostState.BLOCKED
                record.block_reason = f"consecutive_403_{record.consecutive_403}"
        elif status_code == 429:
            record.state = HostState.THROTTLED
            record.consecutive_403 = 0
        elif 200 <= status_code < 400:
            record.total_success += 1
            record.consecutive_403 = 0
            # 成功したらthrottled状態を解除
            if record.state == HostState.THROTTLED:
                record.state = HostState.OK

    def detect_js_required(self, url: str, content: str) -> bool:
        """
        コンテンツからJS必須を判定し、状態を更新。

        Args:
            url: フェッチしたURL
            content: 取得したHTMLコンテンツ

        Returns:
            True if JS required detected
        """
        if not content:
            return False

        # JS必須の兆候
        js_signals = [
            "__NEXT_DATA__",
            "__NUXT__",
            "window.__INITIAL_STATE__",
            'id="__next"',
            'id="app"',
        ]
        content_lower = content[:5000].lower()

        # HTMLが極端に短く、script比率が高い
        text_len = len(content.replace("<script", "").replace("</script>", ""))
        script_count = content_lower.count("<script")

        if script_count > 5 and text_len < 500:
            host = self._get_host(url)
            record = self._get_record(host)
            record.state = HostState.JS_REQUIRED
            record.block_reason = "content_js_heavy"
            return True

        for signal in js_signals:
            if signal.lower() in content_lower and text_len < 1000:
                host = self._get_host(url)
                record = self._get_record(host)
                record.state = HostState.JS_REQUIRED
                record.block_reason = f"js_signal:{signal}"
                return True

        return False

    def get_stats(self) -> Dict[str, Dict]:
        """全ホストの統計情報を返す"""
        stats = {}
        for host, record in self._hosts.items():
            stats[host] = {
                "state": record.state.value,
                "attempts": record.total_attempts,
                "success": record.total_success,
                "blocked": record.total_blocked,
                "consecutive_403": record.consecutive_403,
                "block_reason": record.block_reason,
            }
        return stats

    def get_blocked_hosts(self) -> list:
        """blockedホスト一覧を返す"""
        return [
            h for h, r in self._hosts.items()
            if r.state in (HostState.BLOCKED, HostState.JS_REQUIRED)
        ]
