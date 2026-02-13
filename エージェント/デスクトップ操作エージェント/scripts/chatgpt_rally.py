"""
ChatGPTとラリーを続けるスクリプト v2
- Brave (port 9222) でCDP接続
- SSで稼働確認
- 10回以上ラリー
"""
import time
import mss
from PIL import Image
from pathlib import Path
from playwright.sync_api import sync_playwright

# 設定
CDP_URL = "http://localhost:9222"  # Brave
OUTPUT_DIR = Path("_chatgpt_responses/rally")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def take_screenshot(name: str) -> Path:
    """SSを撮って稼働確認用"""
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[0])
        img = Image.frombytes("RGB", raw.size, raw.rgb)
        # リサイズして軽量化
        img = img.resize((raw.width // 2, raw.height // 2))
        path = Path(f"_screenshots/rally_{name}.jpg")
        img.save(path, "JPEG", quality=60)
        return path

def is_response_complete(page) -> bool:
    """ChatGPTが回答中かどうかをチェック"""
    try:
        # 「Stop」ボタンがあれば回答中
        stop_btn = page.query_selector('button[aria-label="Stop generating"]')
        if stop_btn and stop_btn.is_visible():
            return False
        # 送信ボタンが有効 = 回答完了
        send_btn = page.query_selector('button[data-testid="send-button"]')
        if send_btn:
            return True
    except:
        pass
    return True

def get_latest_response(page) -> str:
    """最新の回答をDOMから取得"""
    try:
        responses = page.query_selector_all('.markdown')
        if responses:
            return responses[-1].inner_text()
    except Exception as e:
        print(f"Error getting response: {e}")
    return ""

def send_message(page, message: str) -> bool:
    """メッセージを送信"""
    try:
        # 入力欄を探す
        textarea = page.query_selector('#prompt-textarea')
        if not textarea:
            print("Textarea not found")
            return False
        
        # クリックしてフォーカス
        textarea.click()
        time.sleep(0.3)
        
        # テキストを入力
        page.evaluate('''(text) => {
            const el = document.getElementById("prompt-textarea");
            if (el) {
                el.focus();
                document.execCommand("insertText", false, text);
            }
        }''', message)
        
        time.sleep(0.5)
        
        # 送信
        send_btn = page.query_selector('button[data-testid="send-button"]')
        if send_btn:
            send_btn.click()
            return True
        else:
            page.keyboard.press("Enter")
            return True
    except Exception as e:
        print(f"Error sending message: {e}")
        return False

def wait_for_response(page, timeout_sec: int = 180) -> str:
    """回答を待つ"""
    start = time.time()
    while time.time() - start < timeout_sec:
        if is_response_complete(page):
            time.sleep(2)  # 安定待ち
            return get_latest_response(page)
        time.sleep(3)
        print(".", end="", flush=True)
    print("\nTimeout!")
    return get_latest_response(page)

def find_chatgpt_page(browser):
    """ChatGPTのページを探す"""
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if 'chatgpt.com' in pg.url:
                return pg
    return None

def rally(messages: list[str], output_prefix: str = "rally"):
    """ChatGPTとラリー"""
    print(f"Connecting to CDP at {CDP_URL}...")
    
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            print(f"CDP connection failed: {e}")
            print("Make sure Brave is running with --remote-debugging-port=9222")
            return []
        
        # ChatGPTタブを探す
        page = find_chatgpt_page(browser)
        
        if not page:
            print("ChatGPT page not found. Opening new tab...")
            page = browser.contexts[0].new_page()
            page.goto("https://chatgpt.com/")
            time.sleep(5)
        
        print(f"Using page: {page.url[:60]}...")
        responses = []
        
        for i, msg in enumerate(messages):
            print(f"\n{'='*50}")
            print(f"=== Rally {i+1}/{len(messages)} ===")
            print(f"{'='*50}")
            print(f"Sending: {msg[:80]}...")
            
            # SS（送信前）
            take_screenshot(f"{i:02d}_before")
            
            # 送信
            if not send_message(page, msg):
                print("Failed to send")
                break
            
            # 回答を待つ
            print("Waiting for response", end="")
            response = wait_for_response(page)
            
            # SS（回答後）
            take_screenshot(f"{i:02d}_after")
            
            # 保存
            output_file = OUTPUT_DIR / f"{output_prefix}_{i:02d}.txt"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"=== Message ===\n{msg}\n\n=== Response ===\n{response}")
            
            print(f"\nResponse saved: {len(response)} chars")
            responses.append(response)
        
        return responses


def _cli_main() -> int:
    messages = [
        """前回の回答ありがとうございます。まず優先度1の「Actionの型統一 + 成功判定」から実装したいです。

現在の実装では、各Layerで個別にクリックや入力を呼び出しています。これをAction型で統一するための具体的なクラス設計を教えてください。

特に知りたいのは：
1. Actionの基底クラス設計
2. 各Layer用のExecutor設計
3. success_checkの実装パターン（DOM変化、画面変化、etc）

具体的なPythonコード例をお願いします。""",
    ]

    results = rally(messages, "action_type")
    print(f"\n{'='*50}")
    print(f"Total responses: {len(results)}")
    return 0


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path

    _here = _Path(__file__).resolve()
    for _parent in _here.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in _sys.path:
                _sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(_run_logged_main("desktop", "chatgpt_rally", _cli_main, phase_name="CHATGPT_RALLY_RUN"))

