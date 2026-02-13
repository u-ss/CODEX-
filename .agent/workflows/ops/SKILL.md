---
name: Ops Workflow v1.0.0
---

# Ops Workflow v1.0.0 (`/ops`)## Purpose

## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。


Consolidate operational workflows under a single entrypoint to reduce navigation overhead.

## Command

```powershell
python .agent/workflows/ops/scripts/ops.py <subcommand> [options]
```

## Subcommands

- `health`: run monitor health checks
- `deploy --dry-run`: run safe pre-deploy checks (requires clean worktree, then runs `workflow_lint`, minimal pytest)
- `clean --dry-run`: run `tools/clean_runtime_artifacts.ps1 -DryRun` when available
- `clean --no-dry-run`: run `tools/clean_runtime_artifacts.ps1` (deletes runtime artifacts)
- `doc-sync --check`: run `workflow_lint` and `repo_hygiene_check` when available
- `doc-sync --agent-map`: generate workflow/physical-agent mapping with `folder_analyzer.py --agent-map`
- `doc-sync --workspace-scan`: generate full workspace structure report with `folder_analyzer.py --workspace`
- `doc-sync --check --agent-map`: run lint checks and mapping in one run

## Outputs

- Artifacts: `_outputs/ops/<YYYYMMDD>/`
- Logs: `_logs/ops.jsonl`

## Rules

- Default to non-destructive behavior
- External dependencies are not allowed
- Keep all operational artifacts under `_outputs/ops/`

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
