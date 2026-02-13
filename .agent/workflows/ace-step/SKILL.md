---
name: ACE-Step Agent v1.0.0
description: ACE-Step AI音楽生成エージェント - テキストから楽曲を生成・編集・延長
capabilities: music_generation, text2music, audio_editing, lyric_editing, voice_cloning
---

# ACE-Step Agent SKILL v1.0.0## コンセプト

## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。


`/ace-step` は **AI音楽生成エージェント**。

- テキスト（タグ + 歌詞）から楽曲を自動生成
- 生成済み楽曲のリテイク・リペイント・編集・延長
- Gradio GUI（ポート7865）またはAPI経由で操作
- 19言語対応（日本語含む）

## 環境

| 項目 | 値 |
|:-----|:---|
| プロジェクトパス | `c:\Users\dodos\Documents\google drive30TB\ACE-Step` |
| Python仮想環境 | `venv\` |
| モデル | `ACE-Step/ACE-Step-v1-3.5B`（~8GB、自動DL） |
| キャッシュ | `~/.cache/ace-step/checkpoints`（初回DL ~8GB） |
| 出力先 | `./outputs/`（自動保存） |
| PyTorch | nightly cu128（RTX 5080対応） |

## タグ（プロンプト）設計

タグはカンマ区切りで以下のカテゴリを指定する：

| カテゴリ | 例 |
|---------|-----|
| ジャンル/雰囲気 | `pop, rock, jazz, funk, dark, upbeat, chill` |
| テンポ | `120 bpm, up-tempo, slow, moderate` |
| 楽器 | `guitar, drums, piano, synth, bass, strings` |
| ボーカル | `female vocal, male vocal, clean, raspy, falsetto` |
| 時代/質感 | `80s style, lo-fi, punchy, warm, dreamy` |

> [!TIP]
> 詩的な文章よりも**具体的なキーワード**が効果的。タグと歌詞が矛盾しないよう注意。

## 歌詞の構造タグ

```
[intro]      — イントロ
[verse]      — Aメロ / Bメロ
[pre-chorus] — プレコーラス
[chorus]     — サビ
[bridge]     — ブリッジ
[outro]      — アウトロ
[instrumental] or [inst] — インストのみ（歌なし）
```

> [!IMPORTANT]
> 構造タグを使い分けることで、モデルが曲の展開を正しく理解して生成する。

## パラメータリファレンス

| パラメータ | デフォルト | 推奨範囲 | 説明 |
|-----------|-----------|---------|------|
| 音声の長さ | -1（ランダム30~240秒） | 30~120 | 秒単位で指定 |
| 推論ステップ | 60 | 30~100 | 高い=高品質だが遅い |
| ガイダンススケール | 15 | 5~25 | 高い=タグに忠実 |
| スケジューラー | euler | euler推奨 | heunは遅い、pingpongはSDE |
| CFGタイプ | apg | apg推奨 | cfg/cfg_starもほぼ同等 |
| 粒度スケール | 10 | 5~30 | 高い=アーティファクト低減 |
| ガイダンス区間 | 0.5 | 0.3~0.7 | 中間ステップにのみガイダンス適用 |
| タグERG | ON | — | 多様性向上 |
| 歌詞ERG | OFF | — | 必要に応じてON |
| 拡散ERG | ON | — | アーティファクト低減 |

## 各タブの機能

### テキスト→音楽（メイン）
タグ + 歌詞 → 楽曲生成。基本のワークフロー。

### リテイク
同じ設定でバリエーション違いを生成。バリアンス（0.0~1.0）で変化量を調整。

### リペイント
曲の一部区間のみ再生成。開始/終了時間を指定し、ソース音源を選択。

### 編集
- `only_lyrics`: 歌詞のみ変更（メロディ維持）。小さな変更推奨（1行程度）
- `remix`: メロディ + ジャンルも変更可能

### 延長
曲を左右に延長。延長長さ（秒）を左右それぞれ指定。

## 応用テクニック

- **Audio2Audio**: 参照音声を元に生成（ボイスクローン等）
- **LoRA**: スタイル特化モデル適用（例: `ACE-Step/ACE-Step-v1-chinese-rap-LoRA`）
- **シード固定**: 気に入った曲のシードを記録して再現
- **反復微調整**: タグ微調整 → 歌詞の1行変更、の順で反復

## API呼び出し

- エンドポイント: `/__call__`（text2music生成）
- 引数順序（24個）: format, duration, tags, lyrics, 推論ステップ, ガイダンス, スケジューラー, CFG, 粒度, シード, ガイダンス区間, 減衰, 最小ガイダンス, ERG×3, OSS, text/lyric guidance, Audio2Audio×3, LoRA×2
- 戻り値: `(audio_path, params_json)`

> 完全なコードサンプルは **WORKFLOW.md Step 2** を参照。

## 既知の修正点（Windows + nightly cu128）

1. **Gradio 6.x互換**: `gr.Audio`の`show_download_button`パラメータを除去
2. **torchaudio保存エラー**: `torchcodec` DLLロード失敗 → `soundfile`で直接保存に変更
3. **triton**: Windowsでは`pip install triton-windows`が必要

## Rules

- タグと歌詞は矛盾させない（タグ=グローバル制御、歌詞=テキスト内容）
- 歌詞編集（only_lyrics）は1行程度の小変更に留める
- 対応言語19言語だが、データ偏りにより英語・中国語以外は品質低下の可能性あり
- Language: 日本語
