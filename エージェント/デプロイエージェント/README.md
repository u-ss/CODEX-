# デプロイエージェント

Git操作・CI連携・事前チェックを自動化するデプロイ部署。

## ワークフロー定義

- 論理層（正）: `.agent/workflows/ops/`（`deploy` サブコマンド）
  - `SKILL.md`: 技術仕様
  - `WORKFLOW.md`: 実行手順

## 使い方

```powershell
# 事前チェック（dry-run）
python .agent/workflows/ops/scripts/ops.py deploy --dry-run
```
