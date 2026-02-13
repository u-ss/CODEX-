# データクリーンエージェント

リポジトリ内のランタイム生成物の掃除と、衛生チェックを担当する部署。

## ワークフロー定義（正）

- 論理層: `.agent/workflows/ops/`（`clean` サブコマンド）
  - `SKILL.md`: 技術仕様
  - `WORKFLOW.md`: 実行手順

## 使い方

```powershell
# 安全（dry-run）
python .agent/workflows/ops/scripts/ops.py clean --dry-run

# 実行（削除あり）
python .agent/workflows/ops/scripts/ops.py clean --no-dry-run
```

補助スクリプト（互換/残置）:
- `scripts/data_clean_safe.ps1`
