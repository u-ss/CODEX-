---
name: AGI Kernel v0.2.0
description: 自己改善ループ（AGIカーネル）— リポジトリスキャン・タスク生成・状態管理・学習記録
---

# AGI Kernel v0.2.0 (`/agi_kernel`)

**リポジトリの健全性をスキャンし、改善タスクを1つずつ処理する自己改善ループ。**
状態を保存し、中断しても再開できる。

> [!CAUTION]
> **必須**: このファイルと同フォルダの`SKILL.md`を読んでから実行

## 📋 Protocol: 8-Phase Self-Improvement Loop

```
┌─────────────────────────────────────────────────────────────┐
│  1. BOOT 🔧                                                 │
│     → state.json 読込（--resume時）、初期化                 │
│     ↓                                                       │
│  2. SCAN 🔍                                                 │
│     → workflow_lint, pytest 実行、結果収集                   │
│     ↓                                                       │
│  3. SENSE 📊                                                │
│     → スキャン結果からタスク候補を生成                       │
│     → candidates.json に出力                                │
│     ↓                                                       │
│  4. SELECT 🎯                                               │
│     → 優先度でソート、PAUSEDを除外、1つだけ選択             │
│     ↓                                                       │
│  5. EXECUTE ✏️ (dry-run時スキップ)                           │
│     → 選択タスクを実行                                      │
│     ↓                                                       │
│  6. VERIFY ✅ (dry-run時スキップ)                            │
│     → 実行結果を検証                                        │
│     ↓                                                       │
│  7. LEARN 📝                                                │
│     → KI Learningに結果記録（成功/失敗/部分成功）           │
│     ↓                                                       │
│  8. CHECKPOINT 💾                                           │
│     → state.json 保存、レポート出力                         │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 スクリプト実行

### 基本コマンド

```powershell
# ヘルプ表示
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --help

# 1サイクル実行（dry-run: EXECUTE/VERIFYスキップ）
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --once --dry-run

# 1サイクル実行（本番）
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --once

# 中断からの再開（dry-run）
python "エージェント/AGIカーネル/scripts/agi_kernel.py" --resume --dry-run
```

### CLIフラグ

| フラグ | 説明 |
|:-------|:-----|
| `--once` | 1サイクルのみ実行して終了 |
| `--resume` | state.jsonから再開 |
| `--dry-run` | EXECUTE/VERIFYフェーズをスキップ |
| `--workspace` | ワークスペースルート（デフォルト: `.`） |

## 📂 出力先

| パス | 内容 |
|:-----|:-----|
| `_outputs/agi_kernel/state.json` | 最新状態 |
| `_outputs/agi_kernel/state.json.bak` | バックアップ |
| `_outputs/agi_kernel/lock` | 多重起動防止ロック |
| `_outputs/agi_kernel/{YYYYMMDD}/candidates.json` | タスク候補 |
| `_outputs/agi_kernel/{YYYYMMDD}/report.json` | サイクルレポート |

## 🔄 Phase詳細チェックリスト

### Phase 1: BOOT
```
□ CLI引数をパース
□ --resume なら state.json を読込
□ cycle_id を生成（YYYYMMDD_HHMMSS）
□ WorkflowLogger 開始
```

### Phase 2: SCAN
```
□ python tools/workflow_lint.py を実行
□ python -m pytest -q を実行
□ 結果をパースして scan_results に格納
```

### Phase 3: SENSE
```
□ scan_results からタスク候補を生成
□ 優先度付与（workflow_lint > pytest > hygiene）
□ candidates.json に保存
```

### Phase 4: SELECT
```
□ paused_tasks に含まれるタスクを除外
□ 優先度順にソート
□ 先頭の1つだけ selected_task に設定
□ 候補なしの場合: status=COMPLETED で早期終了
```

### Phase 5: EXECUTE (dry-run時スキップ)
```
□ selected_task の内容を実行
□ 実行結果を execution_result に格納
□ 失敗時: 失敗分類（TRANSIENT/DETERMINISTIC/ENVIRONMENT/FLAKY/POLICY）
```

### Phase 6: VERIFY (dry-run時スキップ)
```
□ 実行後の状態を検証
□ verification_result に格納
```

### Phase 7: LEARN
```
□ KI Learning に report_action_outcome で記録
□ 失敗時: failure_log に追加
□ 3回失敗: paused_tasks に追加
```

### Phase 8: CHECKPOINT
```
□ state.json を atomic write で保存（tmp+fsync+replace）
□ state.json.bak を作成
□ report.json を出力
□ lockfile を解放
□ WorkflowLogger 終了
```

## ⚠️ 安全弁

- **1サイクル1タスク**: 暴走防止
- **3回失敗 → PAUSED**: 無限ループ防止
- **dry-runデフォルト推奨**: 破壊的操作は実行しない
- **POLICY失敗 → 即停止**: permission denied 等

## 💡 Rules

- **8-Phase順次実行**
- **--once を推奨**: 最初は1サイクルずつ確認
- **失敗/成功を記録**（KI Learning連携）
- **Language**: 日本語で報告
