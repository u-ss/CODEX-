---
name: Desktop ChatGPT v1.0.0
---

# Desktop ChatGPT SKILL v1.0.0 (`/desktop-chatgpt`)> [!CAUTION]

## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。

> **このエージェントは必ずブラウザ版ChatGPT（chatgpt.com）を操作する。**
> - CODEX や API ではない
> - Playwright + CDP でブラウザを操作
> - browser_subagent は **禁止**

## 📋 概要

ブラウザ版 ChatGPT との対話を自動化するエージェント。
質問送信、完了待機、回答取得を CDP 経由で行う。

## 🔐 ログイン保持

```powershell
# golden_profileを使用（cdp_port_broker.py経由）python .agent\workflows\desktop\scripts\cdp_port_broker.py start my_agent
```

## 📤 送信ルール

> [!CAUTION]
> 1. **必ず1回でまとめて送る**
> 2. **keyboard.type()で改行(\n)を含めると途中で送信される**
> 3. `textarea.fill()` を使う

```python
# 正しい送信方法textarea = page.locator('textarea')
textarea.fill(question)
await page.keyboard.press('Enter')
```

## ⏳ 完了判定

| 方式 | 実装 | 推奨度 |
|:-----|:-----|:------:|
| **StateMonitor** | `state_monitor.py` | ⭐⭐⭐ **推奨** |
| **send-button復活** | `wait_for(state="visible")` | ⭐⭐ |
| **FSM合議判定** | `generation_fsm.py` | ⭐ |

### StateMonitor 使用例

```python
from integrations.chatgpt.state_monitor import ChatGPTStateMonitor

monitor = ChatGPTStateMonitor(page, poll_interval_ms=500, stable_window_ms=2000)
success, snapshot = monitor.wait_for_generation_complete(timeout_ms=120000)
```

## ✅ 複数確認手段

| 確認手段 | 内容 |
|:---------|:-----|
| **DOM確認** | メッセージ数の増加を検出 |
| **URL確認** | `/c/` 会話IDの付与を検出 |

> 合議判定: 1/2以上成功で成功と判定

## 🎯 実行モード

| モード | 用途 | コマンド |
|:-------|:-----|:---------|
| `single` | **推奨** Antigravity委譲型 | `--mode single --question "質問"` |
| `loop` | ⚠️ **非推奨**（後方互換） | `--mode loop --goal "目標"` |

```powershell
# 単発モード（推奨）python .agent\workflows\desktop\scripts\goal_driven_consultation.py \
    --mode single --question "具体的な質問内容"
```

## 🧪 テストスクリプト

```powershell
python .agent\workflows\desktop\scripts\test_single_query.py
```

| 検証 | 確認手段 | 判定基準 |
|:-----|:---------|:---------|
| **送信確認** | msg数増加・URL変化・textarea空 | 2/3以上で合格 |
| **稼働確認** | stop-button・テキスト長変化 | いずれかで合格 |
| **返答取得確認** | ハッシュ安定・エラーチェック | 2秒安定で合格 |

## ⚠️ エラー検出

| 検出対象 | セレクタ/URL | 対応 |
|:---------|:-------------|:-----|
| **ログイン画面** | URL に `/auth/login` | 手動ログイン要求 |
| **レート制限** | `div:has-text("You've reached")` | 待機後リトライ |
| **エラーバナー** | `div[role='alert']` | SS保存→中断 |

```python
# セッション切れ検出if "/auth/login" in page.url or "accounts.google.com" in page.url:
    raise Exception("セッション切れ：手動でログインし直してください")
```

## 🔬 QWEN Auto Consultation（実験的）

> [!WARNING]
> **指示があるときのみ使用**。通常はsingleモードを使用。

```powershell
python .agent\workflows\desktop\scripts\qwen_auto_consult.py \
    --goal "システム設計の壁打ち" \
    --topics "アーキテクチャ,技術選定,リスク対策" \
    --max-rallies 100
```

## 🔍 Research Trigger

```python
from integrations.chatgpt.research_trigger import ResearchTrigger

trigger = ResearchTrigger()
result = trigger.evaluate(user_query, assistant_response)
if result.should_search:
    # /research を呼び出す
```

## 📚 KI Learning統合

```python
from ki_learning_hook import report_action_outcome, check_risks

# 操作後：結果記録report_action_outcome(
    agent='/desktop-chatgpt',
    intent_class='send_message',
    outcome='SUCCESS',
    latency_ms=150
)
```

## 💡 Rules

- **browser_subagent 禁止**
- **textarea.fill() を使用**
- **完了判定は StateMonitor 推奨**
- **失敗/成功を KI Learning に記録**
- **Language**: 日本語で報告

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
