# 画像生成エージェント

GCP Vertex AI **Imagen 3** を使用して画像を自動生成するエージェント。
動画制作パイプライン (`/video_orchestrator`) との連携を前提に設計。

## セットアップ

### 1. GCP 準備
1. [Google Cloud Console](https://console.cloud.google.com/) → Vertex AI API 有効化
2. サービスアカウント作成 → 「Vertex AI ユーザー」ロール付与
3. JSONキーをダウンロード

### 2. 設定
`config/imagen_config.json` を編集:
```json
{
    "project_id": "あなたのGCPプロジェクトID",
    "credentials_path": "C:/path/to/your-key.json"
}
```

### 3. 依存パッケージ
```powershell
pip install google-cloud-aiplatform Pillow
```

## 使い方

```powershell
# GCP認証確認
python scripts/imagen_generator.py verify-auth

# 単発プロンプトで画像生成
python scripts/imagen_generator.py prompt "cyberpunk city at night" --count 4

# shot_list からバッチ生成
python scripts/imagen_generator.py generate --project demo --style cinematic

# 特定ショットだけ生成
python scripts/imagen_generator.py generate --project demo --shot s01
```

## スタイル一覧

| スタイル | 説明 |
|:---------|:-----|
| `cinematic` | シネマティック（デフォルト） |
| `anime` | アニメ調 |
| `photorealistic` | 写実的 |
| `concept_art` | コンセプトアート |
| `watercolor` | 水彩画風 |

## ディレクトリ構成

```
画像生成エージェント/
├── config/
│   └── imagen_config.json    # GCP設定
├── scripts/
│   ├── imagen_generator.py   # メインCLI
│   └── prompt_builder.py     # プロンプト構築
├── tests/
│   └── test_imagen_generator.py
└── README.md
```

## ワークフロー定義

- `.agent/workflows/imagen/SKILL.md` — 技術仕様
- `.agent/workflows/imagen/WORKFLOW.md` — 実行手順
