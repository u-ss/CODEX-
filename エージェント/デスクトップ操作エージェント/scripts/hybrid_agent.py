"""
Hybrid PC Agent Observability Demo (Windows)
- UI Automation (pywinauto/uia) prioritized
- Fallback to Screenshot + VLM (Gemini) structured JSON output

ChatGPTとの対話から得たproduction-readyな実装例。

依存:
pip install pywinauto mss pillow pydantic google-genai
"""

from __future__ import annotations

import os
import re
import time
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Literal

# --- UIA (pywinauto) ---
from pywinauto import Desktop
from pywinauto.timings import TimeoutError as PywinautoTimeoutError

# --- Screenshot ---
import mss
from PIL import Image
from io import BytesIO

# --- Schema / parsing ---
from pydantic import BaseModel, Field, ValidationError

# --- Gemini (VLM) ---
from google import genai
from google.genai import types


# -----------------------------
# Data models
# -----------------------------

@dataclass
class UIAElementHint:
    """Minimal element hint for downstream policy."""
    name: str
    control_type: str
    is_enabled: bool
    is_visible: bool


@dataclass
class Observation:
    source: Literal["uia", "vlm"]
    timestamp: float = field(default_factory=time.time)
    confidence: float = 0.0
    facts: Dict[str, Any] = field(default_factory=dict)


class VLMWindow(BaseModel):
    title: str
    app: Optional[str] = None
    is_active: bool = False
    bbox: Optional[Tuple[int, int, int, int]] = None


class VLMErrorDialog(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    severity: Optional[Literal["info", "warning", "error", "unknown"]] = "unknown"


class VLMState(BaseModel):
    """VLM must output ONLY this JSON (structured outputs)."""
    active_app: Optional[str] = Field(default=None)
    active_window_title: Optional[str] = Field(default=None)
    windows: List[VLMWindow] = Field(default_factory=list)
    error_dialog: Optional[VLMErrorDialog] = None
    mode: Literal["ready", "loading", "error", "login", "unknown"] = "unknown"
    actionable_hints: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    unknowns: List[str] = Field(default_factory=list)


# -----------------------------
# Screenshot capture
# -----------------------------

class ScreenCapturer:
    def __init__(self, monitor_index: int = 1):
        self.monitor_index = monitor_index

    def capture_png_bytes(self) -> bytes:
        with mss.mss() as sct:
            mon = sct.monitors[self.monitor_index]
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.rgb)
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()


# -----------------------------
# UIA Observer (pywinauto)
# -----------------------------

class UIAObserver:
    def __init__(self):
        self.desktop = Desktop(backend="uia")

    def find_window(self, title_re: str, timeout_s: float = 2.0):
        end = time.time() + timeout_s
        while time.time() < end:
            wins = self.desktop.windows(title_re=title_re, visible_only=True)
            if wins:
                return wins[0]
            time.sleep(0.1)
        return None

    def detect_error_dialog(self) -> Optional[Dict[str, Any]]:
        dialogs = self.desktop.windows(control_type="Window", visible_only=True)
        error_keywords = ["error", "エラー", "例外", "warning", "警告", "失敗", "停止"]

        for w in dialogs:
            try:
                title = w.window_text() or ""
                if any(k.lower() in title.lower() for k in error_keywords):
                    msg = self._extract_dialog_message(w)
                    return {"title": title, "message": msg, "severity": "error"}
            except Exception:
                continue
        return None

    def _extract_dialog_message(self, win) -> Optional[str]:
        try:
            texts = []
            for ctrl in win.descendants():
                try:
                    ctype = ctrl.friendly_class_name()
                    if ctype in ("Text", "Static"):
                        t = ctrl.window_text()
                        if t:
                            texts.append(t)
                except Exception:
                    pass
            msg = "\n".join(texts).strip()
            return msg if msg else None
        except Exception:
            return None

    def is_input_ready(self, win) -> bool:
        try:
            win.wait("exists enabled visible ready", timeout=2)
            return True
        except PywinautoTimeoutError:
            return False
        except Exception:
            return False

    def observe(self) -> Observation:
        facts: Dict[str, Any] = {}

        try:
            active = self.desktop.get_active()
            active_title = active.window_text()
            facts["active_window_title"] = active_title
            facts["active_window_exists"] = True
            facts["active_window_ready"] = self.is_input_ready(active)
        except Exception:
            facts["active_window_exists"] = False
            facts["active_window_ready"] = False

        err = self.detect_error_dialog()
        facts["has_error_dialog"] = err is not None
        if err:
            facts["error_dialog"] = err

        login_win = self.find_window(title_re=".*(Login|Sign in|ログイン).*", timeout_s=0.2)
        facts["has_login_window"] = login_win is not None

        browser_win = self.find_window(title_re=".*(Chrome|Edge|Firefox|Brave).*", timeout_s=0.2)
        facts["has_browser_window"] = browser_win is not None

        conf = 0.2
        if facts.get("active_window_exists"):
            conf += 0.4
        if facts.get("active_window_ready"):
            conf += 0.2
        if facts.get("has_error_dialog"):
            conf += 0.1
        conf = min(1.0, conf)

        return Observation(source="uia", confidence=conf, facts=facts)


# -----------------------------
# VLM Observer (Gemini structured output)
# -----------------------------

class GeminiVLMObserver:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.schema = VLMState.model_json_schema()

    def infer_state_from_screenshot(self, png_bytes: bytes) -> Observation:
        image_part = types.Part.from_bytes(data=png_bytes, mime_type="image/png")

        prompt = (
            "You are a UI state extractor.\n"
            "Look ONLY at the provided screenshot.\n"
            "Return the current UI mode and any visible error dialog.\n"
        )

        resp = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config={
                "response_mime_type": "application/json",
                "response_json_schema": self.schema,
            },
        )

        try:
            state = VLMState.model_validate_json(resp.text)
        except ValidationError:
            parsed = json.loads(resp.text)
            state = VLMState.model_validate(parsed)

        facts = state.model_dump()
        return Observation(source="vlm", confidence=float(state.confidence), facts=facts)


# -----------------------------
# Hybrid Observer
# -----------------------------

class HybridObserver:
    def __init__(
        self,
        uia: UIAObserver,
        vlm: GeminiVLMObserver,
        capturer: ScreenCapturer,
        uia_threshold: float = 0.65,
    ):
        self.uia = uia
        self.vlm = vlm
        self.capturer = capturer
        self.uia_threshold = uia_threshold

    def observe(self) -> Observation:
        uia_obs = self.uia.observe()

        if self._uia_is_decisive(uia_obs):
            return uia_obs

        png = self.capturer.capture_png_bytes()
        vlm_obs = self.vlm.infer_state_from_screenshot(png)
        return vlm_obs

    def _uia_is_decisive(self, obs: Observation) -> bool:
        f = obs.facts
        if obs.confidence < self.uia_threshold:
            return False
        if f.get("has_error_dialog"):
            return True
        if f.get("has_login_window"):
            return True
        if f.get("active_window_exists") and f.get("active_window_ready"):
            return True
        return False


# -----------------------------
# Action & Policy
# -----------------------------

@dataclass
class Action:
    name: str
    run: Any
    expected: Any
    safe: bool = True


class Policy:
    def __init__(self, uia: UIAObserver):
        self.uia = uia

    def decide(self, obs: Observation) -> Optional[Action]:
        f = obs.facts

        if f.get("has_error_dialog"):
            def dismiss():
                dlg = self.uia.find_window(title_re=".*(Error|エラー|警告).*", timeout_s=0.5)
                if dlg:
                    for bname in ["OK", "はい", "閉じる", "Close"]:
                        try:
                            btn = dlg.child_window(title=bname, control_type="Button")
                            if btn.exists(timeout=0.1):
                                btn.click_input()
                                return
                        except Exception:
                            pass

            return Action(
                name="dismiss_error_dialog",
                run=dismiss,
                expected=lambda o: not o.facts.get("has_error_dialog", False),
            )

        if f.get("active_window_ready"):
            return Action(name="noop_ready", run=lambda: None, expected=lambda o: True)

        return None


def execute_with_recovery(action: Action, observer: HybridObserver, retries: int = 2):
    for i in range(retries + 1):
        try:
            action.run()
            new_obs = observer.observe()
            if action.expected(new_obs):
                return new_obs
        except Exception:
            time.sleep(0.3 * (i + 1))
    raise RuntimeError(f"Action failed: {action.name}")


# -----------------------------
# Main loop
# -----------------------------

def main():
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        raise RuntimeError("Set GEMINI_API_KEY env var.")

    uia = UIAObserver()
    capturer = ScreenCapturer(monitor_index=1)
    vlm = GeminiVLMObserver(api_key=gemini_key)
    observer = HybridObserver(uia=uia, vlm=vlm, capturer=capturer)
    policy = Policy(uia=uia)

    max_steps = 30
    for step in range(max_steps):
        obs = observer.observe()
        print(f"[{step}] source={obs.source} conf={obs.confidence:.2f}")

        action = policy.decide(obs)
        if action is None:
            print("No action needed")
            break

        print(f" -> action={action.name}")
        execute_with_recovery(action, observer)

    print("Done.")


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path

    _here = _Path(__file__).resolve()
    for _parent in _here.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in _sys.path:
                _sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(_run_logged_main("desktop", "hybrid_agent", main, phase_name="HYBRID_AGENT_RUN"))

