# CODEXAPP Skill v1.4.0


> [!CAUTION]








## 役割境界





> **CDP経由でCODEXデスクトップアプリ（Electron）を操作する技術仕様。**


> `/codex` 統合エージェントの子エージェント（App Control）として動作する。





> [!CAUTION]


> **操作でミスが発生したら、まずこのSPEC.mdとGUIDE.mdの手順を確認すること。**


> 固定座標やハードコードされたクラス名に頼らず、以下の検証済み手順に従う。





## 📜 ドキュメント正本境界





| ドキュメント | 正本範囲 | 備考 |


|:-------------|:---------|:-----|


| `SPEC.md`（本ファイル） | **技術契約**（data属性一覧・判定条件・ポート規約） | 「何を保証するか」を定義 |


| `GUIDE.md` | **実行手順**（CLIコマンド例のみ） | JSセレクタ等のコード例は書かない |


| `codexapp_cdp_client.py` | **実装の唯一の正本** | セレクタ・ロジックの真実源 |





> [!IMPORTANT]


> SPEC.md内の実装コード片は**参考情報**。セレクタ変更時は `codexapp_cdp_client.py` のみ修正すれば動作する。





## 🔌 CDP接続仕様





| 項目 | 値 |


|:-----|:---|


| **ポート** | `9224`（エージェント専用） |


| **プロトコル** | Chrome DevTools Protocol (WebSocket) |


| **ターゲットURL** | `http://127.0.0.1:9224/json` |


| **WebSocket** | `ws://127.0.0.1:9224/devtools/page/{ID}` |





### ポート規約





| ポート | 用途 |


|:-------|:-----|


| 9222 | Chrome（予約） |


| 9223 | CODEXAPP（手動用） |


| **9224** | **CODEXAPP（エージェント用）** |





---





## 🚀 起動方法





```powershell


# CODEXAPP をCDP付きで起動（再利用モード）


powershell -NoProfile -ExecutionPolicy Bypass `


  -File "Codex-Windows\scripts\run.ps1" `


  -Reuse -CdpPort 9224


```





> [!IMPORTANT]


> `run.ps1` には以下の修正が適用済み:


> - `--remote-allow-origins=*` — 外部WebSocket接続許可


> - `$skipNative` 条件 — マルチインスタンス時のファイルロック回避


> - `$electronExe` 初期化 — スキップ時の未定義エラー回避





> [!WARNING]


> `--remote-allow-origins=*` はローカル127.0.0.1のみでの利用を前提。


> 外部ネットワークからのアクセスは遮断されていること。





### 起動確認





```powershell


netstat -ano | findstr ":9224" | findstr "LISTENING"


```





---





## 📝 テキスト送信手順





> [!CAUTION]


> **入力欄はtextareaではない。ProseMirror（contenteditable div）である。**





### Step 1: CDP接続





```python


import requests, json, websocket


targets = requests.get("http://127.0.0.1:9224/json").json()


# #9: title="Codex"を優先選択


page = next(


    (t for t in targets if t.get("type") == "page" and "Codex" in t.get("title", "")),


    next((t for t in targets if t.get("type") == "page"), None)


)


if not page:


    raise RuntimeError("CDPターゲットが見つかりません")


ws = websocket.create_connection(page["webSocketDebuggerUrl"])


```





### Step 2: ProseMirrorにフォーカス（動的座標取得）





**概要**: `SELECTORS["prosemirror"]` → `SELECTORS["prosemirror_fallback"]` の順で検索し、`getBoundingClientRect()` で座標を取得→ `Input.dispatchMouseEvent` でクリック。





> [!TIP]


> 実装詳細は `codexapp_cdp_client.py` の `send_message()` を参照。





### Step 3: テキスト入力（トークン付き）





```python


token = "[[REQ:20260210-123456]]"  # 一意トークン


cdp("Input.insertText", {"text": f"質問テキスト\n{token}"})


```





> [!WARNING]


> `textarea.value` や `nativeSetter` は**画面に反映されない**。必ず `Input.insertText` を使用。





### Step 4: 送信ボタンをJSクリック





> [!CAUTION]


> **固定座標やクラス名ベースのボタン検出は使わない。**





**検出ロジック**: `SELECTORS["composer"]` → `SELECTORS["prosemirror"]` → 親要素遡行でcomposer領域を特定。「右端のSVGアイコンボタン（テキストなし・無効でない）」を送信ボタンとしてクリック。





**フォールバック**: ボタンが見つからない場合はEnterキーで送信。





> [!TIP]


> 実装詳細は `codexapp_cdp_client.py` の `send_message()` を参照。





**検証済みdata属性一覧（v1.4.0）**:





| data属性 | 用途 |


|:---------|:-----|


| `data-thread-find-target="conversation"` | 会話コンテナ |


| `data-thread-find-target="review"` | レビューコンテナ |


| `data-codex-composer="true"` | ProseMirror入力エリア |


| `data-thread-find-composer` | composer周辺の祖先 |


| `data-thread-find-skip="true"` | 非表示要素マーカー |





### Step 5: 応答取得





> [!CAUTION]


> **v1.4.0ではクラス名（`.group.min-w-0`等）に依存しない。**


> `data-thread-find-target`配下のブロック走査でアシスタント応答を収集。


> `data-thread-find-skip="true"`要素を除去してクリーンテキストを取得。





**推奨方法**: `codexapp_cdp_client.py` の `poll_response` または `get-latest` を使用





```powershell


# 最新応答取得


python .agent\workflows\codex\sub_agents\app\scripts\codexapp_cdp_client.py get-latest


```





**応答完了判定**（`isComplete` = 5条件AND）:


1. `fullResponseText.length > 0` — 応答テキストが存在


2. `looksReady` — 送信ボタンが活性化（`data-thread-find-composer`内の有効SVGボタン）


3. `stableTicks >= 6` — テキストハッシュが6回連続不変


4. `!isThinking` — 「思考中」テキストでない


5. `!isIntermediate` — 中間ログ（「実行済みコマンド：」「作業しました」）でない





**手動ポーリング**: 非推奨。`codexapp_cdp_client.py send` を使用のこと。





### Step 6: 新スレッドを開く（必要時）





```powershell


# CLI: --new-thread オプション


python .agent\workflows\codex\sub_agents\app\scripts\codexapp_cdp_client.py send --new-thread "質問テキスト"


```





> [!TIP]


> Python APIでは `CdpClient.open_new_thread()` で同等の処理が可能。





### Step 7: 入力欄のテキスト清掃（必要時）





```powershell


# CLI: --clear-input オプション


python .agent\workflows\codex\sub_agents\app\scripts\codexapp_cdp_client.py send --clear-input "質問テキスト"


```





> [!TIP]


> Python APIでは `CdpClient.clear_input()` で同等の処理が可能。





---





## 🔧 統合スクリプト





`scripts/codexapp_cdp_client.py` で送信・応答取得がワンコマンドで可能。





```powershell


# メッセージ送信＆応答取得（トークン自動付与）


python .agent\workflows\codex\sub_agents\app\scripts\codexapp_cdp_client.py send "質問テキスト"





# 新スレッドを開いてから送信


python .agent\workflows\codex\sub_agents\app\scripts\codexapp_cdp_client.py send --new-thread "質問テキスト"





# 最新の応答を取得（トークン自動読込 — send時のトークンを自動で使用）


python .agent\workflows\codex\sub_agents\app\scripts\codexapp_cdp_client.py get-latest





# 最新の応答を取得（トークン手動指定）


python .agent\workflows\codex\sub_agents\app\scripts\codexapp_cdp_client.py get-latest --token "[[REQ:20260210-123456-a1b2c3d4]]"





# オプション: ポート・タイムアウト・出力先


python .agent\workflows\codex\sub_agents\app\scripts\codexapp_cdp_client.py --port 9224 -o result.txt send "質問" --timeout 120





# get-latestでも-o対応（ファイル保存）


python .agent\workflows\codex\sub_agents\app\scripts\codexapp_cdp_client.py -o result.txt get-latest


```





---





## ⚠️ 既知の注意点





| 問題 | 原因 | 対策 |


|:-----|:-----|:-----|


| textarea操作が反映されない | opacity:0のダミー | `.ProseMirror` を操作 |


| WebSocket拒否 | origin制限 | `--remote-allow-origins=*` |


| 起動時ファイルロック | マルチインスタンス | `-Reuse` + `$skipNative` 修正 |


| ポートTIME_WAIT | 前インスタンスの残骸 | 数秒待って再起動 |


| ~~`bg-token-foreground`で誤クリック~~ | ~~v1.4.0で解消~~ | `data-thread-find-composer`内のSVGボタンで特定 |


| ~~`main`要素が存在しない~~ | ~~v1.4.0で解消~~ | `data-thread-find-target`でコンテナを直接特定 |


| `Page.navigate`でアプリ破壊 | Electronアプリでは使用禁止 | 新スレッドはStep 6のボタンクリックで開く |


| ProseMirrorに前回テキスト残留 | 新スレッドでもクリアされない場合あり | Ctrl+A→Delete or 新スレッド後に確認 |


| 中間応答を最終応答と誤判定 | CODEX作業中の出力（「実行済みコマンド」「作業しました」） | `isIntermediate`判定で自動検出・`isComplete`を抑制 |





---





## 📋 依存ライブラリ





| ライブラリ | 用途 |


|:-----------|:-----|


| `requests` | CDPターゲット取得 |


| `websocket-client` | CDP WebSocket通信 |





## 💡 Rules





- **ProseMirrorを操作**（textareaは使わない）


- **Input.insertText でテキスト入力**


- **送信ボタンは`data-thread-find-composer`内SVGボタン**


- **応答取得は`data-thread-find-target`+トークン追跡方式**


- **ポート9224 固定**（9222=Chrome, 9223=手動用）


- **Language**: 日本語


