# Video Director SKILL v1.0.0## 役割

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


`D1` として `shot_list.json` を安全に補強し、Soraブラウザ生成用の成果物を作る。

## 入力

- `projects/<project_slug>/shot_list.json`
- `settings.director`（未指定時はデフォルト）

## 出力

- `shot_list.directed.json`
- `sora_prompt_pack.json`
- `sora_style_guide.md`
- `sora_quality_report.json`

## 実装要件

- 有料APIは使わない（ローカルのテンプレート生成のみ）
- Storyboard前提で `shot` 単位のシーン指示を生成
- 既存の `video.storyboard` がある場合は `--force` なしで上書きしない
- 品質指摘（timing幅・文脈重複・テキスト不足）をJSONで出す
- `storyboard_aspect_ratio` と `settings.resolution` の整合を品質指摘に含める
- `sora_style_guide.md` に素材命名（`shot_id`/`inbox_match`）の指針を含める

## Rules

- 元の `projects/<slug>/shot_list.json` は直接書き換えない
- 生成結果は `run_id` ごとに `_outputs` 配下へ出力する

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
