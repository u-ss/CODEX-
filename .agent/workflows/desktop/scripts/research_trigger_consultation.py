# -*- coding: utf-8 -*-
"""
Research Trigger設計 - ChatGPT 10ラリー相談スクリプト（改善版）
同期APIを使用
"""

import json
import time
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

CDP_PORT = 9223

# 相談内容
INITIAL_PROMPT = """あなたはAI Agent設計の専門家です。
私は「/desktop-chatgpt」というChatGPT対話機能に、以下の新機能を追加したいと考えています。

## 目的
ChatGPT対話中に「調査が必要」と判断したら、自動で/research（Web検索エージェント）を呼び出し、
調査結果を持ってChatGPTに戻って対話を続ける「ハイブリッドフロー」を実現したい。

## 想定ユースケース
例：リセールエージェントを設計中
- 「市場規模は？」→ ChatGPT「推定で〇〇」→ 実データが欲しいので/research呼び出し
- 「この手法の論文ある？」→ /researchで学術情報を収集
- ChatGPTの発言を検証・深掘りしたいときも/research

## 現在の設計案
3つの軸でリサーチ判断：
1. 情報補完（市場データ、法規制、技術仕様）
2. エビデンス要求（論文、実例）
3. 検証・深掘り（ChatGPT発言の確認、より深い理解）

## 質問
1. この設計アプローチについてどう思いますか？
2. 「リサーチが必要」と判断する良いロジック/ヒューリスティクスは何ですか？
3. 見落としている判断軸はありますか？"""

FOLLOWUP_PROMPTS = [
    "ChatGPTの回答から「不確かさ」を検出するための具体的なパターンやキーワードはどのようなものが効果的でしょうか？日本語と英語の両方で教えてください。",
    
    "「エビデンス要求」のトリガーについて深掘りしたいです。論文や実例を求めるべきケースを見極める判断基準を具体的に教えてください。",
    
    "検証・深掘り（Fact-check）の判断について質問です。ChatGPTが自信を持って答えているように見えても、実は検証が必要なケースはありますか？その見分け方は？",
    
    "実装面について質問です。ResearchTriggerクラスを設計する場合、どのような属性やメソッドを持たせるべきでしょうか？Pythonでの設計案を示してください。",
    
    "リサーチ結果をChatGPTに戻すときの最適な伝え方は？文脈を保持しながら効果的に情報を統合する方法を教えてください。",
    
    "エッジケースについて。リサーチの結果とChatGPTの回答が矛盾した場合、どう処理すべきでしょうか？",
    
    "パフォーマンスの観点から、リサーチ判断を高速に行うためのベストプラクティスは？",
    
    "この機能を段階的に実装するためのロードマップを提案してください。MVP（最小実装）から始めて徐々に機能を拡張する計画を。",
    
    "最後に、この設計全体のリスクと注意点を教えてください。改善のための追加アドバイスがあればお願いします。",
]

def run_consultation():
    """10ラリー相談を実行（同期API）"""
    log_entries = []
    
    print(f"Connecting to CDP: http://127.0.0.1:{CDP_PORT}")
    
    p = sync_playwright().start()
    try:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        context = browser.contexts[0]
        
        # ChatGPTページを探す
        page = None
        for pg in context.pages:
            if "chatgpt.com" in pg.url:
                page = pg
                break
        
        if not page:
            print("Error: ChatGPT page not found.")
            return
        
        print(f"Found ChatGPT page: {page.url}")
        page.bring_to_front()
        
        # セッション確認
        if "/auth/login" in page.url:
            print("Error: セッション切れ")
            return
        
        # 全プロンプト
        all_prompts = [INITIAL_PROMPT] + FOLLOWUP_PROMPTS
        
        for i, prompt in enumerate(all_prompts, 1):
            print(f"\n{'='*50}")
            print(f"Rally {i}/10")
            print(f"{'='*50}")
            
            try:
                # 入力欄待機
                textarea = page.locator("#prompt-textarea")
                textarea.wait_for(state="visible", timeout=15000)
                
                # 質問送信
                print(f"Sending prompt ({len(prompt)} chars)...")
                textarea.fill(prompt)
                time.sleep(0.5)
                textarea.press("Enter")
                
                # Phase 1: 生成開始確認（stop-button出現）
                print("Waiting for generation start...")
                stop_btn = page.locator("button[aria-label='Stop streaming']")
                try:
                    stop_btn.wait_for(state="visible", timeout=10000)
                    print("Generation started (stop-button visible)")
                except:
                    print("Stop button not detected, checking if already complete...")
                
                # Phase 2: 生成完了確認（stop-button消失）
                print("Waiting for generation complete...")
                stop_btn.wait_for(state="hidden", timeout=180000)
                print("Generation complete (stop-button hidden)")
                
                time.sleep(2.0)  # 安定待ち（DOM更新完了）
                
                # 回答取得
                response_locator = page.locator("div[data-message-author-role='assistant']").last
                response_text = response_locator.inner_text()
                
                print(f"Response received ({len(response_text)} chars)")
                print(f"Preview: {response_text[:150]}...")
                
                # ログ記録
                log_entries.append({
                    "rally": i,
                    "prompt": prompt,
                    "response": response_text,
                    "timestamp": datetime.now().isoformat()
                })
                
                time.sleep(2)  # 次の質問前に待機
                
            except Exception as e:
                print(f"Rally {i} error: {e}")
                log_entries.append({
                    "rally": i,
                    "prompt": prompt,
                    "response": f"ERROR: {e}",
                    "timestamp": datetime.now().isoformat()
                })
        
        print("\n" + "="*50)
        print(f"完了！ {len([e for e in log_entries if 'ERROR' not in e['response']])}/10 ラリー成功")
        print("="*50)
        
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # ログ保存
        log_dir = Path(r".agent\workflows\desktop\logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # JSON
        json_file = log_dir / f"research_trigger_{timestamp}.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(log_entries, f, ensure_ascii=False, indent=2)
        print(f"\nJSON: {json_file}")
        
        # Markdown
        md_file = log_dir / f"research_trigger_{timestamp}.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write("# Research Trigger設計 - ChatGPT相談ログ\n\n")
            f.write(f"日時: {timestamp}\n\n")
            for entry in log_entries:
                f.write(f"## Rally {entry['rally']}\n\n")
                f.write(f"### 質問\n{entry['prompt']}\n\n")
                f.write(f"### 回答\n{entry['response']}\n\n")
                f.write("---\n\n")
        print(f"Markdown: {md_file}")
        
        p.stop()


if __name__ == "__main__":
    import sys

    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        run_consultation()
    else:
        raise SystemExit(
            run_logged_main(
                "desktop",
                "research_trigger_consultation",
                lambda: run_consultation(),
            )
        )
