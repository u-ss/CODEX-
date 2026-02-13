# Codex CLI Review Agent SKILL v1.1.0
**技術詳細**: git差分をCodex CLIでチャンク単位にレビューし、指摘事項をcodex execで自動修正するループの技術仕様。

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。

## 🎯 概要

2つのPowerShellスクリプトで構成されるCodex CLIベースのレビュー＆自動修正パイプライン:

| スクリプト | パス | 役割 |
|:-----------|:-----|:-----|
| **review_and_fix.ps1** | `tools/review_and_fix.ps1` | オーケストレーター（レビュー→修正→再検証ループ） |
| **codex_cli_diff_review.ps1** | `tools/codex_cli_diff_review.ps1` | レビュー実行（チャンク分割→`codex review`呼び出し） |

```
git diff → チャンク分割 → codex review → Findings判定
                                          ├─ なし → 完了 ✅
                                          └─ あり → codex exec で修正 → 再レビュー（ループ）
```

## 📐 パラメータ一覧

### review_and_fix.ps1（メイン）

| パラメータ | 型 | デフォルト | 説明 |
|:-----------|:---|:-----------|:-----|
| `$Base` | string | `"main"` | 比較対象ブランチ |
| `$ChunkSize` | int | `8` | 1チャンクあたりのファイル数 |
| `$SplitThreshold` | int | `8` | この数以下ならチャンク分割しない |
| `$ReviewRetryCount` | int | `2` | レビュー失敗時のリトライ回数 |
| `$FixRetryCount` | int | `1` | 修正失敗時のリトライ回数 |
| `$FixRounds` | int | `1` | レビュー→修正の最大ラウンド数 |
| `$Uncommitted` | switch | - | コミット前の変更を対象にする |
| `$DryRun` | switch | - | レビュープロンプトを生成するのみ（実行しない） |
| `$ReviewOnly` | switch | - | レビューのみ（修正をスキップ） |
| `$SkipFinalVerification` | switch | - | 修正後の最終検証をスキップ |
| `$UseFullAuto` | bool | `$false` | Codex の `--full-auto` モード |
| `$CodexModel` | string | `""` | 使用するCodexモデル |
| `$OutputDir` | string | 自動生成 | 出力ディレクトリ |
| `$ExtraReviewInstructions` | string | `""` | レビュー時の追加指示 |
| `$ExtraFixInstructions` | string | `""` | 修正時の追加指示 |
| `$MaxReviewCharsPerChunk` | int | `12000` | チャンクあたりのレビュー最大文字数 |
| `$ReviewModel` | string | `""` | レビュー時のCodexモデル（空=デフォルト） |
| `$ReasoningEffort` | string | `""` | 思考レベル: low/medium/high/xhigh（空=デフォルト） |
| `$CodexTimeoutSeconds` | int | `300` | Codex実行タイムアウト（秒、review/fix共通） |
| `$HealthCheckTimeoutSeconds` | int | `45` | 実レビュー前のCodex疎通チェック上限（秒） |
| `$FailFastOnCodexTimeout` | bool | `$true` | タイムアウト/通信障害時に残りチャンクを即スキップ |
| `$SkipCodexHealthCheck` | switch | - | 疎通チェックを明示的に無効化 |
| `$ExcludeGlobs` | string[] | 下記参照 | 除外パターン |

### デフォルト除外パターン

```
_outputs/*, _logs/*, _temp/*, _screenshots/*,
*.png, *.jpg, *.jpeg, *.webp, *.gif, *.mp4, *.mp3, *.wav, *.blend
```

## 🔧 チャンク分割ロジック

```
変更ファイル一覧取得
  → ExcludeGlobsでフィルタリング
  → ファイル数 ≤ SplitThreshold → 1チャンク
  → ファイル数 > SplitThreshold → ChunkSize単位で分割
  → 各チャンクに chunk_XXX_files.txt を生成
```

## 🔍 Findings判定ロジック

レビュー結果は以下のルールで判定:

| 条件 | has_findings | reason |
|:-----|:------------|:-------|
| DryRunモード | `null` | `dry_run` |
| 空パッチチャンク | `false` | `empty_patch` |
| レビュー結果が空 | `null` | `empty_review_output` |
| TIMEOUT/実行例外/空応答 | `null` | `review_execution_error` / `review_execution_timeout` |
| 認証/通信/レート制限エラー（失敗チャンクのみ判定） | `null` | `review_transport_error` |
| `"No findings in scoped files."` に一致 | `false` | `no_findings_phrase` |
| ステータス失敗（その他） | `null` | `review_failed` |
| 上記以外（OKかつ内容あり） | `true` | `findings_present` |

## 📤 修正プロンプト構成

`codex exec` に渡すプロンプトの構造:

```
Fix the issues reported in the review findings for chunk {ChunkId} (round {Round}).
Scope: {scope}

Constraints:
1. Edit only files listed in "Files in scope".
2. Prioritize ERROR and CAUTION fixes.
3. ADVISORY fixes are optional and should be applied only if low-risk.
4. Keep changes minimal and consistent with the repository style.

Files in scope:
- {file1}
- {file2}

Review findings:
{review_text (最大 MaxReviewCharsPerChunk 文字)}

Response format:
- Fixed: bullet list
- Remaining: bullet list (if any)
- Verification: commands run (or "not run")
```

## 🔄 2段階レビュー戦略（自律開発モード）

自律開発時は **こまめなチェック**（Uncommitted）と **節目のチェック**（main...HEAD）を組み合わせて品質を維持する:

| レベル | モード | 対象 | 目的 | 頻度 |
|:-------|:-------|:-----|:-----|:-----|
| **L1: こまめなチェック** | `-Uncommitted` | 未コミットの変更 | 直近の変更が正しいか即時検証 | 毎回（コミット前） |
| **L2: 節目のチェック** | `-Base main`（デフォルト） | main分岐後の全変更 | 全体の整合性・見落とし検出 | N回に1回（ユーザー指定） |

### 判定基準

- **L1** はコミットごとの品質ゲート（高速・軽量）
- **L2** は累積変更の俯瞰レビュー（低速・網羅的）
- L2の頻度はユーザーが指示（例: `5回に1回main...HEADを入れて`）
- L2で新たな指摘が出た場合 → L1ループに戻って修正

### 推奨パラメータ

| パラメータ | L1（こまめ） | L2（節目） |
|:-----------|:------------|:-----------|
| モード | `-Uncommitted` | `-Base main` |
| ReviewOnly | 状況次第 | `-ReviewOnly` 推奨 |
| ChunkSize | デフォルト(8) | `4`（精度重視） |
| FixRounds | `1` | `1`（手動判断推奨） |

> [!TIP]
> 実行手順・コマンド例は WORKFLOW.md の「自律開発モード」セクションを参照。

## 📁 出力ファイル構造

```
_outputs/review/codex_cli/review_and_fix/{timestamp}/
├── run_manifest.json           ← 全体のメタデータ・結果
├── summary.md                  ← 人間向けサマリー
├── round_001/
│   ├── review/                 ← codex_cli_diff_review.ps1 の出力
│   │   ├── review_manifest.json
│   │   ├── chunk_001_files.txt
│   │   ├── chunk_001_prompt.md
│   │   ├── chunk_001_review.txt
│   │   ├── chunk_001_review.stderr.txt
│   │   ├── chunk_001_review.combined.txt
│   │   └── summary.md
│   └── fix/                    ← codex exec の出力
│       ├── chunk_001_fix_prompt.md
│       ├── chunk_001_fix_result.txt
│       ├── chunk_001_fix_result.stderr.txt
│       └── chunk_001_fix_result.combined.txt
└── final_verification/         ← 最終検証（修正後の再レビュー）
    └── (reviewと同構造)
```

## ⚙️ 前提条件

```
必須コマンド:
□ git — バージョン管理
□ powershell — スクリプト実行
□ codex — OpenAI Codex CLI（review, exec サブコマンド）

必須ファイル:
□ tools/review_and_fix.ps1
□ tools/codex_cli_diff_review.ps1
```

## ⚠️ 既知の制約

> [!CAUTION]
> - **Codex CLI が必須**: `codex` コマンドが PATH に無いとエラー
>   - ただし **`-DryRun` 実行時は `codex` 未導入でもプロンプト生成のみ実行可能**
> - ~~**`$args` 変数名の衝突**~~ → **修正済み**: `$cmdArgs`/`$execArgs`/`$GitArgs`/`$gitDiffArgs` にリネーム
> - **大量ファイル**: チャンク数が多いとCodex API呼び出し回数が増大（コスト注意）
> - **ExcludeGlobsの精度**: PowerShellの `-like` 演算子ベース。複雑なglob（`**`等）は非対応
> - ~~**スコープ逸脱検知なし**~~ → **修正済み**: 修正前スナップショット（変更集合 + ファイル指紋）と比較し、逸脱ファイルのみを復元（既存変更の一括巻き戻しを回避）
> - ~~**Findings判定がstdout のみ**~~ → **修正済み**: stdout/stderr/combined を統合して判定
> - ~~**reviewのみタイムアウト対応**~~ → **修正済み**: review/fix 両方でタイムアウト強制終了を実装
> - **API遅延時の総待機時間**: `FailFastOnCodexTimeout=true` で1回の重障害後に残りチャンクを短絡し、待機連鎖を防止
> - **致命的な実行失敗時の診断性**: 失敗時も `summary.md` / `review_manifest.json` を書き出し、原因追跡を容易化

## 💡 Rules

- **レビュー→修正→再検証のラウンドループ**
- **チャンク単位で並列化可能な設計**（ただし現在は逐次実行）
- **DryRun/ReviewOnlyで安全に試行可能**
- **ExcludeGlobsで不要ファイルを除外**
- **自律開発時はL1(Uncommitted)+L2(Base)の2段階戦略を適用**
- **毎回Codex送受信内容を表示**: レビュー完了後、送信プロンプトと返答を必ずユーザーに提示（**表示責務はエージェント＝呼び出し元**。スクリプトはファイル保存のみ）
- **Language**: 日本語で報告
