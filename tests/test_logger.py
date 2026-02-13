"""
tests/test_logger.py — lib/logger.py のテスト

検証項目:
  1. JSONL全行JSONパース
  2. max_bytesによるローテーション
  3. redactで秘密情報が漏れない
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# テスト対象のインポートパス設定
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.logger import redact, setup_logger, info, warn, error, log_event  # noqa: E402


# ───────────────────────── redact テスト ─────────────────────────

class TestRedact:
    """redact()のテスト"""

    def test_mask_password(self) -> None:
        data = {"user": "taro", "password": "secret123"}
        result = redact(data)
        assert result["user"] == "taro"
        assert result["password"] == "***"

    def test_mask_token(self) -> None:
        data = {"access_token": "abc123", "name": "test"}
        result = redact(data)
        assert result["access_token"] == "***"
        assert result["name"] == "test"

    def test_mask_api_key(self) -> None:
        data = {"api_key": "key-xyz", "value": 42}
        result = redact(data)
        assert result["api_key"] == "***"
        assert result["value"] == 42

    def test_mask_authorization(self) -> None:
        data = {"Authorization": "Bearer xxx"}
        result = redact(data)
        assert result["Authorization"] == "***"

    def test_mask_cookie(self) -> None:
        data = {"session_cookie": "sid=abc"}
        result = redact(data)
        assert result["session_cookie"] == "***"

    def test_mask_secret(self) -> None:
        data = {"client_secret": "s3cr3t"}
        result = redact(data)
        assert result["client_secret"] == "***"

    def test_nested_dict(self) -> None:
        data = {"config": {"db_password": "pass123", "host": "localhost"}}
        result = redact(data)
        assert result["config"]["db_password"] == "***"
        assert result["config"]["host"] == "localhost"

    def test_list_of_dicts(self) -> None:
        data = [{"token": "aaa"}, {"name": "ok"}]
        result = redact(data)
        assert result[0]["token"] == "***"
        assert result[1]["name"] == "ok"

    def test_deeply_nested(self) -> None:
        data = {"a": {"b": {"c": {"password": "deep"}}}}
        result = redact(data)
        assert result["a"]["b"]["c"]["password"] == "***"

    def test_original_not_mutated(self) -> None:
        data = {"password": "original"}
        _ = redact(data)
        assert data["password"] == "original"

    def test_non_sensitive_keys_untouched(self) -> None:
        data = {"username": "taro", "email": "t@example.com", "age": 30}
        result = redact(data)
        assert result == data

    def test_case_insensitive(self) -> None:
        data = {"PASSWORD": "p", "Token": "t", "API_KEY": "k"}
        result = redact(data)
        assert result["PASSWORD"] == "***"
        assert result["Token"] == "***"
        assert result["API_KEY"] == "***"


# ───────────────────────── JSONL パーステスト ─────────────────────────

class TestJsonlOutput:
    """JSONLの全行がJSONとしてパースできること"""

    def test_all_lines_are_json(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.jsonl"
        logger = setup_logger(path=log_file, max_bytes=10_000_000)

        info("event_one", user="taro")
        warn("event_two", count=5)
        error("event_three", err=ValueError("bad value"), detail="x")

        # ハンドラーをフラッシュ
        for h in logger.handlers:
            h.flush()

        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

        for i, line in enumerate(lines):
            parsed = json.loads(line)  # パースできなければ例外
            assert "ts" in parsed, f"行{i}: tsフィールドがない"
            assert "level" in parsed, f"行{i}: levelフィールドがない"
            assert "event" in parsed, f"行{i}: eventフィールドがない"
            assert "pid" in parsed, f"行{i}: pidフィールドがない"
            assert "host" in parsed, f"行{i}: hostフィールドがない"

    def test_info_level(self, tmp_path: Path) -> None:
        log_file = tmp_path / "info.jsonl"
        setup_logger(path=log_file)
        info("hello", key="value")
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        entry = json.loads(lines[0])
        assert entry["level"] == "INFO"
        assert entry["event"] == "hello"
        assert entry["key"] == "value"

    def test_error_with_exception(self, tmp_path: Path) -> None:
        log_file = tmp_path / "err.jsonl"
        setup_logger(path=log_file)
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            error("crash", err=e)
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        entry = json.loads(lines[0])
        assert entry["level"] == "ERROR"
        assert entry["error"]["type"] == "RuntimeError"
        assert entry["error"]["message"] == "boom"


# ───────────────────────── redact がログに効くテスト ─────────────────────────

class TestRedactInLogs:
    """ログ出力で秘密情報が平文で出ないこと"""

    def test_password_not_in_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "redact.jsonl"
        setup_logger(path=log_file)
        info("user_login", username="taro", password="super_secret_pw")

        content = log_file.read_text(encoding="utf-8")
        assert "super_secret_pw" not in content
        assert "***" in content

    def test_token_not_in_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "redact2.jsonl"
        setup_logger(path=log_file)
        info("api_call", token="Bearer xyz123", url="/api/v1")

        content = log_file.read_text(encoding="utf-8")
        assert "xyz123" not in content
        assert "Bearer" not in content

    def test_nested_secret_not_in_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "redact3.jsonl"
        setup_logger(path=log_file)
        info("config_loaded", config={"db": {"password": "db_pass_123"}})

        content = log_file.read_text(encoding="utf-8")
        assert "db_pass_123" not in content


# ───────────────────────── ローテーションテスト ─────────────────────────

class TestRotation:
    """max_bytesを小さくしてローテーションが起きること"""

    def test_rotation_creates_backup(self, tmp_path: Path) -> None:
        log_file = tmp_path / "rotate.jsonl"
        # 1024バイトでローテ、世代2
        setup_logger(path=log_file, max_bytes=1024, max_files=2)

        # 大量にログを吐く
        for i in range(100):
            info(f"event_{i}", data="x" * 50, index=i)

        # バックアップファイルが存在すること
        backup1 = tmp_path / "rotate.jsonl.1"
        assert backup1.exists(), "ローテーションファイル .1 が生成されていない"

    def test_rotation_max_files(self, tmp_path: Path) -> None:
        log_file = tmp_path / "rot.jsonl"
        setup_logger(path=log_file, max_bytes=512, max_files=3)

        for i in range(200):
            info(f"evt_{i}", payload="y" * 30)

        # max_files=3 なので .1, .2, .3 まで
        assert (tmp_path / "rot.jsonl.1").exists()
        assert (tmp_path / "rot.jsonl.2").exists()
        # .4は存在しないはず（上限3）
        assert not (tmp_path / "rot.jsonl.4").exists()

    def test_all_rotated_files_are_valid_jsonl(self, tmp_path: Path) -> None:
        log_file = tmp_path / "valid.jsonl"
        setup_logger(path=log_file, max_bytes=1024, max_files=2)

        for i in range(100):
            info(f"check_{i}", n=i)

        # 全ファイルの全行がJSONパース可能
        for f in tmp_path.glob("valid.jsonl*"):
            content = f.read_text(encoding="utf-8").strip()
            if not content:
                continue
            for line_no, line in enumerate(content.splitlines(), 1):
                json.loads(line)  # パース失敗なら例外
