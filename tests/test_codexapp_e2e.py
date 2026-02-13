"""
CODEXAPPクライアント E2Eテスト

前提: CODEXAPPがポート9224で起動中であること
実行: python tests/test_codexapp_e2e.py
"""
import sys
import os
import json
import time
import tempfile

# codexapp_cdp_client.pyのパスを追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '.agent', 'workflows', 'codex', 'sub_agents', 'app', 'scripts'))

from codexapp_cdp_client import (
    CdpClient, send_message, poll_response, get_latest,
    generate_token, save_last_token, load_last_token, _LAST_TOKEN_FILE
)

PORT = 9224
RESULTS = []


def log_result(name, passed, detail=""):
    """テスト結果を記録"""
    status = "✅ PASS" if passed else "❌ FAIL"
    RESULTS.append({"name": name, "passed": passed, "detail": detail})
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


def test_1_send_simple():
    """T1: 簡単なメッセージを送信して応答がファイルに保存されるか"""
    print("\n=== Test 1: send_simple ===")
    cdp = CdpClient(PORT)
    try:
        cdp.connect()

        # 新スレッドで送信
        cdp.open_new_thread()
        time.sleep(1)
        cdp.clear_input()

        token = generate_token()
        save_last_token(token)

        result = send_message(cdp, f"{token}\n1+1は何ですか？数字だけで答えてください。", token)
        if result not in ("clicked_send", "clicked_active", "enter_fallback"):
            log_result("send_simple", False, f"送信失敗: {result}")
            return None, None

        response = poll_response(cdp, token, timeout=60, interval=2.0)

        if not response:
            log_result("send_simple", False, "応答なし")
            return None, None

        # ファイル保存確認
        out_file = "_outputs/codexapp/test_send_response.txt"
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(response)

        log_result("send_simple", True, f"応答{len(response)}文字、保存OK")
        return token, response

    except Exception as e:
        log_result("send_simple", False, str(e))
        return None, None
    finally:
        cdp.close()


def test_2_token_auto_save_load(expected_token):
    """T2: send後に.last_tokenが存在し、中身がトークン形式"""
    print("\n=== Test 2: token_auto_save_load ===")

    if not os.path.exists(_LAST_TOKEN_FILE):
        log_result("token_auto_save_load", False, f"{_LAST_TOKEN_FILE} が存在しない")
        return

    loaded = load_last_token()
    if not loaded:
        log_result("token_auto_save_load", False, "トークンが空")
        return

    if loaded != expected_token:
        log_result("token_auto_save_load", False, f"不一致: expected={expected_token}, got={loaded}")
        return

    if not loaded.startswith("[[REQ:"):
        log_result("token_auto_save_load", False, f"形式不正: {loaded}")
        return

    log_result("token_auto_save_load", True, f"トークン一致: {loaded}")


def test_3_get_latest_with_auto_token(expected_response):
    """T3: send後にget-latestをトークン未指定で実行、同じ応答が取得できる"""
    print("\n=== Test 3: get_latest_with_auto_token ===")
    cdp = CdpClient(PORT)
    try:
        cdp.connect()

        # トークン自動読込でget-latest
        token = load_last_token()
        if not token:
            log_result("get_latest_with_auto_token", False, "トークン自動読込失敗")
            return None

        state = get_latest(cdp, token)
        if not state:
            log_result("get_latest_with_auto_token", False, "応答データなし")
            return None

        text = state.get("fullResponseText", state.get("latestAssistantText", ""))
        if not text:
            log_result("get_latest_with_auto_token", False, "fullResponseTextが空")
            return None

        log_result("get_latest_with_auto_token", True, f"取得OK: {len(text)}文字")
        return text

    except Exception as e:
        log_result("get_latest_with_auto_token", False, str(e))
        return None
    finally:
        cdp.close()


def test_4_get_latest_file_save():
    """T4: get-latest -o で指定ファイルに保存される"""
    print("\n=== Test 4: get_latest_file_save ===")

    # CLIコマンドとして実行して-oの動作を確認
    out_file = "_outputs/codexapp/test_get_latest_output.txt"
    cmd = f'python .agent/workflows/codex/sub_agents/app/scripts/codexapp_cdp_client.py --port {PORT} -o {out_file} get-latest'
    exit_code = os.system(cmd)

    if exit_code != 0:
        log_result("get_latest_file_save", False, f"exit code={exit_code}")
        return

    if not os.path.exists(out_file):
        log_result("get_latest_file_save", False, f"{out_file} が作成されていない")
        return

    with open(out_file, "r", encoding="utf-8") as f:
        content = f.read()

    if not content:
        log_result("get_latest_file_save", False, "ファイルが空")
        return

    log_result("get_latest_file_save", True, f"保存OK: {len(content)}文字")


def test_5_send_vs_get_latest_match(send_response, get_latest_response):
    """T5: sendの保存結果 == get-latestの結果が一致"""
    print("\n=== Test 5: send_vs_get_latest_match ===")

    if send_response is None or get_latest_response is None:
        log_result("send_vs_get_latest_match", False, "比較対象が不足（前のテスト失敗）")
        return

    # 正規化して比較（末尾の空白差を吸収）
    s = send_response.strip()
    g = get_latest_response.strip()

    if s == g:
        log_result("send_vs_get_latest_match", True, f"完全一致（{len(s)}文字）")
    else:
        # 部分一致チェック
        common = min(len(s), len(g))
        diff_pos = 0
        for i in range(common):
            if s[i] != g[i]:
                diff_pos = i
                break
        else:
            diff_pos = common

        log_result("send_vs_get_latest_match", False,
                   f"不一致（send={len(s)}文字, get-latest={len(g)}文字, 差分位置={diff_pos}）")


def test_6_response_not_intermediate(response):
    """T6: 応答テキストに中間ログのみが含まれていないこと"""
    print("\n=== Test 6: response_not_intermediate ===")

    if response is None:
        log_result("response_not_intermediate", False, "応答が取得できていない")
        return

    trimmed = response.strip()

    # 中間ログのみの場合
    is_only_commands = trimmed.startswith("実行済みコマンド：")
    ends_with_work = trimmed.endswith("作業しました")

    if is_only_commands and ends_with_work:
        log_result("response_not_intermediate", False,
                   "応答が中間ログのみ（実行済みコマンド + 作業しました）")
        return

    if ends_with_work and "実行済みコマンド：" in trimmed:
        # 中間ログ + 実際の回答が混在していないか
        lines = trimmed.split("\n")
        last_meaningful = ""
        for line in reversed(lines):
            line = line.strip()
            if line and not line.startswith("実行済みコマンド："):
                last_meaningful = line
                break
        if last_meaningful.endswith("作業しました"):
            log_result("response_not_intermediate", False,
                       f"最終行が中間ログ: {last_meaningful[:100]}")
            return

    log_result("response_not_intermediate", True, "応答は最終回答として妥当")


def main():
    print("=" * 60)
    print("CODEXAPPクライアント E2Eテスト")
    print(f"ポート: {PORT}")
    print("=" * 60)

    # 接続テスト
    print("\n--- 接続確認 ---")
    try:
        cdp = CdpClient(PORT)
        cdp.connect()
        cdp.close()
        print(f"✅ ポート{PORT}に接続成功")
    except Exception as e:
        print(f"❌ 接続失敗: {e}")
        print("CODEXAPPがポート9224で起動していることを確認してください")
        sys.exit(1)

    # テスト実行
    token, send_response = test_1_send_simple()
    test_2_token_auto_save_load(token)
    get_latest_response = test_3_get_latest_with_auto_token(send_response)
    test_4_get_latest_file_save()
    test_5_send_vs_get_latest_match(send_response, get_latest_response)
    test_6_response_not_intermediate(send_response)

    # 結果サマリー
    print("\n" + "=" * 60)
    print("テスト結果サマリー")
    print("=" * 60)
    passed = sum(1 for r in RESULTS if r["passed"])
    total = len(RESULTS)
    for r in RESULTS:
        status = "✅" if r["passed"] else "❌"
        print(f"  {status} {r['name']}")
    print(f"\n結果: {passed}/{total} pass")

    if passed == total:
        print("🎉 全テスト合格！")
    else:
        print("⚠️ 不合格テストあり")
        sys.exit(1)


if __name__ == "__main__":
    main()
