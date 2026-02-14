#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGI Kernel — Webhook通知モジュール (v0.6.1)

指数バックオフ + jitter / timeout / 冪等性キー / 429対応。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("agi_kernel")

# ── 定数 ──
_MAX_RETRIES = 3
_BASE_DELAY = 1.0       # 秒
_MAX_DELAY = 8.0        # 秒
_JITTER = 0.3           # 秒
_TIMEOUT = 10           # 秒


def _backoff_delay(attempt: int) -> float:
    """指数バックオフ + jitter を計算する。"""
    import random
    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
    return delay + random.uniform(0, _JITTER)


def send_webhook(
    url: str,
    payload: dict[str, Any],
    *,
    cycle_id: str = "",
    max_retries: int = _MAX_RETRIES,
    timeout: int = _TIMEOUT,
) -> bool:
    """サイクル結果をWebhook（Discord/Slack互換）で通知する。

    Args:
        url: WebhookエンドポイントURL。空文字列の場合は何もしない。
        payload: 通知内容（summary, status 等）。
        cycle_id: 冪等性キーの一部（重複送信抑止用）。
        max_retries: 最大リトライ回数。
        timeout: HTTP接続タイムアウト（秒）。

    Returns:
        True: 送信成功。False: 全リトライ失敗。
    """
    if not url:
        return False

    import urllib.request
    import urllib.error

    # 冪等性キー生成
    idempotency_key = f"{cycle_id}-{uuid.uuid4().hex[:8]}" if cycle_id else uuid.uuid4().hex

    # ペイロード構築（Discord/Slack互換）
    summary = payload.get("summary", "AGI Kernel 通知")
    description = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(description) > 2000:
        description = description[:1997] + "..."

    body = json.dumps({
        "content": summary,
        "embeds": [{"title": "AGI Kernel", "description": description}],
    }, ensure_ascii=False).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key,
    }

    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            resp = urllib.request.urlopen(req, timeout=timeout)
            status = resp.getcode()
            if status and 200 <= status < 300:
                logger.info(f"[WEBHOOK] 通知送信成功 (status={status}, key={idempotency_key})")
                return True
            # 2xx 以外は予期しないが成功扱い
            logger.warning(f"[WEBHOOK] 予期しないステータス: {status}")
            return True

        except urllib.error.HTTPError as e:
            if e.code == 429:
                # rate-limit: Retry-After ヘッダー尊重
                retry_after = e.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else _backoff_delay(attempt)
                logger.warning(f"[WEBHOOK] 429 Rate Limited — {wait:.1f}秒待機 (attempt={attempt+1})")
                if attempt < max_retries:
                    time.sleep(wait)
                    continue
            elif 500 <= e.code < 600:
                # サーバーエラー → リトライ
                wait = _backoff_delay(attempt)
                logger.warning(f"[WEBHOOK] サーバーエラー {e.code} — {wait:.1f}秒後リトライ (attempt={attempt+1})")
                if attempt < max_retries:
                    time.sleep(wait)
                    continue
            else:
                # 4xx (429以外) → リトライしない
                logger.warning(f"[WEBHOOK] HTTPエラー {e.code}: {e.reason}")
                return False

        except (urllib.error.URLError, OSError, TimeoutError) as e:
            # ネットワーク/タイムアウトエラー → リトライ
            wait = _backoff_delay(attempt)
            logger.warning(f"[WEBHOOK] 通信エラー: {e} — {wait:.1f}秒後リトライ (attempt={attempt+1})")
            if attempt < max_retries:
                time.sleep(wait)
                continue

        except Exception as e:
            # 予期しないエラー → ログして終了
            logger.warning(f"[WEBHOOK] 予期しないエラー: {e}")
            return False

    logger.warning(f"[WEBHOOK] 全{max_retries+1}回失敗 — 通知断念 (key={idempotency_key})")
    return False
