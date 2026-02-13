---
name: VOICEBOX Agent v4.0.0
description: gpt-oss:20bモーラ演出ディレクター統合・高品質TTS
---

# VOICEBOX Agent v4.0.0 (`/voicebox`)

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと

## 概要

gpt-oss:20b（ローカルLLM）がテキストの感情・場面を分析し、モーラ単位の演出とセグメントごとのテンポ・ポーズを自動決定し、VOICEVOXで高品質音声を生成。

## 手順

### Step 1: VOICEVOX接続確認

```python
# VOICEVOX APIサーバーが起動しているか確認
# デフォルト: http://localhost:50021
python エージェント/VOICEVOXエージェント/scripts/voicevox_agent.py --check
```

接続不可時は「VOICEVOXを起動してください」とユーザーに報告して終了。

---

### Step 2: 入力受付

入力パターン:
- **テキスト直接指定**: `--text "台詞"`
- **ファイル指定**: `--file script.txt`
- **コンテキスト付き**: `--text "台詞" --character "四国めたん" --scene "怒りの場面"`

オプション:
- `--no-accent-verify`: Layer 0アクセント照合を無効化（デフォルトON）
- `--no-mora-direction`: Layer Mモーラ演出を無効化（デフォルトON）
- `--enable-base-tuning`: Layer 1ベースチューニングを有効化（デフォルトOFF）

---

### Step 3: テキスト前処理

```
① 長文分割（句読点・改行ベース）
② 伸ばし音補完（インフィニティ→インフィニティー）
③ 読点挿入（長い文に呼吸ポイント追加）
```

---

### Step 4: ルールベース状況分析

```
入力テキストから以下を自動判定:
  - 感情: NEUTRAL / JOY / ANGER / SADNESS / SURPRISE / WHISPER
  - テンション: HIGH / MEDIUM / LOW
  - 文体: STATEMENT / QUESTION / COMMAND / MONOLOGUE
  - 強調語: ピッチブーストすべき単語リスト
```

SKILL.mdの判定ルールに従って分析。

---

### Step 5: 調整プラン策定

```
分析結果 → 以下を決定:
  - ベースプリセット選択（news/youtube/story/game/education/conversation）
  - パラメータ微調整（テンションに応じた補正）
  - 感情スタイル（キャラクターが対応していれば）
  - モーラ単位調整指示（強調語のピッチブースト量、疑問文の語尾上昇量）
```

---

### Step 6: Antigravityレビュー（★核心）

> [!IMPORTANT]
> **Antigravity自身が以下の観点でレビューする。これが自動で行われる。**

```
レビュー観点:
  1. 台詞の意味と感情判定は合っているか？
     例: 「ありがとう…」→ 喜びではなく悲しみかもしれない
  2. パラメータは極端すぎないか？
     例: intonationScale 1.5 は通常の台詞には過剰
  3. キャラクター設定と整合しているか？
     例: 落ち着いたキャラに高テンション設定は不適切
  4. 強調語の選択は正しいか？
     例: 助詞「は」を強調するのは不自然

判定結果:
  - OK → Step 7へ
  - 修正必要 → 修正理由を明記 → Step 5に戻る（最大3回）
```

---

### Step 7: 音声合成（4層パイプライン）

```
① VOICEVOX audio_query API でクエリ生成
② Layer 0: アクセント辞書照合
   → MeCab+UniDicで形態素解析 → aType取得
   → VOICEVOXのaccent値と照合・不一致修正
   → ⚠️ pitch再計算はOFF（recalculate_pitch=False）
③ Layer M: モーラ演出ディレクター（★v4.0.0）
   → gpt-oss:20bにコンテキスト+テキスト送信
   → ワード演出: stretch/pitch_shift/pause_before/pause_after
   → 読点（、）ポーズ: pause_mora.vowel_lengthに適用
   → セグメントテンポ: speedScaleに適用
④ グローバルパラメータ適用（pitch/intonation/volume）
⑤ VOICEVOX synthesis API でWAV生成
⑥ セグメントごとにWAV保存 + 調整ログJSON出力
```

---

### Step 8: ffmpeg結合（句点ポーズ挿入）

```
① 各セグメントWAVの間に句点（。）ポーズとしてsilence WAVを挿入
   → get_period_pause(plan, segment_index) でLLMが決定した秒数を取得
   → ffmpeg -f lavfi -i anullsrc で無音WAV生成
② concat用テキストファイル生成（セグメントWAV + silence WAV の交互リスト）
③ ffmpeg -f concat -safe 0 -i concat_list.txt -c copy final.wav
④ 結合WAVの出力確認
```

> [!IMPORTANT]
> 「。」（句点）のポーズは**セグメント間の無音WAV**で制御。
> audio_query内のpause_moraは全て「、」（読点）のポーズ。
> この分離を誤ると、読点に句点の長いポーズが適用されて不自然になる。

---

## 出力

| ファイル | 保存先 |
|:---------|:-------|
| セグメントWAV | `_outputs/voicebox/{YYYYMMDD}/{name}_{label}.wav` |
| 結合WAV | `_outputs/voicebox/{YYYYMMDD}/{name}_final.wav` |
| 調整ログ | `_outputs/voicebox/{YYYYMMDD}/{name}_log.json` |

### 調整ログフォーマット

```json
{
  "text": "入力テキスト",
  "analysis": {
    "emotion": "SADNESS",
    "tension": "LOW",
    "style": "MONOLOGUE"
  },
  "mora_direction": {
    "model_used": "gpt-oss:20b",
    "context_summary": "nostalgic, bittersweet, quiet melancholy",
    "directions": [
      {"segment_index": 0, "word": "懐かしい", "stretch": 1.4, "reason": "elongate for nostalgia"}
    ],
    "punctuation": [
      {"segment_index": 0, "comma_pause": 0.45, "period_pause": 1.0, "speed": 0.90, "reason": "contemplative pacing"}
    ]
  },
  "accent_verification": {
    "total_phrases": 3,
    "mismatches": 1,
    "fixes": [
      {"phrase_idx": 0, "old_accent": 3, "new_accent": 1, "word": "今日", "confidence": 0.8}
    ]
  },
  "speaker_id": 14,
  "output_path": "...",
  "duration_sec": 45.2
}
```

## Rules

- VOICEVOX接続不可時は即座に失敗終了
- レビューループは最大3回
- Layer 0アクセント照合はデフォルトON、pitch再計算は常にOFF
- Layer M（モーラ演出）はgpt-oss:20b使用。ollama未起動時はスキップ
- Layer 1ベースチューニングはデフォルトOFF（Layer M使用時は不要）
- READING_FIXESはVOICEVOXが読めない単語のみ（読める単語のカタカナ置換禁止）
- 句点（。）ポーズはセグメント間silence、読点（、）ポーズはpause_mora
- 全調整パラメータを再現可能な形でログ保存
- 依存: `fugashi`, `unidic-lite`（Layer 0用）、`ollama`（Layer M用）
- Language: 日本語
