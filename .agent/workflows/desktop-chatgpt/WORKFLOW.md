---
name: Desktop ChatGPT Agent v1.0.0
description: Desktop ChatGPT Agent v1.0.0
---

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと（ルール・技術詳細）

# Desktop ChatGPT Workflow v1.0.0 (`/desktop-chatgpt`)

> [!CAUTION]
> **必須**: このファイルと同フォルダの `SKILL.md` を読んでから実行

## 📋 実行フロー

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 接続確認 🔌                                              │
│    → CDP接続、ログイン状態確認                               │
│    → ログイン画面検出 → 手動対応を要求                       │
│    ↓                                                        │
│ 2. 質問送信 📤                                               │
│    → textarea.fill() で質問を入力                            │
│    → Enter で送信                                            │
│    ↓                                                        │
│ 3. 完了待機 ⏳                                               │
│    → StateMonitor で生成完了を監視                            │
│    → 複数確認手段（DOM・URL等）で合議判定                     │
│    ↓                                                        │
│ 4. エラー検出 ⚠️                                             │
│    → レート制限、エラーバナー確認                             │
│    → 検出時はSS保存・ユーザーに報告                          │
│    ↓                                                        │
│ 5. 回答取得 📥                                               │
│    → DOMからassistantメッセージを取得                        │
│    → ハッシュ安定で完了確認                                   │
│    ↓                                                        │
│ 6. 結果報告 ✅                                               │
│    → 回答を返却                                              │
│    → KI Learning に記録                                      │
└─────────────────────────────────────────────────────────────┘
```

## 🚀 クイックスタート

```powershell
# 1. CDP接続開始
python .agent\workflows\desktop\scripts\cdp_port_broker.py start my_agent

# 2. 質問送信（singleモード）
python .agent\workflows\desktop\scripts\goal_driven_consultation.py \
    --mode single --question "質問内容"
```

## 🎯 モード選択

| モード | 用途 | Antigravityの役割 |
|:-------|:-----|:------------------|
| **single** | 単発質問 | 質問生成・飽和判定を制御 |
| loop | 連続対話 | ⚠️ 非推奨（後方互換） |

## 📊 飽和判定（Antigravityが行う）

```
1. 質問を送信（single モード）
2. 回答を取得
3. 判断:
   - 必要な情報が揃ったか？
   - 追加質問が必要か？
   - 矛盾・懸念はないか？
4. 足りない → 追加質問（1-3繰り返し）
5. すべて揃っている → 終了
```

## ⚠️ エラー対応

| エラー | 対応 |
|:-------|:-----|
| ログイン画面検出 | ユーザーに手動ログインを要求 |
| レート制限 | 待機後リトライ |
| エラーバナー | SS保存・中断→報告 |

## 💡 Rules

- **必ず single モードを使用**
- **飽和判定は Antigravity が行う**
- **browser_subagent は禁止**
- **失敗・成功をKI Learning に記録**
