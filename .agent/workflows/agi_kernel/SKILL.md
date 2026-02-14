---
name: AGI Kernel v0.5.0
description: 自己改善ループ（AGIカーネル）— リポジトリスキャン・タスク生成・状態管理・学習記録
---

# AGI Kernel SKILL v0.5.0

**リポジトリの健全性を定期スキャンし、改善タスクを生成・実行・検証・記録する自己改善ループの技術仕様。**

## 役割境界

- この SKILL.md は技術仕様（Phase定義・state.jsonスキーマ・判定基準・失敗分類）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。

---

## 📖 概要

AGI Kernelは1サイクルで以下を行う：

1. リポジトリの健全性をスキャン（workflow_lint, pytest等）
2. 検出された課題からタスク候補を生成
3. 優先度に基づきタスクを1つだけ選択
4. 選択タスクを実行・検証
5. 結果を学習記録（KI Learning）
6. 状態をcheckpoint保存（再開可能）

> [!IMPORTANT]
> **暴走防止**: 1サイクルで処理するタスクは**1つだけ**。
> **安全弁**: 同一タスク3回失敗で `PAUSED` にして即停止（exit code 1）。
> **v0.5.0**: auto_fixable判定により、修正不可能なタスクは自動スキップ。

### v0.5.0 主要変更

| 機能 | 説明 |
|:-----|:-----|
| **nodeid分割** | pytest失敗をnodeid単位で候補分割（精密な検証・修正） |
| **auto_fixable判定** | `annotate_candidates()` で修正可否を判定。不可候補は `blocked_candidates` に分類 |
| **select_taskフィルタ** | `auto_fixable=false` 候補は選択対象から除外 |
| **環境ブロッカー** | preflight失敗はfailure_logに積まず即`PAUSED`+exit 1 |
| **PAUSED即停止** | `record_failure()` が `paused_now=True` を返したら即停止 |
| **report強化** | `blocked_candidates` + `no_fixable_candidates` reason を追加 |

---

## 🔄 Phase定義

```
BOOT → SCAN → SENSE → SELECT → EXECUTE → VERIFY → LEARN → CHECKPOINT
```

| Phase | 目的 | 入力 | 出力 |
|:------|:-----|:-----|:-----|
| **BOOT** | 初期化・state読込 | CLI引数, state.json | RunContext |
| **SCAN** | リポジトリスキャン | リポジトリ | スキャン結果（lint, pytest） |
| **SENSE** | タスク候補生成 | スキャン結果 | candidates.json |
| **SELECT** | タスク1つ選択 | candidates | selected_task |
| **EXECUTE** | タスク実行 | selected_task | execution_result |
| **VERIFY** | 結果検証 | execution_result | verification_result |
| **LEARN** | 学習記録 | verification_result | KI Learning記録 |
| **CHECKPOINT** | 状態保存 | 全Phase結果 | state.json更新 |

---

## 📁 state.json スキーマ

保存先: `_outputs/agi_kernel/state.json`

```json
{
  "version": "0.2.0",
  "cycle_id": "20260214_005300",
  "phase": "CHECKPOINT",
  "last_completed_phase": "CHECKPOINT",
  "status": "COMPLETED",
  "started_at": "2026-02-14T00:53:00+09:00",
  "completed_at": "2026-02-14T00:55:00+09:00",
  "scan_results": {
    "workflow_lint_errors": 0,
    "pytest_errors": 0,
    "pytest_failures": 0,
    "total_issues": 0
  },
  "candidates": [],
  "selected_task": null,
  "execution_result": null,
  "verification_result": null,
  "failure_log": [],
  "paused_tasks": []
}
```

### フィールド定義

| フィールド | 型 | 説明 |
|:-----------|:---|:-----|
| `version` | string | スキーマバージョン |
| `cycle_id` | string | サイクル識別子（`YYYYMMDD_HHMMSS`） |
| `phase` | enum | 現在実行中のPhase（クラッシュ検出用） |
| `last_completed_phase` | enum/null | 最後に完了したPhase（resume判定用） |
| `status` | enum | `RUNNING` / `COMPLETED` / `FAILED` / `PAUSED` |
| `started_at` | ISO8601 | サイクル開始時刻 |
| `completed_at` | ISO8601 | サイクル完了時刻（null可） |
| `scan_results` | object | SCANフェーズの結果 |
| `candidates` | array | 生成されたタスク候補一覧 |
| `selected_task` | object/null | 選択されたタスク |
| `execution_result` | object/null | 実行結果 |
| `verification_result` | object/null | 検証結果 |
| `failure_log` | array | 失敗履歴（タスクごと） |
| `paused_tasks` | array | 3回失敗でPAUSEDになったタスクID一覧 |

---

## 🎯 タスク生成ルール

候補生成の優先順位：

| 優先度 | ソース | 例 |
|:------:|:-------|:---|
| 1 | `workflow_lint` ERROR | 必須ファイル不足、バージョン不一致 |
| 2 | `pytest` FAIL | テスト失敗 |
| 3 | `repo_hygiene` | 不要ファイル、ドキュメント不整合 |

### タスク候補JSON形式

```json
{
  "task_id": "fix_lint_wl_disc_001",
  "source": "workflow_lint",
  "priority": 1,
  "title": "architecture.md に agi_kernel を追記",
  "description": "WL-DISC-001: .agent/workflows/agi_kernel が未記載",
  "estimated_effort": "low"
}
```

---

## ⚠️ 失敗分類

| カテゴリ | 判定基準 | 対応 |
|:---------|:---------|:-----|
| `TRANSIENT` | timeout, network | リトライ（max 3回） |
| `DETERMINISTIC` | TypeError, FAILED | 修正必要 |
| `ENVIRONMENT` | ModuleNotFoundError | 環境修復 |
| `FLAKY` | intermittent | 隔離 |
| `POLICY` | permission denied | 即停止 |

### 再開ルール

- `--resume` 指定時、`state.json` を読み込んで `last_completed_phase` の次から再開
- `phase` は「開始済み」、`last_completed_phase` は「完了済み」を示す
- クラッシュ時: `phase ≠ last_completed_phase` → そのフェーズから再実行
- `paused_tasks` に含まれるタスクは選択しない
- 同一タスクの `failure_log.count >= 3` → `paused_tasks` に追加して PAUSED

---

## 📂 出力先

| パス | 内容 | Git追跡 |
|:-----|:-----|:-------:|
| `_outputs/agi_kernel/state.json` | 最新状態 | ✖ |
| `_outputs/agi_kernel/state.json.bak` | 前回保存のバックアップ | ✖ |
| `_outputs/agi_kernel/lock` | 多重起動防止ロック | ✖ |
| `_outputs/agi_kernel/{YYYYMMDD}/{cycle_id}/candidates.json` | タスク候補 | ✖ |
| `_outputs/agi_kernel/{YYYYMMDD}/{cycle_id}/report.json` | サイクルレポート | ✖ |
| `_outputs/agi_kernel/{YYYYMMDD}/latest_*.json` | 最新コピー | ✖ |
| `_logs/autonomy/agi_kernel/` | WorkflowLoggerログ | ✖ |

---

## 📚 KI Learning統合

```python
# 安定したインポート（環境変数 > デフォルトパス > Null Client）
import sys
from pathlib import Path
sys.path.insert(0, str(Path('.agent/workflows/shared')))
from ki_learning_hook import report_action_outcome
```

### 記録タイミング

| イベント | Outcome | 記録する情報 |
|:---------|:--------|:-------------|
| サイクル成功 | SUCCESS | cycle_id, task_id, duration |
| サイクル失敗 | FAILURE | error_type, category, root_cause |
| 部分成功 | PARTIAL | completed_phases, failed_phase |

---

## 🔧 ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

```python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import run_logged_main
```

ログ保存先: `_logs/autonomy/agi_kernel/{YYYYMMDD}/`

---

## 💡 Rules

- **1サイクル1タスク**: 暴走防止
- **3回失敗で PAUSED**: 無限ループ防止
- **dry-runデフォルト推奨**: 破壊的操作は禁止
- **state保存必須**: 中断しても再開可能
- **Language**: 日本語

### v0.2.0 追加ルール

- **Atomic Write**: state.jsonはtmp+fsync+os.replaceで保存
- **Backup/復旧**: save前に.bakを作成、load時に.bakフォールバック
- **Lockfile**: `_outputs/agi_kernel/lock` で多重起動防止（TTL=600sでstale回収）
- **Phase Checkpoint**: 各Phase完了時に `last_completed_phase` を更新、--resumeでその次から再開
- **cycle_id分離**: 出力を `{YYYYMMDD}/{cycle_id}/` に保存、latestコピーも作成

### v0.3.1 EXECUTE/VERIFY 安全強化ルール

- **Executor抽象**: `Executor` ABCで差し替え可能（現在: `GeminiExecutor`）
- **環境変数**: `GOOGLE_API_KEY` / `GEMINI_API_KEY` どちらかで動作
- **Preflight**: `_preflight_check` — EXECUTE前にgit状態チェック
- **バックアップ復元**: `_backup_targets` → `_rollback_with_backup`
- **diff検証**: `_compute_patch_diff_lines` — difflib ベース（git非依存）
- **安全制限定数**: `MAX_PATCH_FILES=5`, `MAX_DIFF_LINES=200`, `MAX_LLM_RETRIES=3`

### v0.4.0 候補分割・RESUME安全性・LLMフォールバック

- **pytest候補分割**: 収集エラーをファイル単位に分割して候補化（`_extract_error_blocks`）
  - 各候補に `target_path` を付与（VERIFYターゲット限定に使用）
- **SCAN `--tb=short`**: 原因行（E行）を拾えるように変更
- **Stable task_id**: `_stable_task_id(prefix, *parts)` — sha1先頭10文字で安定過去一致
  - PAUSEDのズレを防止
- **EXECUTE状態永続化**: `modified_files` / `backup_dir` を state.json に保存
  - `_restore_u rollback_context` でRESUME後も復元可能
- **LLMフォールバック**: flashでMAX_LLM_RETRIES失敗 → proで再試行
  - CLI: `--llm-model`, `--llm-strong-model` / env: `AGI_KERNEL_LLM_MODEL`, `AGI_KERNEL_LLM_STRONG_MODEL`
- **VERIFYターゲット限定**: `target_path` がある候補は `pytest <target_path>` で検証
- **auto-commitバグ修正**: `selected.get("id")` → `selected.get("task_id")` に修正
