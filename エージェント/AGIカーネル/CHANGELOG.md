# Changelog — AGI Kernel

## [0.6.2] - Unreleased

### Fixed
- ~~`test_agi_kernel_state.py:856` SyntaxWarning~~ → raw string化で修正済み (172 passed, 0 warnings)

### OPS — クローズ判定基準（24h運用後）
- [ ] `--workspaces` 実行が異常終了 0
- [ ] Webhook 成功率 99%+、未送信取りこぼし 0
- [ ] retry/backoff 発火ログが仕様どおり（失敗時は最終 ERROR 記録）
- [ ] 冪等性キー重複による二重送信 0
- [ ] 判定結果を本セクションに追記 → 全チェックで v0.6.2 クローズ

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
