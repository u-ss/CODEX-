# AGI Kernel CHANGELOG

## v0.6.0 (2026-02-15)

### ⚠️ 互換性・Breaking Changes
- `_log_token_usage()` の戻り値が `None` → `dict[str, Any]` に変更。引数に `state` を追加
- `_record_ki()` に `metadata` キーワード引数を追加（既存呼び出しは互換あり）
- `Scanner.run_workflow_lint()` に `severity_filter` キーワード引数を追加（デフォルトで後方互換）
- `report.json` に `token_usage` フィールドを追加
- `build_parser()` に新フラグ6個追加（既存CLIは影響なし）

### 新機能
- **構造化ログ**: `logging` モジュール移行 + `_JsonFormatter` + `_setup_logging(json_mode=True)`
- **常駐モード**: `--loop --interval N` でサイクル自動繰り返し（Ctrl+C安全停止）
- **承認ゲート**: `--approve` でパッチ適用前に人間の確認を要求
- **Webhook通知**: `--webhook-url` でサイクル完了/PAUSED時にDiscord/Slack互換通知
- **Lint重要度フィルタ**: `--lint-severity error,caution` でCAUTION/ADVISORY取込
- **JSON構造化ログ出力**: `--log-json` フラグ
- **LLMコスト追跡**: `_COST_PER_1M` 料金テーブル + `token_usage` 累積 + 推定コスト（USD）
- **SDK互換警告**: 旧 `google.generativeai` 使用時に `logger.warning` で移行推奨
- **KI構造化記録**: `_record_ki()` に `metadata` 引数追加（failure_class, error_summary, verification_success, files_modified）
- **Webhook通知関数**: `_send_webhook()` 追加（urllib.request ベース）

### テスト追加
- `TestCLIIntegrationLoop`: --loop 統合テスト2件
- `TestCLIIntegrationApprove`: --approve 統合テスト2件
- `TestCLIIntegrationLogJson`: --log-json 統合テスト2件
- `TestCLIIntegrationLintSeverity`: --lint-severity 統合テスト2件
- `TestCLIIntegrationWebhook`: --webhook-url 統合テスト2件
- `TestReportTokenUsageSchema`: スキーマ固定テスト4件
- `TestSeverityFilter` / `TestCostTracking` / `TestStructuredKI` / `TestWebhook` / `TestCLIArgs` / `TestSDKCompat` / `TestLoggingSetup`
- 合計: 140 → 172 テスト (+32)

---

## v0.5.1 (2026-02-15)

### バグ修正
- **[P2] report/state 整合性修正**: `paused_now` 判定を report 生成前に移動し、`state.json` と `report.json` の `status` が常に一致するように修正
- **[P2] パス検証強化**: `".. " in path_str` → `Path.parts` でコンポーネント単位検出に変更。`startswith()` 文字列比較 → `Path.relative_to()` に変更し、prefix衝突脆弱性を解消
- **[P3] `__version__`**: `0.4.0` → `0.5.1` に更新

### テスト追加
- `TestPathValidationBoundary`: パス検証の境界ケース6件（`..`コンポーネント, ファイル名含み`..`, 絶対パス, prefix衝突, 正常ネスト）
- `TestPausedNowReportConsistency`: paused_now 回帰テスト3件（MAX到達, PAUSED除外, 重複追加防止）
- 合計: 131 → 140 テスト

---

## v0.5.0 (2026-02-14)

### 新機能
- **nodeid分割**: pytest失敗を `nodeid` 単位で候補分割（精密な検証・修正）
- **auto_fixable判定**: `annotate_candidates()` で修正可否を判定。不可候補は `blocked_candidates` に分類
- **select_taskフィルタ**: `auto_fixable=false` 候補は選択対象から除外
- **環境ブロッカー**: preflight失敗は `failure_log` に積まず即 `PAUSED` + exit 1
- **PAUSED即停止**: `record_failure()` が `paused_now=True` を返したら即停止
- **report強化**: `blocked_candidates` + `no_fixable_candidates` reason を追加

### テスト追加
- `TestFailureNodes`: `_extract_failure_nodes` テスト7件
- `TestNodeidSplitting`: `generate_candidates` nodeid分割テスト3件
- `TestAutoFixable`: `annotate_candidates` テスト7件
- `TestSelectAutoFixable`: `select_task` auto_fixableフィルタテスト4件
- `TestRecordFailurePaused`: `record_failure` paused_now戻り値テスト3件
- `TestVerifierNodeid`: `Verifier` target_nodeid対応テスト2件

---

## v0.4.0 以前

初期実装。8フェーズパイプライン、LLMパッチ生成、状態永続化、失敗管理、KI Learning。
