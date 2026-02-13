# -*- coding: utf-8 -*-
"""
Desktop Control - ChatGPT Consultant via CDP
自律改善ループの設計についてChatGPTに相談するスクリプト
"""
import asyncio
import os
import sys
import json
from datetime import datetime
from playwright.async_api import async_playwright

CDP_URL = "http://127.0.0.1:9223"

# Planner学習機能
try:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from core.planner import learn_from_actions, Intent, IntentVerb, TargetApp, ActionSpec
    HAS_PLANNER = True
except ImportError:
    HAS_PLANNER = False


# 相談内容
PROMPT = """
あなたは自律型PC操作エージェント「Desktop Agent」のアーキテクトです。
現在、以下の「自律改善ループ（Autonomous Improvement Cycle）」の実装を計画しています。

## 目標
エージェントが自律的に「相談→調査→実装→検証」のサイクルを回し、継続的に自己改善する。

## ベンチマークタスク
**「デスクトップ版 Perplexityアプリ（Windows Native/Electron）」の操作**
- ブラウザ版ではなく、インストールされたデスクトップアプリを操作する。
- したがって、DOM操作（Playwright）は使えず、**UIA（Pywinauto）や画像認識（Vision）** が主体となる。

## 想定サイクル
1. **Consult (/desktop-chatgpt)**: 
   - 検証フェーズのログ（成功/失敗/画像解析結果）をChatGPTに共有。
   - 「なぜ操作に失敗したか」「次にどのロジックを修正すべきか」を決定。
2. **Research (/research)**: 
   - 必要な技術（例: ElectronアプリのUIAツリー構造、新しい画像認識手法）を調査。
3. **Implement (/code)**: 
   - Desktop Agentのコード（自律操作ロジック）を修正。
4. **Verify (Auto-Test)**: 
   - 実際にPerplexityアプリを起動し、検索タスクを実行する。
   - 操作ログ、スクリーンショット、UIAダンプを収集し、Consultへの入力データを作る。

## 質問
この「デスクトップアプリ操作」を軸とした改善ループについて設計してください。

1. **Desktop App検証の安定化**: 
   - DOMが使えない環境で、どのように「検索が成功した」「回答が表示された」を安定判定すべきか？（OCR？画像ハッシュ？UIA？）
2. **Consultへのフィードバック**: 
   - ChatGPTが「前回の失敗原因」を理解しやすくするためのログ形式は？（UIAツリーのスナップショット？エラー直前のSS？）
3. **安全な離脱**: 
   - デスクトップアプリがフリーズしたり、予期せぬウィンドウ（ポップアップ）が出た場合の回復・中断ロジックは？

具体的なPythonの設計案（クラス設計やデータ構造）を含めて回答してください。
"""

async def run_consultation():
    print(f"Connecting to CDP: {CDP_URL}")
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            # ChatGPTのページを探す
            page = None
            for p_obj in context.pages:
                if "chatgpt.com" in p_obj.url:
                    page = p_obj
                    break
            
            if not page:
                print("Error: ChatGPT page not found. Please open ChatGPT.")
                return

            print(f"Target Page: {page.url}")
            await page.bring_to_front()
            
            # 入力欄待機
            print("Waiting for input area...")
            # セレクタは状況により変わる可能性があるが、現状の一般的なものを試行
            textarea = page.locator("#prompt-textarea")
            await textarea.wait_for(state="visible", timeout=10000)
            
            # 質問入力
            print("Sending prompt...")
            await textarea.fill(PROMPT)
            await page.wait_for_timeout(1000)
            await textarea.press("Enter")
            
            # 回答待機（簡易実装: ■ボタンの出現→消失で判定）
            print("Waiting for generation start...")
            # stop button (black square)
            stop_btn = page.locator("button[aria-label='Stop streaming']")
            try:
                await stop_btn.wait_for(state="visible", timeout=5000)
                print("Generation started.")
            except:
                print("Warning: Stop button not detected immediately. Assuming started or quick finish.")

            print("Waiting for completion...")
            # ストップボタンが消えるまで待つ（ロングポール）
            # または特定の完了シグナル（sendボタン復活など）
            send_btn = page.locator("button[data-testid='send-button']")
            await send_btn.wait_for(state="visible", timeout=180000) # 3分待機
            
            print("Generation completed.")
            
            # 回答取得（最新のassistantメッセージ）
            response_locator = page.locator("div[data-message-author-role='assistant']").last
            text = await response_locator.inner_text()
            
            print("=== RESPONSE ===")
            print(text)
            print("================")
            
            # ログ保存
            log_dir = r".agent\workflows\desktop\logs"
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(log_dir, f"consultation_{timestamp}.md")
            
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"# Consultation Log {timestamp}\n\n")
                f.write("## Prompt\n")
                f.write(PROMPT + "\n\n")
                f.write("## Response\n")
                f.write(text + "\n")
            
            print(f"Log saved to: {log_file}")
            
            # 成功を学習
            _learn_chatgpt_result(True)

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            # 失敗を学習
            _learn_chatgpt_result(False)

def _learn_chatgpt_result(success: bool):
    """学習ヘルパー"""
    if not HAS_PLANNER:
        return
    try:
        intent = Intent(
            verb=IntentVerb.ASK,
            target_app=TargetApp.CHATGPT,
            query="ChatGPT相談"
        )
        actions = [
            ActionSpec(layer="cdp", action_type="fill", target="#prompt-textarea", description="質問入力"),
            ActionSpec(layer="cdp", action_type="press", params={"key": "Enter"}, description="送信"),
        ]
        learn_from_actions(intent, actions, success)
        print(f"[Planner] 学習完了: success={success}")
    except Exception as e:
        print(f"[Planner] 学習エラー: {e}")

if __name__ == "__main__":
    from pathlib import Path

    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main_async
    except Exception:
        asyncio.run(run_consultation())
    else:
        raise SystemExit(
            asyncio.run(
                run_logged_main_async(
                    "desktop",
                    "consult_autonomous_loop",
                    run_consultation,
                )
            )
        )
