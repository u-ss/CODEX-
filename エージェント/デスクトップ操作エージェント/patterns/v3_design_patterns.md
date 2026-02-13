# Desktop Control v3.0 設計パターン

ChatGPT (GPT-5.2 Thinking) との10ラリー壁打ちで得た設計パターン詳細。

## 1. Action型統一

**ファイル**: `core/actions.py`

すべての操作を統一的なインターフェースで扱う：

```python
from core import Click, TypeText, WaitUntil, SelectorTarget

# クリック
action = Click(target_=SelectorTarget("#submit-button"))

# テキスト入力
action = TypeText(
    target_=SelectorTarget("textarea"),
    text="Hello World",
    submit=True
)

# 条件待機
action = WaitUntil(
    condition=lambda: page.query_selector(".response") is not None,
    description="response_visible"
)
```

## 2. screen_key（画面識別）

**ファイル**: `core/screen_key.py`

画面を識別するための指紋情報を生成：

| キータイプ | 構成要素 | 用途 |
|-----------|---------|------|
| coarse_key | process + win_class | 大まかな画面分類 |
| mid_key | + URL/title | 推奨（バランス良い） |
| fine_key | + UIAツリー構造 | 厳密だが分裂しやすい |

## 3. Circuit Breaker

**ファイル**: `core/circuit_breaker.py`

```
CLOSED → (3回失敗) → OPEN → (30秒後) → HALF_OPEN → (1回成功) → CLOSED
                                      ↓ (失敗)
                                     OPEN
```

## 4. HSM（階層状態機械）

```
RootState
├── SafeState（安全な状態）
└── AppState
    ├── MainScreen
    └── DialogState
        └── ErrorDialog
```

## 5. エラー回復戦略

| タイプ | 例 | 回復 |
|-------|---|-----|
| Transient | タイムアウト、フォーカス競合 | リトライ |
| Deterministic | 要素不存在 | SAFE_HOME戻し |
| Unsafe | 破壊的操作確認 | 即停止 |

## 6. バンディット学習（将来実装）

UCB1ベースでExecutor選択を最適化。SQLite永続化。

## 7. マルチモニター

DPI Awareness v2 + MonitorFromWindow。

---

*詳細はChatGPT壁打ちのスクリーンショット参照: `_screenshots/`*
