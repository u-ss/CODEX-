---
name: Monitor Agent v1.0.0
description: Monitor Agent v1.0.0
---

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと（ルール・技術詳細）

# Monitor Agent v1.0.0 (`/monitor`)

**ダッシュボード型ヘルスチェック**: 全エージェントの健康状態を一目で把握。

> [!CAUTION]
> **必須**: このファイルと同フォルダの`SKILL.md`を読んでから実行

## 📋 Protocol: 3-Step Health Check

```
┌─────────────────────────────────────────────────────────────┐
│ 1. COLLECT 📥                                                │
│    → KI Learning DB読み取り                                  │
│    → ワークフロー一覧スキャン                                │
│    → Git状態取得                                             │
│    ↓                                                        │
│ 2. ANALYZE 📊                                                │
│    → 成功率計算（エージェント別）                             │
│    → 異常パターン検知                                        │
│    → 閾値判定（OK/INFO/WARN/ERROR）                          │
│    ↓                                                        │
│ 3. REPORT 📋                                                 │
│    → コンソールにダッシュボード表示                           │
│    → _outputs/monitor/ にレポート保存                        │
│    → ERROR/WARNがあれば要約表示                               │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 使用ツール

| Step | ツール |
|:-----|:-------|
| COLLECT | `run_command`（SQLite/git）, `list_dir`, `view_file` |
| ANALYZE | 内部計算（Python的な考慮） |
| REPORT | コンソール出力 + `write_to_file` |

## 📊 データ取得コマンド

```powershell
# KI DB状態
python -c "import sqlite3; db=sqlite3.connect('knowledge/learning/learning.db'); print(db.execute('SELECT COUNT(*) FROM events').fetchone())"

# エージェント別成功率
python -c "
import sqlite3
db = sqlite3.connect('knowledge/learning/learning.db')
for row in db.execute('SELECT agent, outcome, COUNT(*) FROM events GROUP BY agent, outcome'):
    print(row)
"

# Git状態
git status --short | Measure-Object -Line
git log -1 --format="%ci"

# workflow_lint
python tools/workflow_lint.py
```

## 💡 Rules

- **3-Step順次実行**
- **読み取り専用**: 一切のデータ変更禁止
- **部分失敗許容**: DB接続失敗でもgit情報は表示
- **Language**: 日本語

## 実行コマンド（MVP）

```powershell
python .agent/workflows/monitor/scripts/health_check.py
python .agent/workflows/monitor/scripts/health_check.py --run-pytest
```

出力:
- `_outputs/monitor/<YYYYMMDD>/health_report.json`
