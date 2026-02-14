# Changelog — AGI Kernel

## [0.6.3] - 2026-02-15

### Added
- Lint候補に `target_path` を自動抽出・付与（`_extract_lint_target_path`）
- LLMプロンプトに `target_path` ファイル制約を注入（対象外ファイル変更を禁止）
- `validate_patch_result()` に `target_path` 制約チェック追加
- `GeminiExecutor` に `state` 引数追加 → `log_token_usage` の累積が正しく動作

### Changed
- 終了コード定数化: `EXIT_SUCCESS=0`, `EXIT_PAUSED=1`, `EXIT_LOCK=2`
- `__version__` を `0.6.3` に更新

## [0.6.2] - 2026-02-15

### Fixed
- ~~`test_agi_kernel_state.py:856` SyntaxWarning~~ → raw string化で修正済み (172 passed, 0 warnings)

### OPS — クローズ判定
- [x] テストベースクローズ（172 passed, 0 warnings, スモークテスト正常完了）

## [0.6.1] - 2026-02-15

### Changed
- **P2-b モジュール分割**: `agi_kernel.py` を5つのサブモジュールに分割
  - `state.py`: StateManager / FileLock / 失敗分類 / KI記録
  - `scanner.py`: pytest出力パーサー / Scanner / 候補生成・選択
  - `executor.py`: GeminiClient / パッチ生成・適用・検証
  - `verifier.py`: 検証コマンド実行
  - `webhook.py`: Webhook通知
- **P2-a ログ統一**: 全 `print()` → 構造化 `logging` 移行

### Added
- **P3-a マルチリポ**: `--workspaces` CLIオプション
- **P3-b Webhook堅牢化**: 指数backoff + jitter / 429対応 / 冪等性キー

### Removed
- **P1 旧SDK削除**: `google-generativeai` フォールバック完全削除

## [0.6.0] - 2026-02-13

### Added
- 構造化ログ (`--log-json`)
- 常駐モード (`--loop --interval N`)
- 承認ゲート (`--approve`)
- Webhook通知 (`--webhook-url`)
- Lint重要度フィルタ (`--lint-severity`)
- LLMコスト追跡 (`token_usage`)
- KI構造化記録
