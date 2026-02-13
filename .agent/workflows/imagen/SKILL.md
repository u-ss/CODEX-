---
name: Imagen Agent Skill v1.0.0
description: GCP Vertex AI Imagen 3 画像生成エージェント技術仕様
capabilities: image-generation, vertex-ai, gcp, video-assets
---

# Imagen Agent SKILL v1.0.0

## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。

## 役割

GCP Vertex AI Imagen 3 API を使用して、テキストプロンプトまたは shot_list から
動画制作用の画像素材を自動生成する。

## 入力

- テキストプロンプト（`prompt` コマンド）
- `projects/<project_slug>/shot_list.json`（`generate` コマンド）
- `config/imagen_config.json`（GCP認証・デフォルトパラメータ）

## 出力

- 生成画像（PNG）: `_outputs/imagen/<project>/<shot_id>/imagen_YYYYMMDD_HHMMSS_NN.png`
- 生成結果サマリー（JSON）

## 実装要件

- **認証**: GCPサービスアカウントキー or gcloud ADC
- **モデル**: `imagen-3.0-generate-002`
- **リージョン**: `us-central1`（Imagen 3 が安定的に利用可能）
- **最大生成枚数**: 1回あたり4枚
- **アスペクト比**: `16:9`（動画用デフォルト）, `1:1`, `9:16`, `4:3`, `3:4`
- **安全フィルター**: `safety_filter_level` でコンフィグ制御
- **プロンプト構築**: shot_list の `text` + `video.storyboard` からスタイル付きプロンプトを自動生成

## スタイルプリセット

| スタイル | 説明 |
|:---------|:-----|
| `cinematic` | シネマティック照明・映画的構図 |
| `anime` | アニメスタイル・セルシェーディング |
| `photorealistic` | 写実的・DSLR品質 |
| `concept_art` | コンセプトアート・デジタルペインティング |
| `watercolor` | 水彩画風 |

## 料金目安（GCPクレジット消費）

- テキスト→画像生成: 約 ¥3〜5 / 枚
- 4万円クレジットで約8,000〜13,000枚生成可能

## Rules

- GCP認証が成功しない場合は即停止してユーザーに報告
- `project_id` がデフォルト値のまま実行しない
- 元の `shot_list.json` は変更しない
