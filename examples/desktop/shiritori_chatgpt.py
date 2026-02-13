# -*- coding: utf-8 -*-
"""
しりとりスクリプト - ChatGPT Desktop (CDP経由)
Playwright + CDP でブラウザ版ChatGPTとしりとりを行う
"""
import sys
import time
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]

# パスを追加
sys.path.insert(0, str(REPO_ROOT / ".agent" / "workflows" / "desktop" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / ".agent" / "workflows" / "desktop"))

CDP_PORT = 9224  # cdp_port_brokerが割り当てたポート

def wait_for_response(page, initial_msg_count, timeout_s=120):
    """ChatGPTの回答完了を待機（DOM監視ベース）"""
    start = time.time()
    last_text = ""
    stable_count = 0
    
    while time.time() - start < timeout_s:
        time.sleep(1)
        try:
            # アシスタントメッセージを取得
            msgs = page.locator('[data-message-author-role="assistant"]')
            current_count = msgs.count()
            
            if current_count > initial_msg_count:
                # 新しいメッセージが来た
                last_msg = msgs.last
                current_text = last_msg.inner_text()
                
                if current_text == last_text and len(current_text) > 0:
                    stable_count += 1
                    if stable_count >= 3:  # 3秒安定で完了とみなす
                        return True, current_text
                else:
                    stable_count = 0
                    last_text = current_text
        except Exception as e:
            print(f"  [待機中] エラー: {e}")
            continue
    
    return False, last_text


def send_message(page, message):
    """ChatGPTにメッセージを送信"""
    # 入力欄を探す
    textarea = page.locator('#prompt-textarea, textarea[placeholder*="Message"]')
    textarea.wait_for(state="visible", timeout=15000)
    
    # fill() + Enter で送信（SKILL.mdのルールに従う）
    textarea.fill(message)
    time.sleep(0.5)
    textarea.press("Enter")
    print(f"  [送信完了] {message[:50]}...")


def main():
    print("=" * 50)
    print("🎮 しりとり with ChatGPT")
    print("=" * 50)
    
    p = sync_playwright().start()
    try:
        # CDP接続
        print(f"\n[1] CDP接続 (port={CDP_PORT})...")
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        context = browser.contexts[0]
        
        # ChatGPTページを探す
        page = None
        for pg in context.pages:
            if "chatgpt.com" in pg.url:
                page = pg
                break
        
        if not page:
            print("❌ ChatGPTページが見つかりません")
            return
        
        page.bring_to_front()
        print(f"  ChatGPTページ発見: {page.url}")
        
        # セッション確認
        if "/auth/login" in page.url:
            print("❌ セッション切れ：手動でログインしてください")
            return
        
        # 新規チャットを開く
        print("\n[2] 新規チャットを開く...")
        if "/c/" in page.url:
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
        print(f"  URL: {page.url}")
        
        # しりとりの対話ログ
        shiritori_log = []
        
        # === ターン1: 最初のメッセージ送信 ===
        print("\n[3] しりとり開始！")
        first_message = "しりとりをしましょう！ルール：日本語の単語でしりとりをします。「ん」で終わる言葉を言ったら負けです。私から始めます。\n\n「りんご」\n\n次はあなたの番です。「ご」から始まる言葉を言ってください。その後、私が続けられるように最後の文字を教えてください。"
        
        # 送信前のメッセージ数を記録
        initial_count = page.locator('[data-message-author-role="assistant"]').count()
        
        send_message(page, first_message)
        shiritori_log.append({"turn": 1, "player": "私", "word": "りんご"})
        
        # 回答待機
        print("  [回答待機中...]")
        success, response1 = wait_for_response(page, initial_count)
        
        if not success:
            print("❌ タイムアウト")
            return
        
        print(f"\n  📝 ChatGPTの回答:\n  {response1[:200]}...")
        shiritori_log.append({"turn": 1, "player": "ChatGPT", "response": response1})
        
        # === ターン2: 続きを送る ===
        print("\n[4] ターン2...")
        initial_count2 = page.locator('[data-message-author-role="assistant"]').count()
        
        second_message = "いいですね！では次は私の番です。\n\n「ごりら」\n\nあなたの番です。「ら」から始まる言葉を言ってください。"
        send_message(page, second_message)
        shiritori_log.append({"turn": 2, "player": "私", "word": "ごりら"})
        
        print("  [回答待機中...]")
        success2, response2 = wait_for_response(page, initial_count2)
        
        if not success2:
            print("❌ タイムアウト")
            return
        
        print(f"\n  📝 ChatGPTの回答:\n  {response2[:200]}...")
        shiritori_log.append({"turn": 2, "player": "ChatGPT", "response": response2})
        
        # === ターン3: もう1回 ===
        print("\n[5] ターン3...")
        initial_count3 = page.locator('[data-message-author-role="assistant"]').count()
        
        third_message = "楽しいですね！では、\n\n「ラッパ」\n\nあなたの番です。「ぱ」から始まる言葉を言ってください。"
        send_message(page, third_message)
        shiritori_log.append({"turn": 3, "player": "私", "word": "ラッパ"})
        
        print("  [回答待機中...]")
        success3, response3 = wait_for_response(page, initial_count3)
        
        if not success3:
            print("❌ タイムアウト")
            return
        
        print(f"\n  📝 ChatGPTの回答:\n  {response3[:200]}...")
        shiritori_log.append({"turn": 3, "player": "ChatGPT", "response": response3})
        
        # === 結果表示 ===
        print("\n" + "=" * 50)
        print("🎉 しりとり完了！（3ターン）")
        print("=" * 50)
        
        print("\n📋 対話ログ:")
        for entry in shiritori_log:
            if "word" in entry:
                print(f"  ターン{entry['turn']} [{entry['player']}]: {entry['word']}")
            elif "response" in entry:
                print(f"  ターン{entry['turn']} [{entry['player']}]: {entry['response'][:80]}...")
        
        # ログ保存
        log_dir = REPO_ROOT / ".agent" / "workflows" / "desktop" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"shiritori_{int(time.time())}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump({
                "game": "shiritori",
                "turns": shiritori_log,
                "chat_url": page.url,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=2)
        print(f"\n📁 ログ保存: {log_file}")
        
    except Exception as e:
        import traceback
        print(f"❌ エラー: {e}")
        traceback.print_exc()
    finally:
        p.stop()


if __name__ == "__main__":
    main()
