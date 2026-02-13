# ドキュメント同期エージェント

ワークフロー/衛生チェックを通して、ドキュメントと実装の整合性を保つ部署。

## ワークフロー定義（正）

- 論理層: `.agent/workflows/ops/`（`doc-sync` サブコマンド）
  - `SKILL.md`: 技術仕様
  - `WORKFLOW.md`: 実行手順

## 使い方

```powershell
python .agent/workflows/ops/scripts/ops.py doc-sync --check
```

補助スクリプト（互換/残置）:
- `scripts/doc_sync_check.py`

