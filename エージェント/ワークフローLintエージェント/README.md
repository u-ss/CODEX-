# ワークフローLintエージェント

`.agent/workflows/workflow_lint/` の lint を実行して、WORKFLOW/SKILL/README の欠落・矛盾を検出します。

- 正本（手順）: `.agent/workflows/workflow_lint/WORKFLOW.md`
- 正本（技術仕様）: `.agent/workflows/workflow_lint/SKILL.md`

## 実行

```powershell
python .agent/workflows/workflow_lint/scripts/workflow_lint.py
```

元ツール（出力のみ）:

```powershell
python tools/workflow_lint.py
```
