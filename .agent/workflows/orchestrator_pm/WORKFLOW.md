---
name: Orchestrator PM Agent v1.1.0
description: Orchestrator PM Agent v1.1.0
---

# Orchestrator PM Workflow v1.1.0 (`/orchestrator_pm`)

> [!CAUTION]
> **必須**: このファイルと同フォルダの `SKILL.md` を読んでから実行

## 概要

ユーザーの Goal から「ロードマップ → タスク分解 → エージェント割当」を行い、計画を提示する。
実行はしない（後で/checkに接続可能）。

## 前提条件

- `.agent/RULES.md` を確認済み
- SKILL.md を確認済み

## ワークフロー

// turbo-all

### Step 1: INTAKE（入力受取）

```
1. ユーザーから Goal を受け取る
2. Constraints（制約）があれば収集
3. 利用可能エージェント一覧を生成（デフォルト4種 + ユーザー追加分）
4. UserOverrides があれば収集
```

### Step 2: DECOMPOSE（タスク分解）

```
1. Goal と Constraints から成果物単位のタスクに動的分解
2. 各タスクに属性を設定:
   - deliverable（成果物）: 必須
   - gate（ゲート）: 必須
   - risk（リスク）: low/med/high
   - dependencies（依存関係）
3. risk=high のタスクには安全ゲートを必須で含める
4. ★ 手順は書かない（EXPANDで展開）
```

### Step 3: ROUTE（エージェント割当）

```
ルーティングルール:
- 外部事実/最新情報が必要 → ANTIGRAVITY:/research
- 要件が曖昧/意思決定が必要 → ChatGPTDesktop:consult_to_saturation
- 仕様確定+実装可能 → ANTIGRAVITY:/code
- 品質/安全が重要 → ANTIGRAVITY:/check を gate に含める

柔軟性:
- 新規エージェントに `capabilities` を付けると能力ベースで自動割当
- `command_template` を持つエージェントは dispatch_queue の command_hint に反映
```

### Step 4: PRESENT（計画提示 + オーバーライド受付）

```
出力（4セクション必須）:
1. ROADMAP: 人間向けMarkdown
2. TASK LIST: タスクのみ（成果物単位）
3. QUESTIONS: 最大3つ
4. MACHINE_JSON: BEGIN_JSON/END_JSON で囲む
5. Dispatch Queue: 実行接続用キュー（command_hint付き）

オーバーライド受付:
- CLI実行時は stdin 対話で受付（`override> `）
- ADD_AGENT / ASSIGN / INSERT_TASK / SET_GATE / EXPAND / LOCK_ORDER
- ユーザーが指定したら即座に計画を更新して再提示
- オーバーライド適用後は `roadmap` と `questions_to_user` を再生成
- 依存関係はトポロジカル解決し、dispatch_queue を再計算
```

## 出力

- `_outputs/orchestrator_pm/YYYYMMDD/plan.json`（MACHINE_JSON）
- `_outputs/orchestrator_pm/YYYYMMDD/roadmap.md`（人間向け）
- `_outputs/orchestrator_pm/YYYYMMDD/dispatch_queue.json`（実行接続レイヤ）

## 実行コマンド

```powershell
# 基本実行
python エージェント/オーケストレーター/scripts/orchestrator_pm.py --goal "目標"

# 制約付き
python エージェント/オーケストレーター/scripts/orchestrator_pm.py --goal "目標" --constraints "制約1" "制約2"

# 出力先指定
python エージェント/オーケストレーター/scripts/orchestrator_pm.py --goal "目標" --output-dir _outputs/orchestrator_pm/20260208

# 非対話モード（stdinオーバーライド無効）
python エージェント/オーケストレーター/scripts/orchestrator_pm.py --goal "目標" --no-interactive-overrides
```

## 💡 Rules

- **計画生成のみ**（実行はしない）
- **4セクション必須出力**
- **タスクは成果物単位のみ**
- **MACHINE_JSON は必ずパース可能**
- **Language**: 日本語で報告
