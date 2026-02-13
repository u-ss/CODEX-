---
name: Check Agent v1.0.0
description: Check Agent v1.0.0
---

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと（ルール・技術詳細）

# Check Agent v1.0.0 (`/check`)

**ワークスペース健全性チェックエージェント**: 依存関係分析・問題検出・提案・承認・修正を自動化。

> [!CAUTION]
> **必須**: このファイルと同フォルダの`SKILL.md`を読んでから実行

## 📋 Protocol: 7-Step Execution Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. SCAN 📁                                                  │
│    → .agent/ 配下の全ファイル収集                           │
│    ↓                                                        │
│ 2. ANALYZE 🔗                                               │
│    → 各ファイルのimport/参照を解析                          │
│    → Node/Edge構造で依存グラフ構築                          │
│    ↓                                                        │
│ 3. DETECT 🔍                                                │
│    → ルール評価（下記参照）                                 │
│    → finding生成（重要度・対象ファイル・修正案）             │
│    ↓                                                        │
│ 4. PROPOSE 📋                                               │
│    → findingsをマークダウンで表示                           │
│    → plan_id発行                                            │
│    ↓                                                        │
│ 5. APPROVE ✅                                               │
│    → ユーザーが「APPROVE」で承認                            │
│    → 承認なければ終了（提案のみ）                           │
│    ↓                                                        │
│ 6. EXECUTE 🔧                                               │
│    → 承認されたfindingsのみ修正実行                         │
│    ↓                                                        │
│ 7. VERIFY 🔎 【NEW】                                        │
│    → 修正後に再スキャン                                     │
│    → 退行（Regression）検出、失敗時ロールバック             │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 使用ツール

| Step | ツール |
|:-----|:-------|
| SCAN | `list_dir`, `find_by_name` |
| ANALYZE | `view_file`, `grep_search` |
| DETECT | 内部ルール評価 |
| PROPOSE | `notify_user`（提案表示） |
| APPROVE | ユーザー入力待機 |
| EXECUTE | `replace_file_content`, `write_to_file` |

## 🛡️ 安全策

1. **plan_id必須**: 各提案に一意のIDを付与
2. **影響範囲表示**: 修正対象ファイルとdiff表示
3. **明示的承認**: 「APPROVE」と明示しない限り実行しない
4. **ドライラン**: 実際の修正前に計画を表示

## 💡 Rules

- **7-Step順次実行**
- **提案のみで終了可能**
- **承認後のみ修正実行**
- **Language**: 日本語で報告


## CLI Execution (MVP)

```powershell
python .agent/workflows/check/check.py --help
python .agent/workflows/check/check.py --fail-on none
```
