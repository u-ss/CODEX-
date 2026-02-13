---
name: Imagen Agent Workflow v1.0.0
description: GCP Vertex AI Imagen 3 画像生成ワークフロー
---

# Imagen Agent Workflow v1.0.0 (`/imagen`)

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと

## 概要

GCPクレジットを使用し、Vertex AI Imagen 3 で画像素材を生成する。
単発プロンプト生成と shot_list バッチ生成の2モードに対応。

## 事前準備

1. GCP Console でプロジェクト作成 → Vertex AI API 有効化
2. サービスアカウントキー（JSON）をダウンロード
3. `config/imagen_config.json` に `project_id` と `credentials_path` を設定
4. `pip install google-cloud-aiplatform Pillow`

## 実行手順

### モード1: 単発プロンプト生成

// turbo
```powershell
python エージェント/画像生成エージェント/scripts/imagen_generator.py verify-auth
```

```powershell
python エージェント/画像生成エージェント/scripts/imagen_generator.py prompt "your prompt here" --count 4 --style cinematic
```

### モード2: shot_list バッチ生成

1. `projects/<project>/shot_list.json` を用意
2. 認証確認:
// turbo
```powershell
python エージェント/画像生成エージェント/scripts/imagen_generator.py verify-auth
```
3. バッチ生成:
```powershell
python エージェント/画像生成エージェント/scripts/imagen_generator.py generate --project <slug> --style cinematic
```
4. 特定ショットのみ:
```powershell
python エージェント/画像生成エージェント/scripts/imagen_generator.py generate --project <slug> --shot s01
```

## 出力

- `_outputs/imagen/<project>/<shot_id>/imagen_*.png`

## Videoパイプラインとの連携

生成した画像を動画素材として使う場合:
1. `/imagen` で画像生成
2. 生成画像を `sora_inbox/` にコピー or 参照設定
3. `/video_orchestrator` で動画制作パイプラインを実行

## Rules

- GCP認証が通らない場合は停止
- `project_id` がデフォルト値なら実行しない
- 報告言語は日本語
