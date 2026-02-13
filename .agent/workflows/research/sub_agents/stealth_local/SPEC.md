# Stealth Local Research Agent SKILL v1.0.0## コンセプト

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


既存 `/research` は変更せず、完全新規の研究実行系を別ワークフローとして提供する。

- 収集: `Stealth Research Tool v2.1`（既存モジュールを再利用）
- 推論: ローカル `Ollama`（`qwen3:14b` + `gpt-oss:20b`）
- 出力: 監査可能な成果物を新規出力先へ保存

## 入出力

### 入力

- `--goal` (必須、`--jobs-file` 未使用時)
- `--focus` (任意)
- `--jobs-file` (任意、JSON/JSONLバッチ)

### 出力

`_outputs/research_stealth_local/<session>/`

- `final_report.md`
- `search_log.md`
- `audit_pack.json`
- `evidence.jsonl`
- `verified_claims.jsonl`
- `run_summary.json`
- `raw_claims.jsonl`
- `normalized_claims.jsonl`
- `stealth_run/`（Stealth v2.1のトレース/サマリー）

## 実行フェーズ

1. QUERY_PLAN
2. STEALTH_COLLECT
3. EXTRACT_CLAIMS
4. NORMALIZE
5. VERIFY
6. REPORT
7. SAVE_ARTIFACTS

## 自動化

- `--jobs-file` で複数研究ジョブを連続実行
- 失敗ジョブのみ `batch_summary.json` で明示
- 実運用は Task Scheduler から本スクリプトを直接呼び出し可能

## ルール

- 既存 `/research` のコードは編集しない
- 既存 `stealth_research` は読み取り・再利用のみ
- 研究系成果物として `search_log.md` を必ず出力
- 日本語でレポート生成

## ログ記録（WorkflowLogger統合）

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path('.agent/workflows/shared')))
from workflow_logging_hook import run_logged_main
```

