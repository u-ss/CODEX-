---
name: Implementation Agent (Code) v4.2.4
---

# Implementation Agent SKILL v4.2.4
**技術詳細**: 各Phase の詳細手順とチェックリスト。


## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。


## 📝 Phase詳細

### Phase 1: RESEARCH 🔍

**目的**: 既存コードベースを深く理解してから計画を立てる

```
必須アクション:
1. list_dir でプロジェクト構造把握
2. grep_search で関連コード検索（キーワード/関数名）
3. view_file_outline で主要ファイルの構造確認
4. 既存の実装パターン・規約を把握

★ v4.2: RunContext出力
- codebase_map: entrypoints, key_modules, run_commands
- evidence: grep結果（path + line_range + snippet_hash）
```

### Phase 2: PLAN 📐 + Plan Lint

**目的**: 計画策定と機械的検証

```
Plan Lint必須セクション:
[x] Scope - 対象範囲
[x] Acceptance Criteria - 受け入れ条件
[ ] Non-goals - 非目標（warning）
[x] Targets - 変更対象ファイル
[x] Test Strategy - テスト戦略
[ ] Risk Controls - リスク対策（warning）
[ ] Rollback - ロールバック手順（warning）

★ v4.2: Evidence必須
- 各変更対象にgrep/view結果を紐づけ
- Evidence不足 → PLAN失敗
```

### Phase 3: TEST 🧪 (TDD + 差分駆動)

**目的**: テスト先行で期待動作を定義

```
★ v4.2: 差分駆動テスト選択
1. Smoke（最小スモーク）→ まず通す
2. Diff-related（差分関連）→ 変更に関連するテスト
3. Full（フル）→ 時間が許す場合

適用条件:
- 新規関数/クラス追加 → TDD
- ロジック変更 → TDD
- バグ修正 → TDD

スキップ時の代替ゲート:
- UI: snapshot, accessibility, e2e_smoke
- Doc: link_check, example_code_run, markdown_lint
- Config: schema_validation, load_test
```

### Phase 4: CODE ✏️

**目的**: テストを通す最小限の実装

```
ルール:
- 小さな変更を段階的に
- テストが通ることを確認しながら進める
- 既存コードスタイルに合わせる

★ v4.2: RunContext更新
- execution_trace: cmd, exit_code, stdout_hash
```

### Phase 5: DEBUG 🔧 + Self-Healing

**目的**: 動作確認とエラー修正

```
★ v4.2: Self-Healing v1

1. 失敗分類:
   - TRANSIENT: リトライ価値あり（timeout, network）
   - DETERMINISTIC: 修正必要（TypeError, FAILED）
   - ENVIRONMENT: 環境修復（ModuleNotFoundError）
   - FLAKY: 隔離（intermittent）
   - POLICY: 即停止（permission denied）

2. 適応リトライ:
   - transient/flaky → リトライ（max 3回）
   - deterministic → 修正 → 再テスト
   - environment → 依存インストール → 再テスト
   - policy → 即停止 → ユーザー報告

3. 局所CB:
   - コマンド/テスト単位でOPEN/HALF/CLOSED管理
   - OPENの対象は代替経路を優先
```

### Phase 6: VERIFY ✅

**目的**: 最終確認

```
★ v4.2: 数値化完了条件

Tier 0（必須）:
[ ] テストpass率 = 100%
[ ] 受け入れ条件 = 100%充足

Tier 1（品質）:
[ ] changed-lines coverage ≥ 80%
[ ] 新規lint errors = 0

Tier 2（運用）:
[ ] flaky検知 = 0
[ ] 失敗再発率 = 0
```

## 🔧 Pythonライブラリ

`lib/`配下のモジュールを活用:
- `context.py`: RunContext, TaskContract, CodebaseMap
- `plan_lint.py`: lint_plan, require_evidence, require_evidence_for_targets
- `self_healing.py`: FailureCategory, CircuitBreaker, classify_failure
- `test_selector.py`: plan_tests, compute_changeset
- `verify.py`: GateEvaluator, ACVerifier, VerdictLogger (v4.2.1)
- `orchestrator.py`: Orchestrator, Phase, PhaseResult (v4.2.1)

### Phase 7: DOCUMENT 📝 (v4.2.1追加)

**目的**: 実装後に関連ドキュメントを同期

```
★ v4.2.1: ドキュメント同期

1. 対象特定:
   - 変更ファイルのパスからエージェント/ワークフローを特定
   - 関連するSKILL.md, WORKFLOW.md, READMEを洗い出す

2. 内容確認:
   - 既存ドキュメントを読む
   - 重複記述がないか確認

3. 更新判断:
   - バージョン番号 → 更新
   - 新規API/機能 → 追記
   - 廃止/削除機能 → 削除
   - モジュール一覧 → 同期

4. 整合性確認:
   - ドキュメントがコードと一致しているか確認
```

## 📚 KI Learning統合（v4.2.3）

> [!IMPORTANT]
> **テスト/ビルド失敗を記録し、同じ失敗を繰り返さない。**

### 共通フックモジュール

`ki_learning_hook`（`.agent/workflows/shared/`）を使用：

```python
# 安定したインポート（環境変数 > デフォルトパス > Null Client）
import sys
from pathlib import Path
sys.path.insert(0, str(Path('.agent/workflows/shared')))
from ki_learning_hook import report_action_outcome, check_risks
```

### 記録タイミング

| イベント | Outcome | 記録する情報 |
|:---------|:--------|:-------------|
| テスト成功 | SUCCESS | test_name, duration_ms |
| テスト失敗 | FAILURE | error_type, root_cause, fix |
| ビルドエラー | FAILURE | module, error_message |
| Self-Healing成功 | PARTIAL | 回復手段, リトライ回数 |

### 使用例

```python
# テスト失敗時の記録
report_action_outcome(
    agent='/code',
    intent_class='test_execution',
    outcome='FAILURE',
    error_type='AssertionError',
    root_cause='fixture_stale',
    fix='recreate fixtures'
)
```

## 💡 Rules

- **7-Phase順次実行**
- **RunContext必須**: 全フェーズが同じ状態を参照
- **Plan Lint必須**: 未充足ならPLANに戻る
- **Self-Healing: 分類→適応リトライ→局所CB**
- **DOCUMENT必須**: 実装後に関連ドキュメントを同期
- **失敗/成功を記録**（KI Learning連携）
- **Language**: 日本語


##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
