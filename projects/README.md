# Projects

`projects/<project_slug>/shot_list.json` を入力契約として扱います。

最小実行例:

1. `projects/demo/shot_list.json` を作成
2. `python エージェント/動画制作エージェント/scripts/video_pipeline.py d1_direct --project demo`
3. `_outputs/video_pipeline/demo/<run_id>/sora_style_guide.md` を見てSoraブラウザで素材生成
4. `sora_inbox/` に `s001` などショットIDを含む動画ファイルを配置
5. `python エージェント/動画制作エージェント/scripts/video_pipeline.py run --project demo`
