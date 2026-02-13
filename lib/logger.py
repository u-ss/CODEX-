"""
lib/logger.py — JSONL構造化ログ基盤

特徴:
  - JSONL（1行1JSON）形式
  - サイズベースローテーション（RotatingFileHandler）
  - 再帰的redact（秘密情報マスク）
  - 書き込み失敗時のフォールバック（stderr）
  - 標準ライブラリのみ

注意:
  - 単一プロセス前提（並行書き込みは前提外）

使い方:
  from lib.logger import setup_logger, info, warn, error, log_event

  setup_logger()              # デフォルト: _logs/app.jsonl
  info("user_login", user="taro")
  error("db_fail", err=Exception("timeout"), query="SELECT ...")
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import socket
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ───────────────────────── 定数 ─────────────────────────

# マスク対象キーパターン（大文字小文字無視）
_REDACT_PATTERN = re.compile(
    r"(password|passwd|pass|token|api_key|apikey|secret|authorization|cookie|credential)",
    re.IGNORECASE,
)
_REDACT_PLACEHOLDER = "***"

# デフォルト設定
DEFAULT_LOG_DIR = "_logs"
DEFAULT_LOG_FILE = "app.jsonl"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB
DEFAULT_MAX_FILES = 5

# ───────────────────────── Redact ─────────────────────────

def redact(obj: Any) -> Any:
    """
    再帰的にdict/listを走査し、秘密キーの値をマスクする。

    マスク対象キー: password, pass, token, api_key, secret,
                    authorization, cookie, credential（大文字小文字無視）

    元オブジェクトは変更せず、新しいオブジェクトを返す。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _REDACT_PATTERN.search(k):
                out[k] = _REDACT_PLACEHOLDER
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(item) for item in obj]
    return obj


# ───────────────────────── Formatter ─────────────────────────

class JsonlFormatter(logging.Formatter):
    """1行1JSONのログフォーマッター。共通フィールドを自動付与。"""

    def __init__(self) -> None:
        super().__init__()
        self._hostname = socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        # 基本フィールド
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "pid": os.getpid(),
            "host": self._hostname,
        }

        # 追加フィールド（log_event経由で渡されたもの）
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            entry.update(redact(extra))

        # エラー情報
        err = getattr(record, "err_obj", None)
        if err is not None:
            entry["error"] = {
                "type": type(err).__name__,
                "message": str(err),
                "traceback": traceback.format_exception(type(err), err, err.__traceback__) if err.__traceback__ else [],
            }

        return json.dumps(entry, ensure_ascii=False, default=str)


# ───────────────────────── フォールバックハンドラー ─────────────────────────

class _FallbackHandler(logging.Handler):
    """
    書き込み失敗時にstderrへ1回だけ警告するハンドラー。
    メインハンドラーがemit失敗したときのフォールバックとして使う。
    """

    def __init__(self) -> None:
        super().__init__()
        self._warned = False

    def emit(self, record: logging.LogRecord) -> None:
        if not self._warned:
            self._warned = True
            try:
                print(
                    f"[LOGGER FALLBACK] ログファイル書き込み失敗。stderr出力: {record.getMessage()}",
                    file=sys.stderr,
                )
            except Exception:
                pass


class _SafeRotatingHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandlerのemit失敗をキャッチしてフォールバックに委任。"""

    def __init__(self, *args: Any, fallback: _FallbackHandler, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._fallback = fallback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except Exception:
            self._fallback.emit(record)


# ───────────────────────── セットアップ ─────────────────────────

# モジュールレベルのloggerインスタンス
_logger: logging.Logger | None = None


def setup_logger(
    path: str | Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
    *,
    level: int = logging.DEBUG,
) -> logging.Logger:
    """
    ロガーを設定する。

    Args:
        path: ログファイルパス（デフォルト: _logs/app.jsonl）
        max_bytes: ローテーション閾値（バイト）
        max_files: ローテーション世代数
        level: ログレベル

    Returns:
        設定済みのlogger
    """
    global _logger

    if path is None:
        path = Path(DEFAULT_LOG_DIR) / DEFAULT_LOG_FILE
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("antigravity.jsonl")
    # 二重設定を防ぐ
    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False

    formatter = JsonlFormatter()
    fallback = _FallbackHandler()

    handler = _SafeRotatingHandler(
        str(path),
        maxBytes=max_bytes,
        backupCount=max_files,
        encoding="utf-8",
        fallback=fallback,
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    _logger = logger
    return logger


def _get_logger() -> logging.Logger:
    """ロガー取得。未セットアップならデフォルトで初期化。"""
    global _logger
    if _logger is None:
        _logger = setup_logger()
    return _logger


# ───────────────────────── API ─────────────────────────

def log_event(level: str, event: str, **fields: Any) -> None:
    """
    構造化ログを記録する。

    Args:
        level: ログレベル（"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"）
        event: イベント名
        **fields: 追加フィールド（redact対象）
    """
    logger = _get_logger()
    lvl = getattr(logging, level.upper(), logging.INFO)

    record = logger.makeRecord(
        name=logger.name,
        level=lvl,
        fn="",
        lno=0,
        msg=event,
        args=(),
        exc_info=None,
    )
    record.event = event  # type: ignore[attr-defined]
    record.extra_fields = fields  # type: ignore[attr-defined]
    logger.handle(record)


def info(event: str, **fields: Any) -> None:
    """INFOレベルでログ記録。"""
    log_event("INFO", event, **fields)


def warn(event: str, **fields: Any) -> None:
    """WARNINGレベルでログ記録。"""
    log_event("WARNING", event, **fields)


def error(event: str, err: BaseException | None = None, **fields: Any) -> None:
    """ERRORレベルでログ記録。errを渡すとエラー詳細を自動付与。"""
    logger = _get_logger()
    record = logger.makeRecord(
        name=logger.name,
        level=logging.ERROR,
        fn="",
        lno=0,
        msg=event,
        args=(),
        exc_info=None,
    )
    record.event = event  # type: ignore[attr-defined]
    record.extra_fields = fields  # type: ignore[attr-defined]
    if err is not None:
        record.err_obj = err  # type: ignore[attr-defined]
    logger.handle(record)
