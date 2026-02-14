# Codex CLI Review GUIDE v1.1.0

**Codex CLI ベースのコードレビュー＆自動修正**: git差分をチャンク分割し、`codex review` でレビュー → `codex exec` で自動修正 → 最終検証のループを回す。

> [!CAUTION]
> **必須**: このファイルと同フォルダの`SPEC.md`を読んでから実行

## 📋 Protocol: 4-Phase Review & Fix Flow

```
┌───────────────────────────────────────────────────────────────┐
│  Phase 1: CONFIGURE ⚙️                                       │
│     → 対象範囲決定（Base/Uncommitted）                        │
│     → パラメータ確認（ChunkSize, FixRounds等）                │
│     → 前提条件チェック（git, codex, スクリプト存在）          │
│     ↓                                                         │
│  Phase 2: REVIEW 🔍                                           │
│     → review_and_fix.ps1 を実行                               │
│     → チャンク単位でCodex CLIレビュー                         │
│     → Findings判定（発見あり/なし/不明）                      │
│     ↓                                                         │
│  Phase 3: FIX ✏️ (ReviewOnly時はスキップ)                     │
│     → Findingsありチャンクを codex exec で自動修正            │
│     → FixRoundsに応じてラウンドループ                         │
│     ↓                                                         │
│  Phase 4: REPORT 📊                                           │
│     → 最終検証レビュー結果確認                                │
│     → summary.md + run_manifest.json を報告                   │
│     → ユーザーに結果サマリー提示                              │
└───────────────────────────────────────────────────────────────┘
```

## 🔧 使用ツール

| Phase | ツール |
|:------|:-------|
| CONFIGURE | `run_command`（git, codex バージョン確認） |
| REVIEW | `run_command`（review_and_fix.ps1 実行） |
| FIX | （review_and_fix.ps1 内で自動実行） |
| REPORT | `view_file`（summary.md, run_manifest.json 確認） |

## 📝 Phase詳細

### Phase 1: CONFIGURE ⚙️

```
Step 1-1: 対象範囲の決定
  ユーザーの指示に応じて以下を決定:
  - Uncommitted（コミット前の変更）or Base比較（デフォルト: main）
  - ReviewOnly（レビューのみ）or Full（レビュー＋修正）
  - DryRun（プロンプト生成のみ）

Step 1-2: 前提条件チェック
  // turbo
  powershell:
    # git, codex の存在確認
    Get-Command git -ErrorAction SilentlyContinue | Select-Object Name, Source
    Get-Command codex -ErrorAction SilentlyContinue | Select-Object Name, Source

  補足:
  - `-DryRun` の場合は `codex` が無くても実行可能（プロンプト生成のみ）

  // turbo
  powershell:
    # スクリプトの存在確認
    Test-Path "tools/review_and_fix.ps1"
    Test-Path "tools/codex_cli_diff_review.ps1"

Step 1-3: 変更ファイルの事前確認
  // turbo
  powershell:
    # Uncommittedモードの場合
    git diff --name-only
    git diff --name-only --cached
    git ls-files --others --exclude-standard

    # Baseモードの場合
    git diff --name-only main...HEAD

  → ファイル数が0なら「レビュー対象なし」で終了
  → ファイル一覧をユーザーに提示して確認

Step 1-4: パラメータ確定
  デフォルト値で問題ないか確認。カスタマイズが必要な場合:

  | パラメータ | 用途 | 変更タイミング |
  |:-----------|:-----|:---------------|
  | ChunkSize | 大量ファイル時にチャンクサイズ調整 | ファイル50+件 |
  | FixRounds | 修正→再レビューの繰り返し回数 | 品質重視時に増加 |
  | CodexTimeoutSeconds | Codex応答待ちの上限秒数 | タイムアウトが発生する時 |
  | HealthCheckTimeoutSeconds | 実レビュー前の疎通チェック待機秒数 | API不安定時 |
  | FailFastOnCodexTimeout | タイムアウト時に残チャンクを短絡 | 待ちぼうけ回避時 |
  | SkipCodexHealthCheck | 疎通チェックをスキップ | 既知安定環境で高速化したい時 |
  | ExtraReviewInstructions | 特定の観点でレビュー | セキュリティ/パフォーマンス等 |
  | UseFullAuto | codex exec の承認をスキップ | 自動化時 |
  | ExcludeGlobs | 除外パターン追加 | 特定ディレクトリ除外時 |
```

### Phase 2: REVIEW 🔍

```
Step 2-1: レビュー実行
  パラメータに応じたコマンドを構築・実行:

  ■ 基本（mainとの差分をレビュー＆修正）:
    powershell:
      powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1

  ■ Uncommitted（コミット前の変更）:
    powershell:
      powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1 -Uncommitted

  ■ ReviewOnly（レビューのみ、修正しない）:
    powershell:
      powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1 -ReviewOnly

  ■ DryRun（プロンプト確認のみ）:
    powershell:
      powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1 -DryRun

  ■ カスタマイズ例:
    powershell:
      powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1 `
        -Base develop `
        -ChunkSize 4 `
        -FixRounds 3 `
        -UseFullAuto $true `
        -ExtraReviewInstructions "セキュリティ観点を重視してレビューしてください"

Step 2-2: 実行中の監視
  - 各チャンクの処理状況をターミナル出力で確認
  - [ok] / [warn] プレフィックスでステータスを把握
  - `chunk_XXX_review.combined.txt` で実際の返答取得状況を確認
  - タイムアウト時は `CodexTimeoutSeconds` を引き上げて再実行
  - エラー発生時はログを確認して原因特定
```

### Phase 3: FIX ✏️

```
  ★ review_and_fix.ps1 が自動的にPhase 2の結果を受けて修正を実行
  ★ ReviewOnlyモードではこのフェーズは自動スキップ

  内部処理:
  1. Findingsありチャンクを特定
  2. 各チャンクの修正プロンプトを生成
  3. codex exec で修正実行（リトライ付き）
  4. スコープガードで逸脱検知（修正前スナップショット比較）
  5. 逸脱時は逸脱ファイルのみ復元して当該チャンクを失敗扱い
  6. FixRoundsに応じて再レビュー→再修正ループ
  7. 最終検証レビュー実行（SkipFinalVerification時はスキップ）

  介入が必要な場合:
  - codex exec の承認ダイアログ（UseFullAuto=false時）
  - 修正失敗が続く場合はユーザーに報告
```

### Phase 4: REPORT 📊

```
Step 4-1: 結果確認
  出力ディレクトリの構造を確認:

  // turbo
  powershell:
    # 最新の出力ディレクトリを特定
    $latest = Get-ChildItem "_outputs/review/codex_cli/review_and_fix" -Directory |
      Sort-Object Name -Descending | Select-Object -First 1
    Write-Host "出力先: $($latest.FullName)"

  以下のファイルを確認:
  - summary.md — 結果サマリー
  - run_manifest.json — 詳細メタデータ

Step 4-2: サマリー解析
  run_manifest.json から以下を抽出して報告:

  | 項目 | 確認内容 |
  |:-----|:---------|
  | no_findings_reached | 全チャンクの指摘が解消されたか |
  | unknown_results | 判定不能なチャンクがあるか |
  | rounds | 各ラウンドの fix_succeeded / fix_failed |
  | final_verification | 最終検証の結果 |

Step 4-2.5: ★ Codex送受信内容の表示（必須）
  毎ターンのレビュー完了後、以下を必ずユーザーに提示する:

  ■ 送信内容（プロンプト）:
    → chunk_XXX_prompt.md の内容を表示
    → 「何をCodexに聞いたか」がわかるように

  ■ 受信内容（レビュー結果）:
    → chunk_XXX_review.combined.txt を優先表示（stdout/stderr統合）
    → chunk_XXX_review.stderr.txt / chunk_XXX_review.txt も必要に応じて確認
    → 「Codexが何を返したか」がわかるように

  ■ 表示フォーマット:
    各チャンクにつき以下を提示:
    1. 📤 送信プロンプト（要約 or 全文）
    2. 📥 Codex返答（指摘事項の一覧）
    3. 判定結果（findings有無）

Step 4-3: ユーザーへの報告
  結果をユーザーに提示:
  - レビュー対象ファイル数
  - チャンク数
  - 発見された指摘事項数
  - 修正成功/失敗数
  - 最終検証結果
  - 残Issues（あれば）

  ★ 指摘が残っている場合:
  - 該当チャンクのレビュー結果ファイルを提示
  - 手動修正するか再ラウンドするかユーザーに確認
```

## 🔄 自律開発モード

自律的に開発を進める場合、以下のフローで **L1（こまめ）** と **L2（節目）** のレビューを組み合わせる:

```
┌────────────────────────────────────────────────────────────────┐
│  自律開発ループ                                                  │
│                                                                  │
│  ┌──────────────────────────────────────────┐  ← L1ループ       │
│  │  1. コード変更                             │  （毎回）         │
│  │  2. L1レビュー（-Uncommitted）              │                  │
│  │  3. 指摘あり → 修正 → 2へ戻る              │                  │
│  │  4. 指摘なし → コミット                     │                  │
│  └──────────────────────────────────────────┘                    │
│    ↓  N回に1回（ユーザー指定）                                    │
│  ┌──────────────────────────────────────────┐  ← L2チェック     │
│  │  5. L2レビュー（-Base main -ReviewOnly）    │  （定期）         │
│  │  6. 指摘あり → L1ループに戻って修正         │                  │
│  │  7. 指摘なし → 開発続行                     │                  │
│  └──────────────────────────────────────────┘                    │
└────────────────────────────────────────────────────────────────┘
```

### L1: こまめなチェック（毎コミット前）

```
powershell:
  powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1 -Uncommitted
```

- 対象: 未コミットの変更のみ（軽量・高速）
- 指摘あり → 修正してから再レビュー or コミット
- 指摘なし → コミットして次の開発へ

### L2: 節目のチェック（N回に1回）

```
powershell:
  powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1 -ReviewOnly -ChunkSize 4
```

- 対象: main分岐後の全変更（網羅的・低速）
- `-ReviewOnly` 推奨（全体レビューの修正は手動判断が安全）
- `-ChunkSize 4` 推奨（精度を上げるため小さめのチャンク）
- L2の頻度はユーザーが指定（例: `5回に1回main...HEADを入れて`）

### 使用例

```
ユーザー指示例:
  「5回に1回main...HEADを入れて /codex-cli-review で自律開発をしてきて」

エージェント動作:
  コミット1 → L1レビュー → コミット
  コミット2 → L1レビュー → コミット
  コミット3 → L1レビュー → コミット
  コミット4 → L1レビュー → コミット
  コミット5 → L1レビュー → コミット → ★ L2レビュー（全体チェック）
  コミット6 → L1レビュー → コミット
  ...
```

## ⚡ Quick Start

```
/codex-cli-review

典型的な使い方:

  # mainとの差分をレビュー＆修正
  /codex-cli-review

  # コミット前の変更をレビューのみ
  /codex-cli-review -ReviewOnly -Uncommitted

  # DryRunで対象ファイルとプロンプトだけ確認
  /codex-cli-review -DryRun

  # セキュリティ観点で重点レビュー
  /codex-cli-review -ExtraReviewInstructions "セキュリティ脆弱性を重視"

  # 修正を3ラウンド繰り返す
  /codex-cli-review -FixRounds 3 -UseFullAuto $true

  # Codex待機タイムアウトを10分に延長
  /codex-cli-review -CodexTimeoutSeconds 600

  # API疎通チェックを60秒にし、タイムアウト時は残チャンクを即停止
  /codex-cli-review -HealthCheckTimeoutSeconds 60 -FailFastOnCodexTimeout $true

  # 自律開発モード（5回に1回L2チェック）
  /codex-cli-review -Uncommitted  ← L1（毎回）
  /codex-cli-review -ReviewOnly -ChunkSize 4  ← L2（5回に1回）
```

## 💡 Rules

- **Phase順次実行**: スキップ禁止
- **前提条件チェック必須**: git, codex, スクリプトの存在を確認
- **変更ファイル0件は即終了**: 無駄なCodex API呼び出しを防ぐ
- **DryRunで安全に試行**: 初回実行時はDryRunを推奨
- **結果はrun_manifest.jsonで追跡**: 機械可読な結果を保持
- **レビュー結果はcombined優先**: `chunk_XXX_review.combined.txt` を一次情報として扱う
- **無期限待機を避ける**: 必要に応じて `-CodexTimeoutSeconds` を調整
- **自律開発時はL1(Uncommitted)+L2(Base)の2段階戦略を適用**
- **Language**: 日本語で報告
