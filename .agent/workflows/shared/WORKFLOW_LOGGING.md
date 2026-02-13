---
name: WorkflowLogger統合ガイド
description: 全エージェント共通のWorkflowLoggerログ記録手順
---

# WorkflowLogger統合ガイド

> [!IMPORTANT]
> 全エージェントは実行時にWorkflowLoggerで各フェーズを記録すること。

## 📊 ログ記録手順

### 1. インポート

```python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
```

### 2. 使用パターン

```python
# エージェント実行全体
with logged_main("{エージェント名}", "{ワークフロー名}") as logger:

    # 各フェーズ
    with phase_scope(logger, "PHASE_NAME", inputs={...}) as p:
        # 処理実行
        p.set_output("key", value)
        p.add_metric("key", value)
```

### 2.1 エントリポイントの必須ラップ（推奨）

```python
from workflow_logging_hook import run_logged_main

def main() -> int:
    ...
    return 0

if __name__ == "__main__":
    raise SystemExit(run_logged_main("research", "research", main, phase_name="RUN"))
```

`run_logged_main` は以下を自動で記録する:
- `PHASE_START/PHASE_END`
- `VERIFICATION_RUN`（`exit_code_zero`）
- `CLAIM`（`evidence_refs` に verification_id を接続）
- `RUN_SUMMARY`（`claimed_success` / `verified_success`）

### 3. エージェント別の使用例

**Research Agent:**
```python
with logged_main("research", "deep_research") as logger:
    with phase_scope(logger, "SEARCH", inputs={"query": query}) as p:
        p.set_output("sources_found", count)
    with phase_scope(logger, "ANALYZE", inputs={"sources": count}) as p:
        p.set_output("summary_length", len(summary))
    with phase_scope(logger, "REPORT", inputs={"format": "markdown"}) as p:
        p.set_output("report_path", path)
```

**Code Agent:**
```python
with logged_main("code", "implementation") as logger:
    with phase_scope(logger, "RESEARCH", inputs={"goal": goal}) as p:
        p.set_output("files_found", n)
    with phase_scope(logger, "PLAN") as p:
        p.set_output("plan_items", count)
    with phase_scope(logger, "TEST") as p:
        p.set_output("tests_created", n)
    with phase_scope(logger, "CODE") as p:
        p.set_output("files_modified", n)
    with phase_scope(logger, "VERIFY") as p:
        p.set_output("pass_rate", "100%")
```

## 📁 ログ保存先

```
_logs/autonomy/{agent}/{YYYYMMDD}/{run_id}.jsonl       ← 詳細ログ
_logs/autonomy/{agent}/{YYYYMMDD}/{run_id}_summary.json ← サマリー
_logs/autonomy/{agent}/latest.json                      ← 最新ポインタ
```

## 🔍 CODEXAPP相談時のログ添付

```python
from workflow_logging_hook import resolve_latest_log, bundle_logs_for_codex

# 最新ログパスを取得
info = resolve_latest_log("research")
# → {"agent": "research", "log_path": "...", "summary_path": "..."}

# CODEXAPP送信用テキスト
bundle = bundle_logs_for_codex("research", last_n=3)
# → テキスト形式のログサマリー
```

## CLI

```bash
# エージェント一覧
python scripts/autonomy/codex_log_resolver.py --list

# 特定エージェントの最新ログ
python scripts/autonomy/codex_log_resolver.py --agent research

# CODEXAPP向けバンドル
python scripts/autonomy/codex_log_resolver.py --agent research --bundle

# 全エージェントバンドル
python scripts/autonomy/codex_log_resolver.py --all --bundle

# claimed_success=true かつ verified_success=false の矛盾検出
python scripts/autonomy/codex_log_resolver.py --mismatches
```

## Schema v1.0（JSONL）

各行は次の共通キーを持つ:

- `schema_version` = `1.0`
- `ts`
- `event_seq`
- `event_type`
- `run_id`
- `trace_id`
- `span_id`
- `parent_span_id`
- `agent`
- `workflow`
- `payload`

主な `event_type`:

- `TASK_RECEIVED`
- `RUN_START`
- `PHASE_START` / `PHASE_END` / `PHASE_DIRECT`
- `STREAM_OUTPUT`（標準出力・標準エラーの逐次記録）
- `TOOL_CALL` / `TOOL_RESULT`
- `ARTIFACT_WRITTEN`
- `VERIFICATION_RUN`
- `CLAIM`
- `RUN_SUMMARY`

`RUN_SUMMARY` には `claimed_success` と `verified_success` が分離して記録される。
`verified_success` は `VERIFICATION_RUN` が全て `pass` の場合のみ `true`。
