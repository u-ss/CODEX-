# マルチモニター対応・座標取得・SSタイミング

ChatGPTとの対話から得た実践的パターン。

## 0. 前提：DPI Aware設定（必須）

```python
import ctypes

def set_dpi_aware():
    """モニターごとのDPIに追従させる"""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "PER_MONITOR_AWARE_V2"
    except:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "PER_MONITOR_DPI_AWARE"
    except:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "SYSTEM_DPI_AWARE"
    except:
        return "NOT_SET"
```

## 1. 要素座標の正確な取得

**座標クリックではなく、UI要素の矩形を取る**

```python
from pywinauto import Desktop
from pywinauto.mouse import click

def get_elem_rect_and_click(title_re: str, control_type: str = None, auto_id: str = None):
    desk = Desktop(backend="uia")
    win = desk.window(title_re=title_re)
    win.wait("visible", timeout=10)
    win.set_focus()
    
    crit = {}
    if auto_id:
        crit["auto_id"] = auto_id
    if control_type:
        crit["control_type"] = control_type
    
    elem = win.child_window(**crit).wrapper_object()
    r = elem.rectangle()  # 画面座標（マイナス含む）
    cx = (r.left + r.right) // 2
    cy = (r.top + r.bottom) // 2
    
    click(coords=(cx, cy))
    return r, (cx, cy)
```

## 2. マルチモニター座標

- **マイナス座標は正常**（左モニター等）
- Windowsは「仮想スクリーン」で全モニター管理

```python
import ctypes

def get_virtual_screen_rect():
    user32 = ctypes.windll.user32
    return {
        "left": user32.GetSystemMetrics(76),   # SM_XVIRTUALSCREEN
        "top": user32.GetSystemMetrics(77),    # SM_YVIRTUALSCREEN
        "width": user32.GetSystemMetrics(78),  # SM_CXVIRTUALSCREEN
        "height": user32.GetSystemMetrics(79), # SM_CYVIRTUALSCREEN
    }
# 例: {'left': -1920, 'top': 0, 'width': 3840, 'height': 1080}
```

## 3. フォールバック戦略

```
1. UIA再探索（条件を緩める）
2. キーボード操作（Tab → Enter）
3. 座標クリック（ウィンドウ相対）
4. 画像マッチング（ウィンドウ内限定）
5. OCR/ログ → 人が対応
```

## 4. SSタイミング戦略

| タイミング | 範囲 | 理由 |
|:-----------|:-----|:-----|
| アクション直前 | ウィンドウ | 何を触るか証跡 |
| アクション直後 | ウィンドウ | 反映確認 |
| 待機後 | ウィンドウ | 遷移後確認 |
| **失敗時** | **全画面** | 原因が別モニターにある可能性 |
| 分岐判断前 | ウィンドウ | 意思決定の根拠 |

```python
def safe_step(step_name, title_re, action_fn):
    # 直前SS（ウィンドウ）
    screenshot_window(title_re, f"{step_name}_before.png")
    
    try:
        result = action_fn()
        # 直後SS（ウィンドウ）
        screenshot_window(title_re, f"{step_name}_after.png")
        return result
    except Exception:
        # 失敗時：全画面SS
        vs = get_virtual_screen_rect()
        screenshot_region(vs["left"], vs["top"], vs["width"], vs["height"], 
                          f"{step_name}_ERROR_full.png")
        raise
```

## 5. 重要Tips

1. **座標クリックは最終手段** - UIA要素操作を優先
2. **DPI aware設定が必須** - 最初に呼ぶ
3. **画像マッチはウィンドウ内限定** - 誤爆防止＆高速化
4. **SSは通常:ウィンドウ、失敗:全体**
