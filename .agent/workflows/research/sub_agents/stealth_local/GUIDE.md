# Stealth Local Research Agent v1.0.0 

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SPEC.md` を読むこと。

## 概要

既存 `/research` と独立した新規リサーチパイプライン。
Stealth収集 + ローカルLLM推論 + 監査成果物生成を一体化する。

## 実行手順

### Step 1: QUERY_PLAN
- goal/focus から検索クエリを生成（LLM優先、失敗時テンプレート）

### Step 2: STEALTH_COLLECT
- Stealth Research Tool v2.1 で検索・取得を実行
- trace/summaries を保持

### Step 3: EXTRACT_CLAIMS
- 取得本文から claim/evidence を抽出

### Step 4: NORMALIZE
- 重複統合・正規化（scope/relevance付与）

### Step 5: VERIFY
- claimごとに status 判定（VERIFIED/CONDITIONED/CONTESTED/UNSUPPORTED/REFUTED）

### Step 6: REPORT
- 最終レポート（Markdown）を生成

### Step 7: SAVE_ARTIFACTS
- `final_report.md` と同時に `search_log.md` を保存
- 監査用JSON/JSONL一式を保存

## CLI

```powershell
# 単発
python エージェント/ステルス自動リサーチエージェント/scripts/stealth_research_local.py --goal "調査テーマ" --focus "判断したい論点"

# バッチ
python エージェント/ステルス自動リサーチエージェント/scripts/stealth_research_local.py --jobs-file エージェント/ステルス自動リサーチエージェント/scripts/jobs.example.json
```

## 主要オプション

- `--fast-model`（既定: `qwen3:14b`）
- `--accurate-model`（既定: `gpt-oss:20b`）
- `--ollama-base-url`（既定: `http://localhost:11434`）
- `--max-urls` / `--max-fetches` / `--max-time`
- `--output-dir`
- `--json`

## ルール

- 既存 `/research` へ変更を加えない
- `search_log.md` を常に生成する
- 実行結果は `_outputs/research_stealth_local/` 配下へ保存する

