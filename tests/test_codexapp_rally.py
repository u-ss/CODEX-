"""
CODEXAPPエージェント統合デバッグテスト — 10ラリー連続対話

各ラリーで:
1. send でメッセージ送信・応答取得
2. get-latest で再取得・一致確認
3. エラー検出時は即停止
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '.agent', 'workflows', 'codex', 'sub_agents', 'app', 'scripts'))
from codexapp_cdp_client import (
    CdpClient, send_message, poll_response, get_latest,
    generate_token, save_last_token, load_last_token
)

PORT = 9224

# テスト質問10問（多様なパターン）
RALLIES = [
    # 1. 短い質問（基本動作）
    "3+5は？数字だけ答えて。",
    # 2. 日本語/英語混在
    "Translate to English: 「天気が良い」。One line only.",
    # 3. コードブロック依頼
    "Pythonで1から5を出力するコードを1行で。コードのみ、説明不要。",
    # 4. リスト生成
    "曜日を月〜日まで改行で列挙。それ以外書かないで。",
    # 5. 計算問題
    "100÷4の答えは？数字だけ。",
    # 6. Yes/No質問
    "東京は日本の首都ですか？はい/いいえで。",
    # 7. 短い創作
    "「猫」で五七五。一句だけ。",
    # 8. フォーマット指定
    "JSON形式で{name:太郎,age:30}を出力。JSONのみ。",
    # 9. 数値列挙
    "素数を5つ、カンマ区切りで。それ以外不要。",
    # 10. 最終確認
    "「テスト完了」とだけ返答して。",
]

def run_rally(cdp, rally_num, question, use_new_thread=False):
    """1ラリー: send → 応答確認 → get-latest一致確認"""
    print(f"\n{'='*60}")
    print(f"ラリー {rally_num}/10: {question[:50]}...")
    print(f"{'='*60}")

    # 最初のラリーだけ新スレッド
    if use_new_thread:
        print("  → 新スレッド開始")
        cdp.open_new_thread()
        time.sleep(1)

    # 毎回入力欄をクリア（前回テキスト残留対策）
    cdp.clear_input()
    time.sleep(0.3)

    # トークン生成・保存
    token = generate_token()
    save_last_token(token)
    print(f"  → トークン: {token}")

    # 送信
    # 送信（send_messageがトークンを自動追加するのでquestionのみ渡す）
    result = send_message(cdp, question, token)
    print(f"  → 送信結果: {result}")
    if result not in ("clicked_send", "clicked_active", "enter_fallback", "enter_sent"):
        return {"rally": rally_num, "status": "FAIL", "error": f"送信失敗: {result}"}

    # 応答待機
    response = poll_response(cdp, token, timeout=90, interval=2.0)
    if not response:
        return {"rally": rally_num, "status": "FAIL", "error": "応答なし（タイムアウト）"}

    response_len = len(response.strip())
    print(f"  → 応答: {response_len}文字")
    print(f"  → 内容: {response.strip()[:200]}")

    # 短い待機（CODEXAPP側の安定）
    time.sleep(2)

    # get-latest で再取得
    state = get_latest(cdp, token)
    if not state:
        return {"rally": rally_num, "status": "FAIL", "error": "get-latest: 応答データなし"}

    gl_text = state.get("fullResponseText", state.get("latestAssistantText", ""))
    is_intermediate = state.get("isIntermediate", False)
    is_complete = state.get("isComplete", False)

    print(f"  → get-latest: {len(gl_text.strip())}文字, complete={is_complete}, intermediate={is_intermediate}")

    # 一致確認
    send_trimmed = response.strip()
    gl_trimmed = gl_text.strip()
    match = send_trimmed == gl_trimmed

    if not match:
        # 部分一致チェック（send応答がget-latestに含まれるか）
        if send_trimmed in gl_trimmed or gl_trimmed in send_trimmed:
            print(f"  ⚠️ 部分一致（send={len(send_trimmed)}, get-latest={len(gl_trimmed)}）")
            match_status = "PARTIAL_MATCH"
        else:
            print(f"  ❌ 不一致（send={len(send_trimmed)}, get-latest={len(gl_trimmed)}）")
            match_status = "MISMATCH"
    else:
        print(f"  ✅ 完全一致")
        match_status = "MATCH"

    # 中間ログチェック
    if is_intermediate:
        print(f"  ⚠️ 中間ログ検出")
        return {"rally": rally_num, "status": "WARN", "error": "中間ログ検出", "match": match_status, "response_len": response_len}

    return {
        "rally": rally_num,
        "status": "PASS",
        "match": match_status,
        "response_len": response_len,
        "send_len": len(send_trimmed),
        "gl_len": len(gl_trimmed),
    }


def main():
    print("=" * 60)
    print("CODEXAPPエージェント統合デバッグテスト — Phase A: 10ラリー")
    print(f"ポート: {PORT}")
    print("=" * 60)

    # 接続
    cdp = CdpClient(PORT)
    try:
        title = cdp.connect()
        print(f"✅ 接続成功: {title}")
    except Exception as e:
        print(f"❌ 接続失敗: {e}")
        sys.exit(1)

    results = []
    try:
        for i, question in enumerate(RALLIES, 1):
            result = run_rally(cdp, i, question, use_new_thread=(i == 1))
            results.append(result)

            if result["status"] == "FAIL":
                print(f"\n❌ ラリー{i}でエラー発生 — テスト停止")
                print(f"  エラー: {result['error']}")
                break

            # ラリー間の待機（CODEXAPPの安定）
            if i < len(RALLIES):
                time.sleep(1)

    finally:
        cdp.close()

    # 結果サマリー
    print(f"\n{'='*60}")
    print("テスト結果サマリー — Phase A")
    print(f"{'='*60}")

    pass_count = sum(1 for r in results if r["status"] == "PASS")
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    match_count = sum(1 for r in results if r.get("match") == "MATCH")
    total = len(results)

    for r in results:
        status_icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(r["status"], "?")
        match_icon = {"MATCH": "=", "PARTIAL_MATCH": "≈", "MISMATCH": "≠"}.get(r.get("match", ""), "-")
        error_info = f" [{r['error']}]" if "error" in r else ""
        print(f"  {status_icon} ラリー{r['rally']}: {r['status']} (一致:{match_icon}, {r.get('response_len', 0)}文字){error_info}")

    print(f"\n結果: {pass_count}pass / {fail_count}fail / {warn_count}warn (全{total}ラリー)")
    print(f"一致率: {match_count}/{total}")

    # JSON保存
    out_file = "_outputs/codexapp/rally_test_results.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"詳細結果: {out_file}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
