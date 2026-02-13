# antigravity

このリポジトリは「エージェント（部署）」を **論理層**（`.agent/workflows/`）と **物理層**（`エージェント/`）で運用するためのワークスペースです。

## Quickstart

### 1) まず健康診断

```powershell
python tools/workflow_lint.py
python tools/repo_hygiene_check.py
```

### 2) 運用の入口（推奨: `/ops`）

```powershell
python .agent/workflows/ops/scripts/ops.py --help
python .agent/workflows/ops/scripts/ops.py health
python .agent/workflows/ops/scripts/ops.py deploy --dry-run
python .agent/workflows/ops/scripts/ops.py clean --dry-run
python .agent/workflows/ops/scripts/ops.py doc-sync --check
```

> `deploy --dry-run` は **作業ツリーがクリーンでないと失敗**します（意図したゲート）。

### 3) Check Agent

```powershell
python .agent/workflows/check/scripts/check.py --help
```

### 4) Video Pipeline（A0〜A9）

```powershell
python エージェント/動画制作エージェント/scripts/video_pipeline.py run --project demo
python エージェント/動画制作エージェント/scripts/video_pipeline.py d1_direct --project demo
python エージェント/動画制作エージェント/scripts/video_pipeline.py a1_validate --project demo
```

入力契約は `projects/<project_slug>/shot_list.json`。
`d1_direct` は `shot_list` を補強し、Sora Storyboard向けの `sora_prompt_pack.json` と `sora_style_guide.md` を出力します。
有料APIは使わず、Soraはブラウザ手動運用を前提とします。

### 5) Codex CLI 差分レビュー自動化

```powershell
# dry-run（差分取得と分割のみ）
powershell -ExecutionPolicy Bypass -File tools/codex_cli_diff_review.ps1 -Base main -DryRun

# 実行（必要時のみ分割。既定: 8ファイル単位）
powershell -ExecutionPolicy Bypass -File tools/codex_cli_diff_review.ps1 -Base main
```

出力先: `_outputs/review/codex_cli/<timestamp>/`

### 6) Codex CLI レビュー + 修正ループ自動化

```powershell
# dry-run（レビュー分割のみ確認。修正はしない）
powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1 -Base main -DryRun

# レビューだけ実行（修正はしない）
powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1 -Base main -ReviewOnly

# 1回の修正ラウンド + 最終再レビュー
powershell -ExecutionPolicy Bypass -File tools/review_and_fix.ps1 -Base main -FixRounds 1
```

主な出力先: `_outputs/review/codex_cli/review_and_fix/<timestamp>/`

## ポリシー

- **正は `.agent/workflows/`**（手順と技術詳細はここに集約）
- `エージェント/*/README.md` は導線のみ（重い仕様は置かない）
- ランタイム生成物はコミットしない（`_outputs/`, `_logs/`, `_temp/`, `_screenshots/`, `__pycache__/` など）

詳細: `docs/architecture.md`
