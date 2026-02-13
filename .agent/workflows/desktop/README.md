---
name: Desktop Control v5.2.2
description: 自律PC操作エージェント - 技術リファレンス
---

# Desktop Control v5.2.2 - 技術リファレンス

自律PC操作エージェント。CDP/UIA/PyAutoGUIの多層制御。
v5.2.2: StateMonitor統合、3点検証（送信・稼働・返答）追加、runtime pathを環境変数化。

> [!NOTE]
> **必須ルール・禁止事項**: [SKILL.md](./SKILL.md) 参照
> **実行フロー・サブコマンド**: [WORKFLOW.md](./WORKFLOW.md) 参照

---

## 保存先設定

Desktop系の保存先は runtime resolver で一元管理する。

| 環境変数 | 既定値 |
|:---------|:-------|
| `AG_DESKTOP_BASE_DIR` | `_outputs/desktop` |
| `AG_DESKTOP_SCREENSHOT_DIR` | `${AG_DESKTOP_BASE_DIR}/screenshots` |
| `AG_DESKTOP_TEMPLATE_STORE_PATH` | `${AG_DESKTOP_BASE_DIR}/learning/learned_templates.json` |

---

## クイックスタート

```powershell
# テスト実行（3点検証付き）
python .agent\workflows\desktop\scripts\test_single_query.py
```

**期待出力**:
```
[Verify-1] 送信確認...
    [01] 0.3s | msg:3→4 📤🔗⏳
  ✅ PASS

[Verify-2] 稼働確認...
    [01] 0.0s | stop:⏹️ len:0→0 ⏳
  ✅ PASS

[Verify-3] 返答取得確認...
    [01] 0.5s | ⏹️ 生成中... len=0
    ...
    [12] 5.7s | ✅ 安定確認 (2.1s)
  ✅ PASS

[Result] 3/3 checks passed
```

---

## コアモジュール

| モジュール | 役割 |
|:-----------|:-----|
| `tools/chatgpt.py` | ChatGPT操作API |
| `tools/screenshot.py` | マルチモニターSS |
| `integrations/chatgpt/state_monitor.py` | **v5.2推奨** リアルタイム状態監視 |
| `integrations/chatgpt/generation_fsm.py` | FSM合議判定 |
| `integrations/chatgpt/adaptive_selector.py` | DOM動的セレクタ発見 |
| `scripts/test_single_query.py` | **v2.0** 3点検証テスト |

---

## ブラウザCDP起動

```powershell
# Edge（CDP有効）
Start-Process "msedge.exe" -ArgumentList "--remote-debugging-port=9222", "https://chatgpt.com"

# ポートブローカー使用（推奨）
python .agent\workflows\desktop\scripts\cdp_port_broker.py start my_agent
```

---

## ChatGPT連携

### 完了待機（v5.2推奨パターン）

```python
# v5.2: StateMonitor方式（推奨）
from integrations.chatgpt.state_monitor import ChatGPTStateMonitor, ChatGPTState
monitor = ChatGPTStateMonitor(page, poll_interval_ms=500, stable_window_ms=2000)
success, snapshot = monitor.wait_for_generation_complete(timeout_ms=120000)

# FSM方式
from integrations.chatgpt.generation_fsm import wait_for_generation_async
success, fsm = await wait_for_generation_async(page, cfg)
```

### テストスクリプトの検証クラス

```python
# test_single_query.py v2.0
from scripts.test_single_query import ChatGPTVerifier

verifier = ChatGPTVerifier(page)
result1 = verifier.verify_message_sent(pre_count, pre_url)      # 送信確認
result2 = verifier.verify_chatgpt_active()                       # 稼働確認
result3 = verifier.verify_response_received(pre_count)           # 返答取得確認
```

### 回答取得

```python
response = page.locator("div[data-message-author-role='assistant']").last
text = await response.inner_text()
```

---

## エラー検出

```python
# ログイン画面
if "/auth/login" in page.url:
    raise Exception("セッション切れ")

# レート制限
if page.locator("div:has-text('You\\'ve reached')").count() > 0:
    raise Exception("レート制限")
```
