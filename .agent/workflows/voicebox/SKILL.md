---
name: VOICEBOX Agent v4.0.0
description: gpt-oss:20bモーラ演出ディレクター統合・高品質TTS
capabilities: tts, voicevox, intonation, emotion, accent_verification, mora_direction, llm_pacing
---

# VOICEBOX Agent SKILL v4.0.0**状況分析 → gpt-oss:20bモーラ演出 → VOICEVOX音声生成**

## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。

テキストの感情・場面をLLMが分析し、モーラ単位の演出＋セグメントごとのテンポ・ポーズを自動決定するエージェント。

> [!NOTE]
> v4.0.0: gpt-oss:20bモーラ演出ディレクターを統合。LLMがワード強調・句読点ポーズ・セグメントテンポを文脈ベースで決定。
> Layer 0のpitch再計算を無効化（VOICEVOXのpitchカーブを最大限活用）。
> Layer 1（base_tuner）はデフォルト無効化。

## API仕様

### VOICEVOX エンドポイント（`localhost:50021`）

| エンドポイント | メソッド | 用途 |
|:---------------|:---------|:-----|
| `/audio_query` | POST | テキスト→音声クエリJSON生成 |
| `/accent_phrases` | POST | テキスト→アクセント句のみ取得 |
| `/mora_data` | POST | accent修正後のmora pitch再計算 |
| `/synthesis` | POST | クエリJSON→WAV生成 |
| `/speakers` | GET | キャラクター＆スタイル一覧 |
| `/user_dict_word` | POST | ユーザー辞書に単語登録 |
| `/version` | GET | 接続確認 |

### audio_query パラメータ

| パラメータ | 型 | 説明 | 推奨範囲 |
|:-----------|:---|:-----|:---------|
| `speedScale` | float | 話速 | 0.8〜1.3 |
| `pitchScale` | float | 音高 | -0.1〜+0.1 |
| `intonationScale` | float | 抑揚（★最重要） | 0.8〜1.5 |
| `volumeScale` | float | 音量 | 0.8〜1.2 |
| `prePhonemeLength` | float | 先頭無音 | 0.0〜0.5 |
| `postPhonemeLength` | float | 末尾無音 | 0.0〜0.5 |

### モーラ単位制御

```json
{
  "accent_phrases": [
    {
      "moras": [
        {"text": "コ", "pitch": 5.5, "vowel_length": 0.12},
        {"text": "ン", "pitch": 5.3, "vowel_length": 0.10}
      ],
      "accent": 1,
      "pause_mora": null
    }
  ]
}
```

- `moras[].pitch`: 各モーラの音高（上下でイントネーション制御）
- `moras[].vowel_length`: 母音の長さ
- `accent`: アクセント核の位置（1-indexed）
- `pause_mora`: ポーズ挿入（`vowel_length` で長さ制御）

---

## 4層パイプライン

### パイプライン実行順

```
Layer 0: アクセント辞書照合（土台）
  → MeCab+UniDicで正確なaType取得
  → VOICEVOXのaccent値と照合・修正
  → ⚠️ pitch再計算は無効（recalculate_pitch=False）
  → VOICEVOXのpitchカーブは変更しない＝イントネーション自然
     ↓
Layer M: モーラ演出ディレクター（★v4.0.0追加）
  → gpt-oss:20b（ローカルLLM）にコンテキスト+テキストを送信
  → LLMが以下を決定:
    • ワード演出: stretch（母音伸縮）/ pitch_shift / pause_before / pause_after
    • 読点（、）ポーズ: セグメントごとの読点ポーズ秒数
    • 句点（。）ポーズ: セグメント間の無音秒数
    • テンポ: セグメントごとのspeedScale
  → audio_queryのmora.vowel_length / mora.pitch に適用
     ↓
Layer 1: ベースチューニング（デフォルトOFF）
  → ダウンステップ・デクリネーション・自然ピッチ揺れ
  → ポーズ最適化・文末イントネーション補正
  → ⚠️ Layer M使用時は不要（LLMが同等の制御を実施）
     ↓
Layer 2: コンテキスト調整（場面・感情適応）
  → グローバルパラメータ（speed/pitch/intonation/volume）
  → ⚠️ speedはLayer Mが決定するため、Mが有効時はスキップ
```

> [!IMPORTANT]
> **v4.0.0推奨フロー**: Layer 0 → Layer M のみ。
> Layer 1/2はフォールバック用（LLM使用不可時に使用）。

### 句読点ポーズの正しい分離

```
「、」（読点）→ audio_query内のpause_moraで制御
  → セグメント内の全pause_moraはcomma_pause秒に設定

「。」（句点）→ セグメント間の無音WAVで制御
  → ffmpegでsilenceを生成しconcatで挿入
  → audio_query内にはperiod対応のpause_moraは存在しない
```

---

## ルールベース状況分析

### 感情判定ルール

| シグナル | 判定 | 例 |
|:---------|:-----|:---|
| `！` が2個以上 | 高テンション | 「すごい！！」 |
| `？` で終了 | 疑問 | 「本当ですか？」 |
| `…` / `。。。` | ためらい/悲しみ | 「そうか…」 |
| 怒りキーワード | 怒り | 「ふざけるな」「何だと」 |
| 喜びキーワード | 喜び | 「やった」「嬉しい」「最高」 |
| 悲しみキーワード | 悲しみ | 「残念」「辛い」「寂しい」 |
| 短文＋感嘆符 | 叫び | 「逃げろ！」 |
| 長文＋句読点多 | 落ち着き | 説明文 |

### テンション判定

| 特徴 | テンション |
|:-----|:-----------|
| 感嘆符密度 高 | HIGH |
| 文が短い（10文字以下） | HIGH |
| 「〜ね」「〜よ」で終わる | MEDIUM |
| 長文＋句読点多い | LOW |
| 「…」「――」含む | LOW |

### 文体判定

| パターン | 文体 |
|:---------|:-----|
| 「？」終了 | QUESTION |
| 「！」＋命令語 | COMMAND |
| 独白系（「〜か」「〜な」） | MONOLOGUE |
| その他 | STATEMENT |

---

## プリセット定義

| 名前 | speed | pitch | intonation | volume | 用途 |
|:-----|:-----:|:-----:|:----------:|:------:|:-----|
| `news` | 1.0 | 0.0 | 0.9 | 1.0 | ニュース |
| `youtube` | 1.1 | 0.0 | 1.2 | 1.0 | YouTube解説 |
| `story` | 0.9 | 0.0 | 1.3 | 0.95 | 朗読 |
| `game` | 1.2 | 0.05 | 1.5 | 1.1 | ゲーム実況 |
| `education` | 0.85 | 0.0 | 1.0 | 1.0 | 教育 |
| `conversation` | 1.0 | 0.0 | 1.1 | 1.0 | 日常会話 |

---

## Antigravityレビュー基準

Antigravity（自身）は以下の観点でルールベースの判定結果をレビューする:

1. **感情と文脈の整合**: 台詞の意味に対して感情判定が合っているか
2. **パラメータの妥当性**: intonationScale等が極端でないか
3. **キャラクターとの整合**: 指定キャラの性格に合った設定か
4. **ピッチブースト対象の適切さ**: 強調語の選択が正しいか

> [!WARNING]
> レビューで問題が見つかった場合は、修正理由を明記して再策定する。
> 最大3回までの修正ループ。それ以上は現状のまま生成。

---

## モーラ演出ディレクター（Layer M）

### gpt-oss:20bプロンプト制御

| パラメータ | 範囲 | 説明 |
|:-----------|:-----|:-----|
| `stretch` | 0.7-1.8 | 母音伸縮率（1.0=変更なし, 1.3-1.5=優しい強調）|
| `pitch_shift` | -0.2〜+0.2 | ピッチ微調整（控えめに）|
| `pause_before` | 0-0.5s | ワード前の間 |
| `pause_after` | 0-0.5s | ワード後の間 |
| `comma_pause` | 0.2-0.8s | 「、」（読点）ポーズ |
| `period_pause` | 0.5-1.5s | 「。」（句点）ポーズ |
| `speed` | 0.7-1.1 | セグメントテンポ |

### 注意事項

> [!WARNING]
> - READING_FIXESにVOICEVOXが正しく読める単語を入れないこと（例: 旋律→センリツは不要）
> - カタカナ置換はVOICEVOXが読めない単語のみに限定
> - 全pause_moraは読点（、）として扱う（句点はセグメント間silence）

---

## 物理層スクリプト

| スクリプト | 場所 | 役割 |
|:-----------|:-----|:-----|
| `voicevox_client.py` | `エージェント/VOICEVOXエージェント/scripts/` | API通信（accent_phrases/mora_data含む） |
| `accent_verifier.py` | 同上 | **Layer 0: アクセント辞書照合**（pitch再計算OFF） |
| `mora_director.py` | 同上 | **Layer M: gpt-oss:20bモーラ演出ディレクター**（★v4.0.0追加） |
| `base_tuner.py` | 同上 | Layer 1: ベースチューニング（デフォルトOFF） |
| `text_preprocessor.py` | 同上 | テキスト前処理 |
| `situation_analyzer.py` | 同上 | ルールベース状況分析（フォールバック用） |
| `adjustment_planner.py` | 同上 | Layer 2: 調整プラン策定（フォールバック用） |
| `presets.py` | 同上 | プリセット管理 |
| `voicevox_agent.py` | 同上 | CLIエントリポイント |

## ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

ログ保存先: `_logs/autonomy/voicebox/{YYYYMMDD}/`
