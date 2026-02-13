# Workflow Logger Schema v1.0

## 保存先

- 詳細ログ: `_logs/autonomy/{agent}/{YYYYMMDD}/{run_id}.jsonl`
- 実行サマリー: `_logs/autonomy/{agent}/{YYYYMMDD}/{run_id}_summary.json`
- 最新ポインタ: `_logs/autonomy/{agent}/latest.json`
- アーティファクト: `_logs/autonomy/{agent}/{YYYYMMDD}/artifacts/{run_id}/`

## イベント共通キー

- `schema_version` (`"1.0"`)
- `ts` (ISO8601 UTC)
- `event_seq` (run内連番)
- `event_type`
- `run_id`
- `trace_id`
- `span_id`
- `parent_span_id`
- `agent`
- `workflow`
- `payload`

## 主要イベント

- `TASK_RECEIVED`: 受信したゴール/受け入れ条件
- `RUN_START`: 実行開始メタ情報
- `PHASE_START` / `PHASE_END`: フェーズ実行境界
- `PHASE_DIRECT`: 単発フェーズ記録
- `STREAM_OUTPUT`: stdout/stderrの逐次行ログ
- `TOOL_CALL` / `TOOL_RESULT`: `call_id`で対応付け
- `ARTIFACT_WRITTEN`: 大きな本文を別保存した参照
- `VERIFICATION_RUN`: 検証チェック結果
- `CLAIM`: 主観的成功主張（`evidence_refs`必須）
- `RUN_SUMMARY`: 実行集計（`claimed_success` と `verified_success` を分離）

## 成功判定ルール

- `claimed_success=true` は `evidence_refs` が無い場合は自動で抑止される
- `verified_success=true` は `VERIFICATION_RUN` が存在し、かつ全件 `pass=true` の場合のみ

## 参照CLI

- 一覧: `python scripts/autonomy/codex_log_resolver.py --list`
- 直近取得: `python scripts/autonomy/codex_log_resolver.py --agent <agent>`
- バンドル表示: `python scripts/autonomy/codex_log_resolver.py --agent <agent> --bundle`
- 矛盾検出: `python scripts/autonomy/codex_log_resolver.py --mismatches`
