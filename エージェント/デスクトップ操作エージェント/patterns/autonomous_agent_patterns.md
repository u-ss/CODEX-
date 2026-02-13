# 自律PC操作エージェント設計パターン

ChatGPTとの対話から得られた、production-readyな設計パターン集。

## 1. 中核アーキテクチャ: Perceive-Decide-Act ループ

```python
class Observation:
    url: str
    title: str
    facts: Dict[str, Any]
    timestamp: float

class Action:
    name: str
    run: Callable
    expected: Callable[[Observation], bool]  # 成功判定

class AgentRunner:
    def run_steps(self, max_steps=30):
        for step in range(max_steps):
            obs = self.observer.observe()      # Perceive
            action = self.policy.decide(obs)   # Decide
            action.run()                       # Act
            
            # 成功判定
            new_obs = self.observer.observe()
            if not action.expected(new_obs):
                raise RuntimeError("Action failed")
```

## 2. 状態検知パターン

### 2.1 State Detectors（コードで構造化）
```python
class BrowserObserver:
    def observe(self) -> Observation:
        facts = {
            "has_login_form": self.page.get_by_role("textbox", name="Email").count() > 0,
            "has_dashboard": self.page.get_by_role("heading", name="Dashboard").count() > 0,
            "has_spinner": self.page.get_by_test_id("loading").count() > 0,
            "has_error": self.page.get_by_test_id("error-banner").count() > 0,
        }
        return Observation(url=self.page.url, title=self.page.title(), facts=facts)
```

### 2.2 Hierarchical State
- **画面(Scene)**: login / dashboard / settings
- **サブ状態(Mode)**: loading / error / ready / empty

## 3. 意思決定パターン: FSM + ガード条件

```python
class AgentPolicy:
    def decide(self, obs: Observation) -> Optional[Action]:
        f = obs.facts
        
        # 優先順位で判定
        if f.get("has_error_banner"):
            return Action("reload", lambda: self.page.reload())
        if f.get("has_spinner"):
            return Action("wait", lambda: time.sleep(1))
        if f.get("has_login_form"):
            return Action("login", self.do_login)
        if f.get("has_dashboard"):
            return Action("next_task", self.do_task)
        return None  # 未知状態
```

## 4. エラーリカバリ: Retry + Fallback + Checkpoint

```python
def execute_with_recovery(action, observer, retries=2):
    for i in range(retries + 1):
        try:
            action.run()
            if action.expected(observer.observe()):
                return
        except Exception as e:
            time.sleep(0.3)
    
    # Fallback
    observer.page.reload()
    raise RuntimeError(f"Failed after {retries} retries")
```

## 5. ハイブリッド戦略: UIA優先 → VLMフォールバック

```python
class HybridObserver:
    def observe(self) -> Observation:
        # 1. UIAで試行
        uia_obs = self.uia_observer.observe()
        if uia_obs.confidence > 0.8:
            return uia_obs
        
        # 2. VLMにフォールバック
        screenshot = self.capturer.capture_png_bytes()
        vlm_state = self.vlm.analyze(screenshot)
        return Observation(source="vlm", facts=vlm_state.dict())
```

## 6. VLM構造化出力（Gemini）

```python
class VLMState(BaseModel):
    active_app: Optional[str]
    mode: Literal["ready", "loading", "error", "login", "unknown"]
    error_dialog: Optional[Dict]
    confidence: float
    actionable_hints: List[str]
```

## 7. 安全停止条件

- フォーカスが予期せず外れた
- エラーダイアログ検出
- 未知状態が連続
- CAPTCHA/2FA検出
- ユーザー操作を検知

## 8. 4層アーキテクチャ統合

```
Layer 2+ (CDP/Playwright) → ブラウザDOM操作
Layer 3 (UIA/Pywinauto)   → ウィンドウ/要素操作
Layer 1 (PyAutoGUI)       → 座標/画像
Layer 0 (VLM)             → 画面理解フォールバック
```
