# House Real Workflow v1.2.0 

> [!CAUTION]
> **必須**: このファイルと同フォルダの `SPEC.md` を読んでから実行

## 概要

自然文入力から実寸整合を重視した戸建てモデルを生成する。
CODEXAPPによる評価ループで95点以上を目指す。

// turbo-all

## Step 1: プレビュー生成（爆速モード）

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
  --background --python "C:\Users\dodos\Documents\TEST\エージェント\実在戸建てモデリングエージェント\scripts\pipeline.py" `
  -- --prompt "2F建ての3LDKを作って" --render-profile preview --render-cameras ext_only
```

出力: `_outputs/iter_XXX/house_ext_{south,north,east,west}.png`

### プロファイルバリエーション

```powershell
# 高品質最終版（Cycles, 全カメラ）
... -- --prompt "..." --render-profile final --render-cameras all

# 指定カメラ追加（外観4+int_ldk）
... -- --prompt "..." --render-cameras list --render-camera-names "int_ldk"

# 最速OpenGL（形状チェック）
... -- --prompt "..." --render-method opengl
```

## Step 2: CODEXAPPレビュー（画像パスを渡す）

```powershell
python "C:\Users\dodos\Documents\TEST\scripts\codex_bridge.py" `
  --new-thread --send "以下のパスにある戸建てレンダリング画像を確認してください。100点満点で採点し、改善点を教えてください。パス: C:\Users\dodos\Documents\TEST\エージェント\実在戸建てモデリングエージェント\_outputs\iter_XXX\" `
  --port 9223
```

## Step 3: 改善→再生成→再レビュー

CODEXAPPの改善提案をコードに反映 → Step 1-2を繰り返す。
95点以上で完了。

## 出力物

```
_outputs/iter_XXX/
├── house_ext_south.png      # 外観南面
├── house_ext_north.png      # 外観北面
├── house_ext_east.png       # 外観東面
├── house_ext_west.png       # 外観西面
├── house_int_ldk.png        # 内装LDK（all/list時）
├── house_int_bedroom.png    # 内装寝室（all/list時）
├── house.blend              # Blenderファイル
├── house_spec.json          # 入力仕様
├── house_spec_normalized.json  # 正規化仕様
├── qa_report.json           # QA検証結果
└── pipeline_report.json     # パイプライン実行レポート
```

## 失敗時

- QA 3回で `pass=true` にならない場合: `status=NEEDS_INPUT`
- 実行エラー時: `status=FAILED`, Blenderログパスを記録

## Rules

- 外部アセット導入は初期版では行わない（手続き生成のみ）
- 寸法許容: 主要構造 ±0.01m, 開口/外構 ±0.03m
- 各イテレーションは個別フォルダに出力（前回分と混ぜない）
- Language: 日本語で報告
