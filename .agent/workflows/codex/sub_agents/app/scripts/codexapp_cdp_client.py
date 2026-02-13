"""
codexapp_cdp_client.py — CODEXAPPデスクトップアプリのCDP操作クライアント

使い方:
    # メッセージ送信＆応答取得
    python codexapp_cdp_client.py send "質問テキスト"
    
    # 最新の応答だけ取得
    python codexapp_cdp_client.py get-latest

    # ポート指定
    python codexapp_cdp_client.py --port 9224 send "質問テキスト"
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime

try:
    import requests
    import websocket
except ImportError:
    print("必須ライブラリ: pip install requests websocket-client")
    sys.exit(1)

# === セレクタ定数（v1.4.0: data-*属性ベース） ===
# 全JSコード内のセレクタはここに定義された値と一致していること。
# セレクタ変更時はこの定数セクションと各JS内の値を同時に更新する。
# ドキュメント（SPEC.md/GUIDE.md）の変更は不要。
# トークン自動保存先（send時に保存、get-latest時に自動読込）
_LAST_TOKEN_FILE = os.path.join("_outputs", "codexapp", ".last_token")

SELECTORS = {
    # 会話コンテナ（応答取得で使用）
    "conversation": '[data-thread-find-target="conversation"]',
    "review": '[data-thread-find-target="review"]',
    "any_target": '[data-thread-find-target]',
    # コンポーザー（入力・送信で使用）
    "composer": '[data-thread-find-composer]',
    "prosemirror": '[data-codex-composer="true"]',
    "prosemirror_fallback": '.ProseMirror',
    # フィルタ（テキスト抽出で使用）
    "skip": '[data-thread-find-skip="true"]',
}


# === CDPヘルパー ===

class CdpClient:
    """CDP (Chrome DevTools Protocol) クライアント"""

    def __init__(self, port=9224):
        self.port = port
        self.ws = None
        self.msg_id = 0

    def connect(self):
        """CDPターゲットに接続（#9: title='Codex'優先選択）"""
        targets = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=5).json()
        # title="Codex"を優先、なければ最初のpage
        page = next(
            (t for t in targets if t.get("type") == "page" and "Codex" in t.get("title", "")),
            next((t for t in targets if t.get("type") == "page"), None)
        )
        if not page:
            raise RuntimeError("CDPページターゲットが見つかりません")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
        return page.get("title", "")

    def send(self, method, params=None):
        """CDPコマンドを送信（#1 Critical: id一致ループでイベント割り込み耐性）"""
        self.msg_id += 1
        mid = self.msg_id
        payload = {"id": mid, "method": method, "params": params or {}}
        self.ws.send(json.dumps(payload))
        # id一致するまで受信ループ（CDPイベント通知をスキップ）
        for _ in range(100):  # 無限ループ防止
            raw = self.ws.recv()
            resp = json.loads(raw)
            if resp.get("id") == mid:
                return resp
            # idなし = CDPイベント通知 → スキップ
        raise RuntimeError(f"CDP応答が見つかりません (id={mid})")

    def evaluate(self, expression):
        """Runtime.evaluate のショートカット（例外検出付き）"""
        r = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True
        })
        # JS実行エラーの検出
        exc = r.get("result", {}).get("exceptionDetails")
        if exc:
            desc = exc.get("exception", {}).get("description", "不明なJSエラー")
            print(f"⚠️ JS実行エラー: {desc}")
            return None
        return r.get("result", {}).get("result", {}).get("value")

    def close(self):
        """WebSocket接続を安全に閉じる"""
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass  # 切断済みの場合のエラーを無視

    def open_new_thread(self):
        """新スレッドを開く（既存会話をリセット）"""
        result = self.evaluate("""
            (() => {
                // 方法1: テキストで検索（多言語対応）
                let btns = document.querySelectorAll('button');
                for (let b of btns) {
                    let t = (b.innerText || '').trim();
                    if ((t.includes('新しいスレッド') || t.includes('New thread') || t.includes('New chat'))
                        && !b.disabled) {
                        b.click();
                        return 'clicked_new_thread';
                    }
                }
                // 方法2: Ctrl+Shift+O ショートカット
                return 'not_found';
            })()
        """)
        if result == 'clicked_new_thread':
            time.sleep(3)  # スレッド切り替え待機
        return result

    def clear_input(self):
        """ProseMirrorの入力欄をクリア（Ctrl+A → Delete）"""
        # Ctrl+A: 全選択
        self.send("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "a", "code": "KeyA",
            "windowsVirtualKeyCode": 65, "modifiers": 2  # 2 = Ctrl
        })
        self.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "a", "code": "KeyA",
            "windowsVirtualKeyCode": 65, "modifiers": 2
        })
        time.sleep(0.1)
        # Delete: 選択テキスト削除
        self.send("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "Delete", "code": "Delete",
            "windowsVirtualKeyCode": 46
        })
        self.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Delete", "code": "Delete",
            "windowsVirtualKeyCode": 46
        })
        time.sleep(0.2)
        return True


# === 応答取得JSテンプレート（v1.4.0: SELECTORS定数から動的注入） ===
# セレクタは $SEL_XXX プレースホルダで参照し、_build_collect_js()で置換する。

_COLLECT_RESPONSE_JS_TEMPLATE = r"""
(() => {
  const REQUEST_TOKEN = "__REQ_TOKEN__";
  const normalize = (s) =>
    (s || "").replace(/\\r\\n/g, "\\n").replace(/\\n{3,}/g, "\\n\\n").trim();

  // === skip要素を除去してクリーンテキスト取得 ===
  const getCleanText = (el) => {
    const c = el.cloneNode(true);
    c.querySelectorAll('$SEL_SKIP').forEach(n => n.remove());
    return normalize(c.innerText || "");
  };

  // === 会話コンテナの取得 ===
  const conversationRoot =
    document.querySelector('$SEL_CONVERSATION') ||
    document.querySelector('$SEL_REVIEW') ||
    document.querySelector('$SEL_ANY_TARGET');
  const conversationFound = !!conversationRoot;

  // laneは会話ブロックの直接の親（firstElementChildがレーン）
  const lane = conversationRoot?.firstElementChild || conversationRoot;

  // === 可視ブロック一覧を取得（クラス非依存） ===
  const blocks = lane
    ? Array.from(lane.children).filter(el => {
        const text = getCleanText(el);
        if (!text) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      })
    : [];

  // === トークンでアンカーブロック（ユーザー行）を特定 ===
  let anchorIdx = -1;
  let anchorText = "";
  if (REQUEST_TOKEN) {
    for (let i = blocks.length - 1; i >= 0; i--) {
      const t = getCleanText(blocks[i]);
      if (t.includes(REQUEST_TOKEN)) {
        anchorIdx = i;
        anchorText = t.substring(0, 100);
        break;
      }
    }
  }
  // フォールバック: トークンが見つからない場合、最後から2番目をアンカーとする
  if (anchorIdx < 0) anchorIdx = Math.max(0, blocks.length - 2);

  // === アンカー後続ブロック（アシスタント応答）を収集 ===
  const responseBlocks = blocks.slice(anchorIdx + 1).filter(el => {
    const t = getCleanText(el);
    return t && !t.includes('[[REQ:');
  });

  const responseTexts = responseBlocks.map(getCleanText);
  const latestText = responseTexts[responseTexts.length - 1] || "";
  const fullResponseText = responseTexts.join("\n\n");
  const assistantCount = responseBlocks.length;

  // === 送信ボタンの判定（composerベース） ===
  const composerRoot =
    document.querySelector('$SEL_COMPOSER') ||
    document.querySelector('$SEL_PROSEMIRROR')?.closest('$SEL_COMPOSER') ||
    document.querySelector('$SEL_PROSEMIRROR')?.parentElement;
  const actionButton = composerRoot
    ? Array.from(composerRoot.querySelectorAll('button:not([disabled])'))
        .filter(b => b.querySelector('svg') && !(b.innerText || "").trim())
        .map(b => ({ b, r: b.getBoundingClientRect() }))
        .filter(x => x.r.width > 0 && x.r.height > 0)
        .sort((a, b) => (b.r.x - a.r.x) || (b.r.y - a.r.y))[0]?.b || null
    : null;

  // looksReady: ボタンが存在し、無効でなければReady
  const looksReady = !!actionButton;

  // === 思考中テキストの検出 ===
  const thinkingPatterns = /^(思考中|Thinking|考え中|Analyzing|処理中|Loading|\.\.\.)$/i;
  const isThinking = thinkingPatterns.test(latestText.trim());

  // === 中間ログ検出（CODEX作業中の出力を最終回答と誤判定しない） ===
  const trimmedFull = fullResponseText.trim();
  const isIntermediate = /作業しました$/.test(trimmedFull)
    || /^実行済みコマンド：/.test(trimmedFull)
    || (/実行済みコマンド：/.test(trimmedFull) && !/^(log_strategy|task_spec|quality_criteria|##|###|\d+\.)/.test(trimmedFull.split('\n').pop()?.trim() || ""));

  // === テキスト安定判定（ポーリング用ハッシュ） ===
  const hash = (s) => {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h += (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24);
    }
    return (h >>> 0).toString(16);
  };

  const stateKey = REQUEST_TOKEN || "__fallback__";
  window.__codexCdpReplyState = window.__codexCdpReplyState || {};
  const st =
    window.__codexCdpReplyState[stateKey] ||
    (window.__codexCdpReplyState[stateKey] = { lastHash: null, stableTicks: 0 });

  const fp = hash(fullResponseText);
  if (fp && fp === st.lastHash) st.stableTicks += 1;
  else {
    st.lastHash = fp;
    st.stableTicks = 0;
  }

  // isComplete: 応答テキストあり + 送信ボタンReady + 6回安定 + 思考中でない + 中間ログでない
  const isComplete = fullResponseText.length > 0 && looksReady && st.stableTicks >= 6 && !isThinking && !isIntermediate;
  // isReady: get-latest用（ワンショット簡易判定）
  const isReady = fullResponseText.length > 0 && looksReady && !isThinking;

  return JSON.stringify({
    conversationFound: conversationFound,
    anchorFound: anchorIdx >= 0,
    anchorText: anchorText,
    assistantAfterCount: assistantCount,
    latestAssistantType: assistantCount > 0 ? 'data_block' : null,
    latestAssistantText: latestText,
    fullResponseText: fullResponseText,
    latestAssistantLen: latestText.length,
    fullResponseLen: fullResponseText.length,
    stableTicks: st.stableTicks,
    looksReady: looksReady,
    isThinking: isThinking,
    isIntermediate: isIntermediate,
    isComplete: isComplete,
    isReady: isReady,
  });
})()
"""


def _build_collect_js(token: str = "") -> str:
    """SELECTORS定数からセレクタを注入してCOLLECT_RESPONSE_JSを生成する。"""
    js = _COLLECT_RESPONSE_JS_TEMPLATE.replace("__REQ_TOKEN__", token)
    js = js.replace("$SEL_CONVERSATION", SELECTORS["conversation"])
    js = js.replace("$SEL_REVIEW", SELECTORS["review"])
    js = js.replace("$SEL_ANY_TARGET", SELECTORS["any_target"])
    js = js.replace("$SEL_COMPOSER", SELECTORS["composer"])
    js = js.replace("$SEL_PROSEMIRROR", SELECTORS["prosemirror"])
    js = js.replace("$SEL_SKIP", SELECTORS["skip"])
    return js




def generate_token():
    """一意リクエストトークンを生成（#14: uuid4で衝突防止）"""
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    uid = uuid.uuid4().hex[:8]
    return f"[[REQ:{ts}-{uid}]]"


def save_last_token(token: str):
    """トークンをファイルに保存（get-latestでの自動読込用）"""
    token_dir = os.path.dirname(_LAST_TOKEN_FILE)
    if token_dir:
        os.makedirs(token_dir, exist_ok=True)
    with open(_LAST_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)


def load_last_token() -> str:
    """保存済みトークンを読み込む（ファイルがなければ空文字列）"""
    try:
        with open(_LAST_TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def send_message(cdp: CdpClient, text: str, token: str):
    """ProseMirrorにテキストを入力して送信ボタンをクリック"""

    # 状態リセット
    cdp.evaluate("(() => { window.__codexCdpReplyState = {}; return true; })()")

    # ProseMirror座標を動的取得してクリック（SELECTORS定数から注入）
    pos_json = cdp.evaluate(f"""
        (() => {{
            let pm = document.querySelector('{SELECTORS["prosemirror"]}')
                  || document.querySelector('{SELECTORS["prosemirror_fallback"]}');
            if (!pm) return null;
            let rect = pm.getBoundingClientRect();
            return JSON.stringify({{x: rect.x+rect.width/2, y: rect.y+rect.height/2}});
        }})()
    """)
    if not pos_json:
        print("❌ ProseMirrorが見つかりません")
        return 'no_prosemirror'
    try:
        pos = json.loads(pos_json)
    except (json.JSONDecodeError, TypeError):
        print(f"❌ ProseMirror座標の解析失敗: {pos_json!r}")
        return 'pos_parse_error'

    cdp.send("Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": pos["x"], "y": pos["y"],
        "button": "left", "clickCount": 1
    })
    cdp.send("Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": pos["x"], "y": pos["y"],
        "button": "left", "clickCount": 1
    })
    time.sleep(0.3)

    # トークン付きテキストを入力
    full_text = f"{text}\n{token}"
    cdp.send("Input.insertText", {"text": full_text})
    time.sleep(0.5)

    # Enterキーで送信（ボタンクリックは不安定なためEnterを第一手段とする）
    # 根拠: CODEXAPPのProseMirrorではボタン検出が応答状態により変化し
    # 別ボタン（data-state=closedの非送信ボタン）を誤クリックする問題が確認済み。
    # Enterキーはラリーテストで安定動作を確認。
    cdp.send("Input.dispatchKeyEvent", {
        "type": "keyDown", "key": "Enter", "code": "Enter",
        "windowsVirtualKeyCode": 13
    })
    cdp.send("Input.dispatchKeyEvent", {
        "type": "keyUp", "key": "Enter", "code": "Enter",
        "windowsVirtualKeyCode": 13
    })
    result = 'enter_sent'

    return result


def poll_response(cdp: CdpClient, token: str, timeout=120, interval=2.0):
    """応答完了までポーリングし、最新のアシスタント応答を返す"""
    # #7 High: トークンをJSセーフ文字列にエスケープ
    safe_token = json.dumps(token)[1:-1]  # JSON文字列化してクォートを除去
    js_with_token = _build_collect_js(safe_token)
    js_no_token = _build_collect_js("")  # フォールバック: トークンなし

    # DOM再構築リカバリー用の状態追跡
    prev_count = 0          # 前回の応答件数
    zero_count_ticks = 0    # 連続0件のtick数
    ZERO_FALLBACK_TICKS = int(30 / interval)  # 30秒間0件でフォールバック

    for i in range(int(timeout / interval)):
        time.sleep(interval)

        # 通常はトークン付きJSで取得、0件が続いたらフォールバック
        use_fallback = zero_count_ticks >= ZERO_FALLBACK_TICKS
        js = js_no_token if use_fallback else js_with_token

        raw = cdp.evaluate(js)
        if not raw:
            if i % 10 == 0:
                elapsed = int(i * interval)
                print(f"  [{elapsed}s] DOM未構築...")
            continue

        # P4: JSONパース保護
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            if i % 10 == 0:
                print(f"  [{int(i * interval)}s] ⚠️ 応答JSONパース失敗")
            continue

        current_count = state.get("assistantAfterCount", 0)

        # 応答消失検知: 応答が1件以上→0件に減少した場合
        if prev_count > 0 and current_count == 0:
            elapsed = int(i * interval)
            print(f"  [{elapsed}s] ⚠️ 応答消失検知 ({prev_count}→0件), DOM再構築待機中...")
            zero_count_ticks = 1
            prev_count = 0
            continue

        # 0件カウントの追跡
        if current_count == 0:
            zero_count_ticks += 1
            if zero_count_ticks == ZERO_FALLBACK_TICKS:
                print(f"  [{int(i * interval)}s] ⚠️ {int(ZERO_FALLBACK_TICKS * interval)}秒間応答0件、トークンなしフォールバックに切替")
        else:
            zero_count_ticks = 0

        prev_count = current_count

        if state["isComplete"]:
            fb_label = " (fallback)" if use_fallback else ""
            print(f"  [{int(i * interval)}s] ✅ 応答完了{fb_label} ({state.get('fullResponseLen', state['latestAssistantLen'])}文字, stable={state['stableTicks']})")
            return state.get("fullResponseText", state["latestAssistantText"])

        if i % 5 == 0:
            status_parts = []
            if not state["conversationFound"]:
                status_parts.append("conversation未検出")
            elif not state["anchorFound"]:
                status_parts.append("アンカー未検出")
            else:
                status_parts.append(f"応答{state['assistantAfterCount']}件")
                status_parts.append(f"{state['latestAssistantLen']}文字")
                status_parts.append(f"ready={state['looksReady']}")
                status_parts.append(f"stable={state['stableTicks']}")
                if state.get("isIntermediate"):
                    status_parts.append("⚠️中間ログ")
            if use_fallback:
                status_parts.append("fallback")
            print(f"  [{int(i * interval)}s] {' / '.join(status_parts)}")

    print(f"  ⚠️ タイムアウト ({timeout}s)")
    # タイムアウト後: トークンなしフォールバックでも最終取得を試みる
    for final_js in [js_with_token, js_no_token]:
        try:
            raw = cdp.evaluate(final_js)
            if raw:
                state = json.loads(raw)
                text = state.get("fullResponseText", state.get("latestAssistantText", ""))
                if text:
                    print(f"  ✅ タイムアウト後に取得成功 ({len(text)}文字)")
                    return text
        except (json.JSONDecodeError, TypeError, Exception) as e:
            print(f"  ⚠️ タイムアウト後のパース失敗: {e}")
    return ""


def get_latest(cdp: CdpClient, token=""):
    """現在の最新応答を1回だけ取得（#11: JSONパース保護付き）"""
    safe_token = json.dumps(token)[1:-1]
    js = _build_collect_js(safe_token)
    raw = cdp.evaluate(js)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def main():
    parser = argparse.ArgumentParser(description="CODEXAPPデスクトップ CDP クライアント")
    parser.add_argument("--port", type=int, default=9224, help="CDPポート (default: 9224)")
    parser.add_argument("-o", "--output", help="応答をファイルに保存")
    sub = parser.add_subparsers(dest="command")

    # send サブコマンド（#13: --new-thread/--clear-inputオプション追加）
    p_send = sub.add_parser("send", help="メッセージを送信して応答を待つ")
    p_send.add_argument("message", help="送信するメッセージ")
    p_send.add_argument("--timeout", type=int, default=120, help="応答タイムアウト(秒)")
    p_send.add_argument("--new-thread", action="store_true", help="送信前に新スレッドを開く")
    p_send.add_argument("--clear-input", action="store_true", help="送信前に入力欄をクリア")

    # get-latest サブコマンド
    p_get = sub.add_parser("get-latest", help="最新の応答を取得")
    p_get.add_argument("--token", default="", help="追跡トークン")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    cdp = CdpClient(port=args.port)
    try:
        title = cdp.connect()
        print(f"接続: {title} (port {args.port})")

        if args.command == "send":
            # #13: 送信前オプション
            if getattr(args, 'new_thread', False):
                print("新スレッドを開きます...")
                cdp.open_new_thread()
            if getattr(args, 'clear_input', False):
                print("入力欄をクリアします...")
                cdp.clear_input()

            token = generate_token()
            save_last_token(token)  # get-latestでの自動読込用に保存
            print(f"トークン: {token}")

            result = send_message(cdp, args.message, token)
            print(f"送信: {result}")

            if result not in ("clicked_send", "clicked_active", "enter_fallback"):
                print(f"❌ 送信失敗: {result}")
                return

            print("応答待機中...")
            response = poll_response(cdp, token, timeout=args.timeout)

            if response:
                print(f"\n=== 応答 ({len(response)}文字) ===")
                print(response[:3000])
                if len(response) > 3000:
                    print(f"\n... ({len(response) - 3000}文字省略)")
            else:
                print("❌ 応答を取得できませんでした")

            # #2 Critical: ファイル保存（ディレクトリ空文字チェック）
            out = args.output or "_outputs/codexapp/response_latest.txt"
            out_dir = os.path.dirname(out)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                f.write(response or "")
            print(f"\n保存: {out}")

        elif args.command == "get-latest":
            token = args.token
            if not token:
                token = load_last_token()
                if token:
                    print(f"トークン自動読込: {token}")
            state = get_latest(cdp, token)
            if state:
                text = state.get("fullResponseText", state.get("latestAssistantText", ""))
                is_intermediate = state.get("isIntermediate", False)
                print(f"応答取得: {len(text)}文字 (complete={state.get('isComplete')}, intermediate={is_intermediate})")
                if is_intermediate:
                    print("⚠️ 中間ログの可能性あり（CODEXがまだ作業中かもしれません）")
                print(text[:3000])
                if len(text) > 3000:
                    print(f"\n... ({len(text) - 3000}文字省略)")
                # ファイル保存（-o対応）
                out = args.output or "_outputs/codexapp/response_latest.txt"
                out_dir = os.path.dirname(out)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                with open(out, "w", encoding="utf-8") as f:
                    f.write(text)
                print(f"\n保存: {out}")
            else:
                print("❌ 応答データなし")

    except Exception as e:
        print(f"❌ エラー: {e}")
        raise
    finally:
        cdp.close()


if __name__ == "__main__":
    from pathlib import Path

    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        main()
    else:
        raise SystemExit(run_logged_main("codexapp", "cdp_client", main))
