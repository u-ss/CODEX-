# AGI Kernel v0.6.1 残課題

## Issue 1: `google.generativeai` 完全脱却
- **優先度**: P1
- **概要**: 旧SDK `google.generativeai` への互換フォールバックを完全に除去し、`google-genai` SDK のみに統一する
- **現状**: v0.6.0 で `logger.warning` による移行推奨メッセージを追加済み。コード内に旧SDK対応が残存
- **作業内容**:
  - `_get_genai_client()` から旧SDKフォールバック分岐を削除
  - `_GeminiClientCompat` の旧SDK分岐を除去
  - `requirements.txt` / セットアップドキュメントを更新
  - テスト: `TestSDKCompat` の旧SDK関連テストを廃止ケースに変更

## Issue 2: `print()` → `logger` 完全移行
- **優先度**: P2
- **概要**: `run_cycle()` 内の全 `print()` 呼び出しを `logger.info/warning/error` に置換
- **現状**: v0.6.0 で `logging` 基盤と `_setup_logging()` は追加済みだが、既存の print 文は未移行
- **作業内容**:
  - 全 `print(f"[PHASE]` パターンを `logger.info` に変換
  - エラー系を `logger.error` / `logger.warning` に適切に振り分け
  - テスト: ログ出力のキャプチャテスト追加

## Issue 3: ファイル分割（scanner/executor/verifier）
- **優先度**: P2
- **概要**: 2000行超の `agi_kernel.py` を機能モジュールに分割
- **現状**: 全ロジックが単一ファイルに集約
- **作業内容**:
  - `scanner.py` — Scanner クラス + generate_candidates
  - `executor.py` — Executor + パッチ適用ロジック
  - `verifier.py` — Verifier クラス
  - `state.py` — StateManager + FileLock
  - `agi_kernel.py` — エントリーポイント + run_cycle（オーケストレーション）
  - テスト: import パスの変更に伴うテスト修正

## Issue 4: マルチリポ巡回対応
- **優先度**: P3（v0.7.0 候補）
- **概要**: 複数リポジトリを順次巡回してスキャン・修正を実行
- **作業内容**:
  - `--workspaces` 引数で複数パス指定
  - 各ワークスペース単位で独立サイクルを実行
  - レポートの統合出力

## Issue 5: `_send_webhook` の堅牢化
- **優先度**: P3
- **概要**: Webhook送信のリトライ・タイムアウト制御を強化
- **作業内容**:
  - 指数バックオフリトライ（最大3回）
  - レスポンスステータスのログ記録
  - rate-limit 対応 (429)
