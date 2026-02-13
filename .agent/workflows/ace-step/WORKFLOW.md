---
name: ACE-Step Agent v1.0.0
description: ACE-Step AI音楽生成ワークフロー - サーバー起動→楽曲生成→編集→出力
---

# ACE-Step Agent Workflow v1.0.0 (`/ace-step`)

> [!CAUTION]
> **必須**: このファイルと同フォルダの `SKILL.md` を読んでから実行

## 概要

テキスト（タグ + 歌詞）から楽曲を AI 生成するワークフロー。
GUI（Gradio）またはAPI経由でACE-Stepを操作する。

## 前提条件

- ACE-Step がクローン済み: `c:\Users\dodos\Documents\google drive30TB\ACE-Step`
- Python仮想環境 `venv` がセットアップ済み
- PyTorch nightly cu128 インストール済み

## ワークフロー

// turbo-all

### Step 1: サーバー起動確認

既にサーバーが起動しているか確認する。起動していなければ起動する。

```powershell
# Cwd: c:\Users\dodos\Documents\google drive30TB\ACE-Step
& "venv\Scripts\acestep.exe" --port 7865
```

起動確認: `http://127.0.0.1:7865` にアクセスできればOK。初回はモデルDL（~8GB）のため時間がかかる。

### Step 2: 楽曲生成（API経由）

Gradio Client APIで生成する。ブラウザ操作は不要。

```python
from gradio_client import Client
import time

client = Client('http://127.0.0.1:7865')

# --- ここをカスタマイズ ---
FORMAT = 'wav'            # wav, mp3, flac, ogg
DURATION = 60             # 秒数（-1でランダム30~240秒）
TAGS = 'pop, female vocal, catchy, upbeat, 120 bpm'
LYRICS = """[verse]
Hello world, this is a test
Singing in the morning light

[chorus]
La la la, let's go
Feel the rhythm flow
"""
# --- カスタマイズここまで ---

print(f'生成開始: {DURATION}秒, タグ: {TAGS[:50]}...')
start = time.time()

result = client.predict(
    FORMAT,          # フォーマット
    DURATION,        # 音声の長さ（秒）
    TAGS,            # タグ（プロンプト）
    LYRICS,          # 歌詞
    60,              # 推論ステップ
    15.0,            # ガイダンススケール
    'euler',         # スケジューラー
    'apg',           # CFGタイプ
    10.0,            # 粒度スケール
    '',              # シード値（空=ランダム）
    0.5,             # ガイダンス区間
    0.0,             # ガイダンス区間減衰
    3.0,             # 最小ガイダンススケール
    True,            # タグERG
    False,           # 歌詞ERG
    True,            # 拡散ERG
    '',              # OSSステップ
    0.0,             # テキスト用ガイダンス
    0.0,             # 歌詞用ガイダンス
    False,           # Audio2Audio
    0.5,             # 参照音声強度
    None,            # 参照音声パス
    'none',          # LoRA
    1.0,             # LoRA重み
    api_name='/__call__'
)

elapsed = time.time() - start
audio_path = result[0]
print(f'生成完了！ ({elapsed:.1f}秒)')
print(f'出力: {audio_path}')
```

### Step 3: リテイク（バリエーション生成）

生成結果のバリエーションを作成する。

```python
# Step 2の後に実行
retake_result = client.predict(
    '{"actual_seeds": [12345]}',  # 元のパラメータJSON
    0.2,                           # バリアンス（0.0~1.0）
    '',                            # リテイクシード
    api_name='/retake_process_func'
)
print(f'リテイク出力: {retake_result[0]}')
```

### Step 4: 生成結果の確認

出力ディレクトリの内容を確認する。

```powershell
Get-ChildItem "c:\Users\dodos\Documents\google drive30TB\ACE-Step\outputs" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 10 Name, @{N='Size(MB)';E={[math]::Round($_.Length/1MB,1)}}, LastWriteTime
```

### Step 5: 楽曲の再生（任意）

生成された楽曲をデフォルトプレーヤーで再生する。

```powershell
# 最新の出力ファイルを再生
$latest = Get-ChildItem "c:\Users\dodos\Documents\google drive30TB\ACE-Step\outputs\*.wav" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
Start-Process $latest.FullName
```

## 出力

- `outputs/output_YYYYMMDDHHMMSS_0.wav` — 生成された楽曲
- `outputs/output_YYYYMMDDHHMMSS_0_input_params.json` — 生成パラメータ

## ジャンルプリセット例

| プリセット | タグ |
|-----------|------|
| Pop | `catchy, pop, synth, drums, female vocal, upbeat, 120 bpm` |
| Rock | `rock, electric guitar, drums, bass, male vocal, powerful, 140 bpm` |
| Jazz | `jazz, piano, double bass, drums, saxophone, smooth, 90 bpm` |
| EDM | `edm, electronic, synth, heavy bass, drop, energetic, 128 bpm` |
| Classical | `classical, orchestra, strings, piano, violin, elegant` |
| Lo-Fi | `lo-fi, chill, mellow, warm, vinyl, 80 bpm` |
| Hip Hop | `hip hop, trap, 808, hi-hats, male vocal, rhythmic, 90 bpm` |
| J-Pop | `j-pop, japanese, female vocal, catchy, synth, upbeat, 130 bpm` |

## 日本語楽曲の例

```python
TAGS = 'j-pop, japanese, female vocal, catchy, synth, upbeat, 130 bpm'
LYRICS = """[verse]
朝の光が差し込んで
新しい一日が始まる
窓を開けて深呼吸
今日も頑張ろうって思うの

[chorus]
走り出せ 夢を追いかけて
止まらないで このまま
きっと届く 空の向こうへ
信じてる この道を
"""
```

## トラブルシューティング

| 症状 | 原因 | 対処 |
|:-----|:-----|:-----|
| `torchcodec` DLLエラー | nightly torchaudioの互換性 | `pipeline_ace_step.py`のsave_wav_fileが修正済み |
| `show_download_button` TypeError | Gradio 6.x非互換 | `components.py`から該当パラメータ除去済み |
| CUDA out of memory | VRAM不足 | `--cpu_offload` オプション使用 |
| 初回起動が遅い | モデルDL中（~8GB） | 待機。次回以降は即起動 |

## Rules

- サーバー起動確認を必ず最初に行う
- API呼び出しは `/__call__` エンドポイントを使用
- ブラウザ操作よりAPI経由を優先（安定性が高い）
- タグ・パラメータの詳細は SKILL.md を参照
- Language: 日本語で報告
