# 動画制作エージェント

`projects/<project_slug>/shot_list.json` を入力として、`_outputs/video_pipeline/<project_slug>/<run_id>/exports/final.mp4` を生成するパイプラインです。

このエージェントは「有料APIを使わない」前提です。Sora素材はブラウザで手動生成し、`sora_inbox/` に投入します。

## どの順で動くか

1. `D1 VideoDirector`  
   `shot_list` を安全補強し、Sora Storyboard用のプロンプト集を生成
2. `A1 ShotListValidator`  
   `shot_list.directed.json`（存在すれば）を検証して正規化
3. `A2 AssetCollector` と `A4 VoiceVoxTTS`（並列）  
   素材回収と音声生成を同時実行
4. `A3 -> A5 -> A6 -> A7 -> A8 -> A9`  
   測定/Conform、タイミング、Remotionレンダリング、音声ミックス、最終mux

## 主要成果物

- `shot_list.directed.json`: Director補強済みのshot_list
- `sora_prompt_pack.json`: Sora Storyboardに貼るシーン別プロンプト
- `sora_style_guide.md`: 人間向け運用ガイド（どの順で生成するか）
- `sora_quality_report.json`: 構成上の品質指摘
- `shot_list.normalized.json` ～ `exports/final.mp4`: 本編生成成果物

## 使い方（最短）

1. `projects/demo/shot_list.json` を用意
2. `python エージェント/動画制作エージェント/scripts/video_pipeline.py d1_direct --project demo`
3. 生成された `sora_style_guide.md` と `sora_prompt_pack.json` を見てSoraブラウザで素材生成
4. 生成動画を `sora_inbox/` に置く
5. `python エージェント/動画制作エージェント/scripts/video_pipeline.py run --project demo`

## ワークフロー定義（正）

- `.agent/workflows/video/` — メインオーケストレーター
  - `sub_agents/director/` — D1 VideoDirector
  - `sub_agents/shotlist/` — A1 ShotListValidator
  - `sub_agents/asset/` — A2 AssetCollector
  - `sub_agents/probe/` — A3 MediaProbe
  - `sub_agents/voicevox/` — A4 VoiceVoxTTS
  - `sub_agents/timing/` — A5 TimingBuilder
  - `sub_agents/remotion_props/` — A6 RemotionPropsBuilder
  - `sub_agents/renderer/` — A7 RemotionRenderer
  - `sub_agents/audio/` — A8 AudioMixer
  - `sub_agents/finalize/` — A9 Finalize
