---
name: Ops Agent v1.0.0
description: Ops Agent v1.0.0
---

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと（ルール・技術詳細）

# Ops Workflow v1.0.0 (`/ops`)

## Goal

Use one operational entrypoint instead of multiple fragmented workflows.

## Execution flow

1. Select an operation (`health`, `deploy`, `clean`, `doc-sync`)
2. Run subcommand from `ops.py`
3. Save artifacts to `_outputs/ops/<YYYYMMDD>/`
4. Record events to `_logs/ops.jsonl`

## Examples

```powershell
python .agent/workflows/ops/scripts/ops.py --help
python .agent/workflows/ops/scripts/ops.py health
python .agent/workflows/ops/scripts/ops.py deploy --dry-run
python .agent/workflows/ops/scripts/ops.py clean --dry-run
python .agent/workflows/ops/scripts/ops.py clean --no-dry-run
python .agent/workflows/ops/scripts/ops.py doc-sync --check
python .agent/workflows/ops/scripts/ops.py doc-sync --agent-map
python .agent/workflows/ops/scripts/ops.py doc-sync --check --agent-map
python .agent/workflows/ops/scripts/ops.py doc-sync --check --workspace-scan
```

> Note: `deploy --dry-run` は作業ツリーがdirtyの場合に失敗します（意図したゲート）。

## Migration note

`deploy`, `report`, `schedule`, `data-clean`, and `doc-sync` are consolidated into `/ops`.
