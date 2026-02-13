---
name: Orchestrator PM v1.1.0
description: Goal/Constraints からロードマップ・タスク分解・エージェント割当・override調整を行う計画生成オーケストレーター
capabilities: plan, orchestration
---

# Orchestrator PM SKILL v1.1.0## コンセプト

## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。


ユーザーの `Goal + Constraints` から、次を一貫して生成する統括オーケストレーター。

1. ROADMAP（人間向け）
2. TASK LIST（成果物単位）
3. QUESTIONS（最大3つ）
4. MACHINE_JSON（機械可読）
5. Dispatch Queue（実行接続ヒント）

MVPは**計画生成のみ**。実行そのものは行わず、後段（例: /check）に委譲する。

## 入出力仕様

### 入力

- `Goal: str`（必須）
- `Constraints: list[str]`（任意）
- `AvailableAgents: list[agent]`（任意。未指定時は `.agent/workflows` から自動検出）
- `UserOverrides: list[override]`（任意）

### 出力（必須）

1. ROADMAP
2. TASK LIST
3. QUESTIONS（最大3）
4. MACHINE_JSON（`BEGIN_JSON` / `END_JSON`）
5. Dispatch Queue（`command_hint` 付き）

## ルーティングルール

- 外部事実・最新情報が必要: `ANTIGRAVITY:/research`
- 要件整理・意思決定が必要: `ChatGPTDesktop:consult_to_saturation`
- 仕様確定済みで実装可能: `ANTIGRAVITY:/code`
- 品質/安全重視: `ANTIGRAVITY:/check` を gate に含める

## 能力ベース拡張

- `available_agents[].capabilities` がある場合は能力一致を優先する
- `required_capabilities` をタスクに付与すると能力ベースで再割当する
- `command_template` があれば dispatch の `command_hint` をテンプレート生成する

## タスク設計ルール

各タスクは次を持つ:

- `id`
- `title`
- `deliverable`（必須）
- `gate`（必須）
- `dependencies`
- `risk`（`low | med | high`）

追加ルール:

- `risk=high` は安全ゲートを必須化する
- 一覧は成果物単位のみ。詳細手順は `EXPAND` で展開する
- 依存関係はトポロジカルに解決する

## Override コマンド

- `ADD_AGENT name=... desc=... [workflow_path=...] [capabilities=...] [command_template=...]`
- `ASSIGN task=T3 agent=...`
- `INSERT_TASK after=T2 id=T2b title="..." [agent=...] [deliverable=...] [gate=...] [risk=low|med|high]`
- `SET_GATE task=T4 gate="..."`
- `EXPAND task=T2`
- `LOCK_ORDER tasks=T1,T2,T3`

CLI実行時は `override> ` で受け付ける。適用後は roadmap/questions/dispatch_queue を再生成する。

## MACHINE_JSON 例

```json
{
  "goal": "...",
  "success_criteria": ["..."],
  "constraints": ["..."],
  "roadmap": [{"phase": "...", "summary": "...", "agents": ["..."]}],
  "tasks": [
    {
      "id": "T1",
      "title": "...",
      "objective": "...",
      "assigned_agent": "...",
      "deliverable": "...",
      "gate": "...",
      "dependencies": [],
      "risk": "low"
    }
  ],
  "questions_to_user": ["..."],
  "dispatch_queue": [
    {
      "task_id": "T1",
      "assigned_agent": "...",
      "depends_on": [],
      "command_hint": "..."
    }
  ],
  "supported_overrides": ["ADD_AGENT", "ASSIGN", "INSERT_TASK", "SET_GATE", "EXPAND", "LOCK_ORDER"]
}
```

## Rules

- 計画生成のみ（実行しない）
- MACHINE_JSON は必ずパース可能
- タスクは成果物単位で表現
- `risk=high` には安全ゲートを付与
- QUESTIONS は最大3
- 報告言語は日本語

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
