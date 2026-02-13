from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / ".agent"
    / "workflows"
    / "desktop"
    / "scripts"
    / "chatgpt_cli.py"
)


def _load_chatgpt_cli():
    if "playwright.sync_api" not in sys.modules:
        playwright = types.ModuleType("playwright")
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.Page = object
        sync_api.sync_playwright = lambda: None  # type: ignore[assignment]
        sys.modules["playwright"] = playwright
        sys.modules["playwright.sync_api"] = sync_api

    spec = importlib.util.spec_from_file_location("chatgpt_cli_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_parse_args_supports_positional_and_option() -> None:
    mod = _load_chatgpt_cli()

    args_positional = mod.parse_args(["hello", "world"])
    args_option = mod.parse_args(["--question", "hello option"])

    assert args_positional.question == "hello world"
    assert args_option.question == "hello option"


def test_main_passes_timeout_and_cli_options(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_chatgpt_cli()
    captured: dict = {}

    def fake_ask(question, *, cdp_port, timeout_s, new_chat):
        captured.update(
            {
                "question": question,
                "cdp_port": cdp_port,
                "timeout_s": timeout_s,
                "new_chat": new_chat,
            }
        )
        return {"success": True, "response": "ok", "error": None}

    monkeypatch.setattr(mod, "ask_chatgpt_once", fake_ask)
    rc = mod.main(
        ["--question", "q1", "--cdp-port", "9333", "--timeout-s", "123", "--new-chat"]
    )

    assert rc == 0
    assert captured == {
        "question": "q1",
        "cdp_port": 9333,
        "timeout_s": 123,
        "new_chat": True,
    }


def test_main_keeps_result_keys_and_writes_result_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    mod = _load_chatgpt_cli()
    out_file = tmp_path / "result.json"

    monkeypatch.setattr(
        mod,
        "ask_chatgpt_once",
        lambda *_args, **_kwargs: {"success": False, "response": "partial", "error": "timeout"},
    )
    rc = mod.main(["-q", "q2", "--result-file", str(out_file)])
    stdout = capsys.readouterr().out.strip()

    assert rc == 1
    printed = json.loads(stdout)
    saved = json.loads(out_file.read_text(encoding="utf-8"))

    for payload in (printed, saved):
        assert set(payload.keys()) == {"success", "response", "error"}
        assert payload["response"] == "partial"
        assert payload["error"] == "timeout"
