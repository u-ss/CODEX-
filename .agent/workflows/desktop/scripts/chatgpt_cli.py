# -*- coding: utf-8 -*-
"""
ChatGPT 1往復CLIツール

1回の質問送信→回答取得を行い、結果を標準出力に返す。
Antigravityがこのツールを呼び出し、回答を読んで次の質問を決める。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

DEFAULT_CDP_PORT = 9223
DEFAULT_TIMEOUT_S = 300


def _first_chatgpt_page(context) -> Optional[Page]:
    for page in context.pages:
        if "chatgpt.com" in page.url:
            return page
    return None


def _safe_count(page: Page, selector: str) -> int:
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def _safe_inner_text(page: Page, selector: str) -> str:
    try:
        loc = page.locator(selector).last
        if loc.count() > 0:
            return loc.inner_text().strip()
    except Exception:
        return ""
    return ""


def _wait_for_response_pro(
    page: Page,
    initial_assistant_count: int,
    timeout_s: int,
) -> tuple[bool, str, Optional[str]]:
    """
    Pro推論フェーズ対応の完了待機。

    stop button消失だけに依存せず、推論中UI・メッセージ増加・テキスト安定を合議で判定する。
    """
    start = time.time()
    stable_count = 0
    last_text = ""
    saw_new_message = False

    while (time.time() - start) < timeout_s:
        page.wait_for_timeout(1200)

        is_reasoning = _safe_count(page, 'text="今すぐ回答"') > 0
        is_generating = _safe_count(
            page,
            "button[data-testid='stop-button'], button[aria-label*='Stop']",
        ) > 0
        assistant_count = _safe_count(page, "div[data-message-author-role='assistant']")

        # 送信失敗時の早期検知
        if _safe_count(page, "div[role='alert']") > 0:
            return False, "", "エラーバナーを検出"
        if _safe_count(page, "div:has-text(\"You've reached\")") > 0:
            return False, "", "レート制限を検出"

        response_text = _safe_inner_text(page, "div[data-message-author-role='assistant']")
        if not response_text:
            response_text = _safe_inner_text(page, ".markdown")

        if assistant_count > initial_assistant_count:
            saw_new_message = True

        if response_text and saw_new_message:
            if (
                response_text == last_text
                and len(response_text) >= 10
                and not is_generating
                and not is_reasoning
            ):
                stable_count += 1
                if stable_count >= 3:
                    return True, response_text, None
            else:
                last_text = response_text
                stable_count = 0

    if last_text:
        return False, last_text, f"生成待機タイムアウト({timeout_s}s)"
    return False, "", f"生成待機タイムアウト({timeout_s}s)"


def ask_chatgpt_once(
    question: str,
    *,
    cdp_port: int = DEFAULT_CDP_PORT,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    new_chat: bool = False,
) -> dict:
    """
    ChatGPTに1往復の質問を行う。

    Returns:
        dict: {"success": bool, "response": str, "error": str|None}
    """
    p = sync_playwright().start()
    try:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        if not browser.contexts:
            return {"success": False, "response": "", "error": "ブラウザコンテキストがありません"}
        ctx = browser.contexts[0]

        page = _first_chatgpt_page(ctx)
        if not page:
            return {"success": False, "response": "", "error": "ChatGPTページが見つかりません"}

        page.bring_to_front()
        if "/auth/login" in page.url:
            return {"success": False, "response": "", "error": "セッション切れ"}

        if new_chat and "/c/" in page.url:
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)

        textarea = page.locator(
            "#prompt-textarea, textarea[placeholder*='Message'], textarea[placeholder*='メッセージ']"
        ).first
        try:
            textarea.wait_for(state="visible", timeout=15000)
        except Exception:
            return {"success": False, "response": "", "error": "入力欄が見つかりません"}

        initial_assistant_count = _safe_count(page, "div[data-message-author-role='assistant']")

        textarea.fill(question)
        page.wait_for_timeout(500)
        textarea.press("Enter")

        ok, response_text, wait_error = _wait_for_response_pro(
            page=page,
            initial_assistant_count=initial_assistant_count,
            timeout_s=timeout_s,
        )
        if not ok:
            return {"success": False, "response": response_text, "error": wait_error}
        return {"success": True, "response": response_text, "error": None}

    except Exception as e:
        return {"success": False, "response": "", "error": str(e)}
    finally:
        p.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ChatGPT 1往復CLI")
    parser.add_argument("question_positional", nargs="*", help="質問（後方互換用 positional）")
    parser.add_argument("--question", "-q", help="質問文")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT, help="CDPポート (default: 9223)")
    parser.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S, help="待機タイムアウト秒")
    parser.add_argument("--new-chat", action="store_true", help="送信前に新規チャットを開く")
    parser.add_argument("--result-file", help="結果JSONの保存先")
    return parser


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    positional_question = " ".join(args.question_positional).strip()
    args.question = (args.question or positional_question or "").strip()
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if not args.question:
        result = {"success": False, "response": "", "error": "質問が指定されていません"}
        print(json.dumps(result, ensure_ascii=False))
        return 1

    result = ask_chatgpt_once(
        args.question,
        cdp_port=args.cdp_port,
        timeout_s=args.timeout_s,
        new_chat=args.new_chat,
    )

    if args.result_file:
        out = Path(args.result_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        raise SystemExit(main())
    raise SystemExit(run_logged_main("desktop", "chatgpt_cli", main))
