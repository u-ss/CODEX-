---
name: Monitor Agent v1.0.0
---

# Monitor Agent SKILL v1.0.0**目的**: エージェントエコシステム全体の健康状態を可視化。問題を早期発見。

## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。


> [!IMPORTANT]
> **`/ki-learning`との違い**: `/ki-learning`は記録インフラ。`/monitor`は記録データを**読み取って可視化**する。

---

## 🏗️ アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│  データソース                                                │
│    ├─ KI Learning DB (learning.db)                          │
│    ├─ 会話ログ (brain/*/logs/)                              │
│    ├─ ワークフロー定義 (.agent/workflows/)                  │
│    └─ Git状態 (git log, git status)                         │
├─────────────────────────────────────────────────────────────┤
│  Monitor Agent                                               │
│    ├─ Collector: データ収集                                  │
│    ├─ Analyzer: 集計・異常検知                               │
│    └─ Reporter: ダッシュボード出力                           │
├─────────────────────────────────────────────────────────────┤
│  出力                                                        │
│    ├─ コンソール出力（テーブル形式）                         │
│    ├─ _outputs/monitor/health_report.md                     │
│    └─ アラート（異常検知時）                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 📋 チェック項目

### 1. エージェント実行状況

| 指標 | データソース | 閾値 |
|:-----|:-------------|:-----|
| 直近24h成功率 | learning.db events | ≥ 80% → OK |
| 失敗パターン増加 | failure_patterns | 新規パターン ≥ 3 → WARN |
| CB OPEN数 | locator_stats | ≥ 5 → WARN |
| 未記録エージェント | events vs workflows一覧 | 存在 → INFO |

### 2. KI DB健全性

| 指標 | チェック方法 | 閾値 |
|:-----|:-------------|:-----|
| DBアクセス可能 | SQLite接続テスト | 不可 → ERROR |
| events件数 | COUNT(*) | 0件 → WARN |
| 最終記録時刻 | MAX(ts_end) | 24h超 → WARN |
| テーブル存在 | sqlite_master | 欠損 → ERROR |

### 3. ワークスペース状態

| 指標 | チェック方法 | 閾値 |
|:-----|:-------------|:-----|
| workflow_lint結果 | python tools/workflow_lint.py | ERROR → WARN |
| 未コミット変更数 | git status --short | ≥ 20 → WARN |
| 最終コミット時刻 | git log -1 | 48h超 → INFO |

---

## 📊 出力フォーマット

### コンソール出力

```
═══════════════════════════════════════════════════
  /monitor ヘルスレポート - 2026-02-07 10:00
═══════════════════════════════════════════════════

  エージェント実行状況
  ┌──────────────┬────────┬─────────┬──────────┐
  │ エージェント │ 成功率 │ 失敗数  │ CB状態   │
  ├──────────────┼────────┼─────────┼──────────┤
  │ /desktop     │  87%   │   3     │ 1 OPEN   │
  │ /code        │  95%   │   1     │ 0 OPEN   │
  │ /research    │ 100%   │   0     │ 0 OPEN   │
  └──────────────┴────────┴─────────┴──────────┘

  KI DB: ✅ 正常（events: 42件, 最終: 2h前）
  ワークスペース: ⚠️ 未コミット12件
  workflow_lint: ✅ パス

  総合: ⚠️ WARN（1件の注意事項あり）
═══════════════════════════════════════════════════
```

---

## 📈 重要度レベル

| レベル | 意味 | アクション |
|:-------|:-----|:-----------|
| ✅ OK | 正常 | なし |
| ℹ️ INFO | 参考情報 | 認識のみ |
| ⚠️ WARN | 注意 | 早めに対処推奨 |
| 🔴 ERROR | 異常 | 即対応必要 |

---

## 💡 Rules

- **読み取り専用**: データを変更しない
- **KI Learning DBに直接アクセス**: SQLiteクエリ
- **失敗時もレポート**: 一部取得失敗でも他の項目は表示
- **Language**: 日本語

## CLI実行（MVP）
```powershell
python .agent/workflows/monitor/scripts/health_check.py
python .agent/workflows/monitor/scripts/health_check.py --run-pytest
```

生成成果物:
- `_outputs/monitor/<YYYYMMDD>/health_report.json`

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
