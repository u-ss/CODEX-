"""
Autonomous PC Operation Agent (Windows) — Production-ready-ish single-file implementation
---------------------------------------------------------------------------------------
Requirements implemented:
1) Perceive-Decide-Act loop
2) 4-layer architecture: CDP(Playwright) > UIAutomation(pywinauto) > PyAutoGUI > VLM(Gemini)
3) Error recovery + automatic fallback
4) Safety stop (danger detection)
5) Execution logging (JSONL + standard log + screenshots)

Use cases:
- Search interesting videos on YouTube
- Ask ChatGPT and retrieve answer
- Type text into Notepad

---------------------------------------------------------------------------------------
SETUP (Windows)
1) Install deps:
   pip install playwright pywinauto pyautogui mss pillow pydantic google-genai
   python -m playwright install chromium

2) Start Chrome with CDP enabled (one-time shortcut recommended):
   chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\tmp\\chrome-cdp"

3) Environment variables:
   set CHROME_CDP_URL=http://127.0.0.1:9222
   set GEMINI_API_KEY=YOUR_KEY   (optional; VLM is last-resort fallback)

4) Run:
   python agent.py

Notes:
- This code is designed for *safe*, limited automation. It avoids destructive actions.
- For ChatGPT website, login may be required; agent will attempt but may stop if uncertain/captcha.

---------------------------------------------------------------------------------------
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import uuid
import shutil
import signal
import traceback
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Callable, List, Tuple, Literal

# --- Logging ---
import logging

# --- CDP / Playwright ---
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeoutError

# --- UI Automation ---
from pywinauto import Desktop, Application
from pywinauto.timings import TimeoutError as UIATimeoutError

# --- PyAutoGUI (fallback) ---
try:
    import pyautogui
    pyautogui.FAILSAFE = True  # Move mouse to top-left corner to abort
except Exception:
    pyautogui = None

# --- Screenshot ---
import mss
from PIL import Image
from io import BytesIO

# --- VLM (Gemini) ---
try:
    from pydantic import BaseModel, Field, ValidationError
    from google import genai
    from google.genai import types
except Exception:
    BaseModel = None  # type: ignore


# =============================================================================
# Utilities: Run directory + JSONL logger
# =============================================================================

class RunLogger:
    def __init__(self, base_dir: str = "runs"):
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_id = f"{ts}_{uuid.uuid4().hex[:8]}"
        self.dir = os.path.join(base_dir, self.run_id)
        os.makedirs(self.dir, exist_ok=True)

        self.jsonl_path = os.path.join(self.dir, "events.jsonl")
        self.log_path = os.path.join(self.dir, "run.log")
        self.screenshot_dir = os.path.join(self.dir, "screenshots")
        os.makedirs(self.screenshot_dir, exist_ok=True)

        self._setup_std_logging()

    def _setup_std_logging(self):
        self.logger = logging.getLogger("agent")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        fh = logging.FileHandler(self.log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)

        self.logger.addHandler(fh)
        self.logger.addHandler(sh)

    def event(self, kind: str, payload: Dict[str, Any]):
        record = {
            "ts": time.time(),
            "kind": kind,
            "payload": payload,
        }
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save_screenshot_png(self, png_bytes: bytes, name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", name)[:80]
        path = os.path.join(self.screenshot_dir, f"{int(time.time())}_{safe}.png")
        with open(path, "wb") as f:
            f.write(png_bytes)
        return path


# =============================================================================
# Safety guard
# =============================================================================

class SafetyStop(Exception):
    pass


class SafetyGuard:
    """
    Stops the agent when it detects dangerous situations.
    This is intentionally conservative.
    """
    DANGEROUS_WINDOW_KEYWORDS = [
        "Registry Editor",
        "レジストリ エディター",
        "Windows Security",
        "Windows セキュリティ",
        "Task Manager",
        "タスク マネージャー",
        "PowerShell",
        "コマンド プロンプト",
        "Command Prompt",
        "Firewall",
        "ファイアウォール",
        "Device Manager",
        "デバイス マネージャー",
    ]

    DISALLOWED_DOMAINS = [
        # agent use-cases allow youtube + chatgpt only (customize if needed)
    ]

    ALLOWED_DOMAINS = [
        "www.youtube.com",
        "youtube.com",
        "m.youtube.com",
        "chat.openai.com",
    ]

    def __init__(self, runlog: RunLogger):
        self.runlog = runlog

    def check_active_window_title(self, title: str):
        if not title:
            return
        for kw in self.DANGEROUS_WINDOW_KEYWORDS:
            if kw.lower() in title.lower():
                raise SafetyStop(f"Dangerous window detected: '{title}' (keyword: {kw})")

    def check_url_allowlist(self, url: str):
        if not url:
            return
        # allowlist check only for http(s)
        if url.startswith("http://") or url.startswith("https://"):
            m = re.match(r"^https?://([^/]+)", url)
            if m:
                domain = m.group(1).lower()
                if any(domain == a or domain.endswith("." + a) for a in self.ALLOWED_DOMAINS):
                    return
                # Unknown domain => stop (safe)
                raise SafetyStop(f"Unexpected domain: {domain} (url={url})")

    def check(self, facts: Dict[str, Any]):
        # UIA active window title
        title = facts.get("uia_active_title") or ""
        self.check_active_window_title(title)

        # CDP current URL
        url = facts.get("cdp_url") or ""
        if url:
            self.check_url_allowlist(url)

        # VLM danger mode
        if facts.get("vlm_mode") in ("danger", "system_settings"):
            raise SafetyStop(f"VLM flagged dangerous mode: {facts.get('vlm_mode')}")

        # Hard stop if captcha detected (automation risk + loops)
        if facts.get("vlm_mode") == "captcha":
            raise SafetyStop("Captcha detected. Stopping for safety.")


# =============================================================================
# Screen capture
# =============================================================================

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


# =============================================================================
# VLM schema + observer (Gemini structured output)
# =============================================================================

if BaseModel is not None:
    class VLMState(BaseModel):
        mode: Literal[
            "ready",
            "login",
            "error",
            "captcha",
            "notepad",
            "browser",
            "chatgpt",
            "youtube",
            "system_settings",
            "danger",
            "unknown",
        ] = "unknown"
        confidence: float = Field(default=0.0, ge=0.0, le=1.0)
        active_window_title: Optional[str] = None
        visible_error_text: Optional[str] = None
        actionable_hints: List[str] = Field(default_factory=list)
        unknowns: List[str] = Field(default_factory=list)


class GeminiVLM:
    def __init__(self, api_key: Optional[str], model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self.enabled = bool(api_key) and (BaseModel is not None)
        self.client = genai.Client(api_key=api_key) if self.enabled else None
        self.schema = VLMState.model_json_schema() if self.enabled else None

    def infer(self, png_bytes: bytes) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "mode": "unknown", "confidence": 0.0}

        image_part = types.Part.from_bytes(data=png_bytes, mime_type="image/png")
        prompt = (
            "You are a UI state classifier for a Windows PC screenshot.\n"
            "Return ONLY JSON matching the schema.\n"
            "Rules:\n"
            "- If you see a captcha / robot check -> mode='captcha'.\n"
            "- If you see Notepad -> mode='notepad'.\n"
            "- If you see YouTube page -> mode='youtube'.\n"
            "- If you see ChatGPT page -> mode='chatgpt'.\n"
            "- If you see an error dialog -> mode='error' and include visible_error_text.\n"
            "- If you see system settings / security / registry / terminal -> mode='danger' or 'system_settings'.\n"
            "- If unsure -> mode='unknown' and list unknowns.\n"
            "- confidence must reflect certainty.\n"
        )

        resp = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config={
                "response_mime_type": "application/json",
                "response_json_schema": self.schema,
            },
        )

        # Parse + validate
        try:
            st = VLMState.model_validate_json(resp.text)
            out = st.model_dump()
            out["enabled"] = True
            return out
        except Exception:
            # best effort JSON extraction
            txt = (resp.text or "").strip()
            m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
            if not m:
                return {"enabled": True, "mode": "unknown", "confidence": 0.0, "unknowns": ["parse_failed"]}
            try:
                parsed = json.loads(m.group(0))
                # validate if possible
                try:
                    st = VLMState.model_validate(parsed)
                    out = st.model_dump()
                    out["enabled"] = True
                    return out
                except Exception:
                    parsed["enabled"] = True
                    return parsed
            except Exception:
                return {"enabled": True, "mode": "unknown", "confidence": 0.0, "unknowns": ["parse_failed"]}


# =============================================================================
# Perception: multi-layer observer
# =============================================================================

@dataclass
class Observation:
    ts: float = field(default_factory=time.time)
    facts: Dict[str, Any] = field(default_factory=dict)
    confidence: Dict[str, float] = field(default_factory=dict)  # per layer


# =============================================================================
# CDP Layer (Playwright)
# =============================================================================

class CDPTools:
    def __init__(self, runlog: RunLogger, safety: SafetyGuard):
        self.runlog = runlog
        self.safety = safety
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None

    def connect(self, cdp_url: str):
        self.runlog.logger.info(f"CDP connect: {cdp_url}")
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(cdp_url)
        # Use existing context if present (CDP typically has one)
        self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)
        self.page.set_default_navigation_timeout(30000)

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.pw:
                self.pw.stop()

    # ---- Helpers ----

    def current_url(self) -> str:
        return self.page.url if self.page else ""

    def goto(self, url: str):
        self.safety.check_url_allowlist(url)
        self.page.goto(url, wait_until="domcontentloaded")

    # ---- YouTube ----

    def youtube_search_and_open_first(self, query: str) -> str:
        """
        Returns opened video title (best effort).
        """
        self.goto("https://www.youtube.com")

        # Consent popups may appear; try close if present (best effort)
        self._try_click_any([
            "button:has-text('Accept all')",
            "button:has-text('I agree')",
            "button:has-text('同意する')",
            "button:has-text('すべて同意')",
        ])

        box = self.page.locator("input#search, input[name='search_query']").first
        box.wait_for(state="visible")
        box.fill(query)
        box.press("Enter")

        # Wait results
        self.page.wait_for_url("**/results**", timeout=30000)

        # Click first video
        first = self.page.locator("ytd-video-renderer a#video-title, a#video-title").first
        first.wait_for(state="visible")
        title = first.get_attribute("title") or ""
        first.click()

        # Wait video page
        self.page.wait_for_url("**/watch**", timeout=30000)
        self.page.locator("video").first.wait_for(state="attached", timeout=30000)
        return title or "unknown"

    # ---- ChatGPT web ----

    def chatgpt_ask_and_get_answer(self, question: str) -> str:
        """
        Best-effort for chat.openai.com (web UI changes often).
        If login required, this may fail and should be handled by fallback layers.
        """
        self.goto("https://chat.openai.com/")

        # Attempt to find a textarea
        ta = self.page.locator("textarea").first
        ta.wait_for(state="visible", timeout=30000)
        ta.fill(question)

        # Send button (multiple possible selectors)
        if not self._try_click_any([
            "button[data-testid='send-button']",
            "button:has-text('Send')",
            "button[aria-label*='Send']",
            "button:has(svg[aria-label*='Send'])",
        ]):
            # fallback: Enter (some UIs allow)
            ta.press("Enter")

        # Wait for assistant message
        # Most common attribute: data-message-author-role='assistant'
        self.page.locator("div[data-message-author-role='assistant']").last.wait_for(timeout=120000)
        msg = self.page.locator("div[data-message-author-role='assistant']").last.inner_text(timeout=120000)
        return msg.strip()

    def _try_click_any(self, selectors: List[str]) -> bool:
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=3000)
                    return True
            except Exception:
                continue
        return False


# =============================================================================
# UIAutomation Layer (pywinauto)
# =============================================================================

class UIATools:
    def __init__(self, runlog: RunLogger, safety: SafetyGuard):
        self.runlog = runlog
        self.safety = safety
        self.desktop = Desktop(backend="uia")

    def active_title(self) -> str:
        try:
            w = self.desktop.get_active()
            title = w.window_text() or ""
            self.safety.check_active_window_title(title)
            return title
        except Exception:
            return ""

    def find_window(self, title_re: str, timeout_s: float = 2.0):
        end = time.time() + timeout_s
        while time.time() < end:
            wins = self.desktop.windows(title_re=title_re, visible_only=True)
            if wins:
                return wins[0]
            time.sleep(0.1)
        return None

    def detect_error_dialog(self) -> Optional[Dict[str, Any]]:
        """
        Heuristic scanning: visible windows + keyword in title or text controls.
        """
        error_keywords = ["error", "エラー", "例外", "warning", "警告", "失敗", "停止"]
        for w in self.desktop.windows(control_type="Window", visible_only=True):
            try:
                title = w.window_text() or ""
                if any(k.lower() in title.lower() for k in error_keywords):
                    msg = self._extract_static_text(w)
                    return {"title": title, "message": msg, "severity": "error"}

                msg = self._extract_static_text(w)
                if msg and any(k.lower() in msg.lower() for k in error_keywords):
                    return {"title": title or None, "message": msg, "severity": "error"}
            except Exception:
                continue
        return None

    def _extract_static_text(self, win) -> Optional[str]:
        try:
            texts = []
            for ctrl in win.descendants():
                try:
                    if ctrl.friendly_class_name() in ("Text", "Static"):
                        t = ctrl.window_text()
                        if t:
                            texts.append(t)
                except Exception:
                    pass
            msg = "\n".join(texts).strip()
            return msg if msg else None
        except Exception:
            return None

    def dismiss_common_dialog(self) -> bool:
        """
        Try clicking common dialog buttons in the active window.
        """
        try:
            w = self.desktop.get_active()
            title = w.window_text() or ""
            self.safety.check_active_window_title(title)

            for bname in ["OK", "Ok", "はい", "閉じる", "Close", "キャンセル", "Cancel"]:
                try:
                    btn = w.child_window(title=bname, control_type="Button")
                    if btn.exists(timeout=0.2) and btn.is_enabled():
                        btn.click_input()
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def launch_notepad_and_type(self, text: str) -> bool:
        """
        Launch Notepad and type text using UIA.
        """
        try:
            app = Application(backend="uia").start("notepad.exe")
            win = app.window(title_re=".*Notepad.*|.*メモ帳.*")
            win.wait("exists enabled visible ready", timeout=10)
            win.set_focus()

            # Notepad main edit control
            edit = win.child_window(control_type="Edit")
            edit.wait("exists enabled visible ready", timeout=5)
            edit.type_keys(text, with_spaces=True, pause=0.02)
            return True
        except Exception as e:
            self.runlog.logger.info(f"UIA notepad failed: {e}")
            return False


# =============================================================================
# PyAutoGUI Layer (last-resort deterministic input)
# =============================================================================

class PyAutoGUITools:
    def __init__(self, runlog: RunLogger, safety: SafetyGuard):
        self.runlog = runlog
        self.safety = safety
        self.enabled = pyautogui is not None

    def type_text(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            pyautogui.write(text, interval=0.01)
            return True
        except Exception as e:
            self.runlog.logger.info(f"PyAutoGUI type failed: {e}")
            return False

    def hotkey(self, *keys: str) -> bool:
        if not self.enabled:
            return False
        try:
            pyautogui.hotkey(*keys)
            return True
        except Exception as e:
            self.runlog.logger.info(f"PyAutoGUI hotkey failed: {e}")
            return False


# =============================================================================
# Agent: Goals / State machine
# =============================================================================

@dataclass
class Goal:
    name: str
    status: Literal["pending", "running", "done", "failed"] = "pending"
    result: Optional[Any] = None
    error: Optional[str] = None


@dataclass
class AgentState:
    goals: List[Goal] = field(default_factory=list)
    current_goal_index: int = 0
    step_count: int = 0
    max_steps: int = 120
    last_error: Optional[str] = None
    stop_reason: Optional[str] = None


# =============================================================================
# Perceive-Decide-Act Agent
# =============================================================================

class AutonomousAgent:
    def __init__(
        self,
        runlog: RunLogger,
        cdp: CDPTools,
        uia: UIATools,
        pag: PyAutoGUITools,
        capturer: ScreenCapturer,
        vlm: GeminiVLM,
        safety: SafetyGuard,
    ):
        self.runlog = runlog
        self.cdp = cdp
        self.uia = uia
        self.pag = pag
        self.capturer = capturer
        self.vlm = vlm
        self.safety = safety

        self.state = AgentState(
            goals=[
                Goal("youtube_find_interesting_video"),
                Goal("chatgpt_ask_and_get_answer"),
                Goal("notepad_type_text"),
            ]
        )

        # Use-case parameters (customize)
        self.youtube_query = "面白い 動画 おすすめ"
        self.chatgpt_question = "今週末に短時間で楽しめる面白いアイデアを3つ提案して。"
        self.notepad_text_prefix = "ChatGPTの回答:\n"

    # -------------------------
    # Perceive
    # -------------------------
    def perceive(self) -> Observation:
        obs = Observation()
        facts = {}

        # UIA: active title + error dialog
        uia_title = self.uia.active_title()
        facts["uia_active_title"] = uia_title

        err = self.uia.detect_error_dialog()
        facts["uia_has_error_dialog"] = err is not None
        if err:
            facts["uia_error_dialog"] = err

        # CDP: url if connected
        try:
            url = self.cdp.current_url()
        except Exception:
            url = ""
        facts["cdp_url"] = url

        # VLM: only if needed later, but we can sample lightly every N steps
        # Keep cost low: do it only when uncertain or every 10 steps.
        vlm_out = {"enabled": self.vlm.enabled, "mode": "unknown", "confidence": 0.0}
        if self.vlm.enabled and (self.state.step_count % 10 == 0):
            png = self.capturer.capture_png_bytes()
            shot_path = self.runlog.save_screenshot_png(png, f"perceive_step{self.state.step_count}")
            self.runlog.event("screenshot", {"path": shot_path, "reason": "periodic_perceive"})
            vlm_out = self.vlm.infer(png)

        facts["vlm_mode"] = vlm_out.get("mode", "unknown")
        facts["vlm_confidence"] = float(vlm_out.get("confidence", 0.0))
        facts["vlm_active_title"] = vlm_out.get("active_window_title")
        facts["vlm_error_text"] = vlm_out.get("visible_error_text")

        # Safety check (may raise)
        self.safety.check(facts)

        obs.facts = facts
        obs.confidence = {
            "cdp": 0.7 if facts["cdp_url"] else 0.2,
            "uia": 0.8 if facts["uia_active_title"] else 0.3,
            "vlm": float(facts["vlm_confidence"] or 0.0) if self.vlm.enabled else 0.0,
            "pag": 0.2 if self.pag.enabled else 0.0,
        }

        self.runlog.event("perceive", {"facts": facts, "confidence": obs.confidence})
        return obs

    # -------------------------
    # Decide
    # -------------------------
    def decide(self, obs: Observation) -> Callable[[], Any]:
        """
        Returns an action callable.
        Strategy:
        0) If error dialog -> recover
        1) Execute current goal with CDP preferred; fallback on failures
        2) If uncertain state -> run VLM now; if still uncertain -> safe stop
        """

        # 0) error recovery first
        if obs.facts.get("uia_has_error_dialog"):
            return self._act_recover_from_error_dialog

        # Ensure goal pointer is valid
        if self.state.current_goal_index >= len(self.state.goals):
            return self._act_noop_done

        goal = self.state.goals[self.state.current_goal_index]
        if goal.status == "pending":
            goal.status = "running"

        # Goal-specific actions
        if goal.name == "youtube_find_interesting_video":
            return self._act_goal_youtube

        if goal.name == "chatgpt_ask_and_get_answer":
            return self._act_goal_chatgpt

        if goal.name == "notepad_type_text":
            return self._act_goal_notepad

        return self._act_safe_stop_unknown_goal

    # -------------------------
    # Act (with fallback + logging)
    # -------------------------
    def act(self, action: Callable[[], Any]) -> Any:
        name = getattr(action, "__name__", "action")
        self.runlog.event("act_start", {"action": name, "step": self.state.step_count})

        try:
            result = action()
            self.runlog.event("act_end", {"action": name, "ok": True, "result": self._safe_repr(result)})
            return result
        except SafetyStop as e:
            self.state.stop_reason = str(e)
            self.runlog.event("act_end", {"action": name, "ok": False, "error": str(e), "type": "SafetyStop"})
            raise
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            self.state.last_error = err
            self.runlog.event("act_end", {"action": name, "ok": False, "error": err, "trace": traceback.format_exc()})
            raise

    def run(self):
        self.runlog.logger.info(f"Run ID: {self.runlog.run_id}")
        self.runlog.logger.info("Agent start")

        while self.state.step_count < self.state.max_steps:
            self.state.step_count += 1

            try:
                obs = self.perceive()
                action = self.decide(obs)
                self.act(action)

                # If current goal done, advance
                self._advance_goals_if_done()

                # If all goals done, stop
                if self._all_done():
                    self.runlog.logger.info("All goals completed.")
                    return

            except SafetyStop as e:
                self.runlog.logger.error(f"SAFETY STOP: {e}")
                return
            except KeyboardInterrupt:
                self.runlog.logger.error("Interrupted by user.")
                return
            except Exception as e:
                # Recovery: if action fails, try tiered fallback depending on context
                self.runlog.logger.error(f"Step failed: {e}")
                recovered = self._attempt_global_recovery()
                if not recovered:
                    self.runlog.logger.error("Recovery failed. Stopping.")
                    return

        self.runlog.logger.error("Max steps reached. Stopping.")

    # =============================================================================
    # Goal actions
    # =============================================================================

    def _act_goal_youtube(self):
        goal = self.state.goals[self.state.current_goal_index]
        self.runlog.logger.info("Goal: YouTube - search and open first video")

        # Preferred: CDP
        try:
            title = self.cdp.youtube_search_and_open_first(self.youtube_query)
            goal.status = "done"
            goal.result = {"video_title": title, "url": self.cdp.current_url()}
            return
        except Exception as e:
            self.runlog.logger.info(f"CDP YouTube failed: {e}")

        # Fallback: UIA (limited). If browser exists, just focus and stop (safe).
        # (You can implement app-specific UIA steps here.)
        try:
            w = self.uia.find_window(title_re=".*(Chrome|Edge|Firefox).*", timeout_s=1.0)
            if w:
                w.set_focus()
                # As a minimal safe fallback, use VLM to confirm we're on YouTube before typing
                if self._vlm_confirm_mode("youtube", min_conf=0.65):
                    # Use pyautogui to type in search box is risky (unknown focus). Avoid.
                    goal.status = "failed"
                    goal.error = "CDP failed and UIA fallback not implemented for YouTube safely."
                    raise RuntimeError(goal.error)
        except Exception:
            pass

        # Last resort: VLM
        if self._vlm_confirm_mode("youtube", min_conf=0.50):
            goal.status = "failed"
            goal.error = "CDP failed; on YouTube but no safe deterministic control without CDP."
            raise RuntimeError(goal.error)

        goal.status = "failed"
        goal.error = "YouTube goal failed: could not reliably reach YouTube state."
        raise RuntimeError(goal.error)

    def _act_goal_chatgpt(self):
        goal = self.state.goals[self.state.current_goal_index]
        self.runlog.logger.info("Goal: ChatGPT - ask and get answer")

        # Preferred: CDP
        try:
            ans = self.cdp.chatgpt_ask_and_get_answer(self.chatgpt_question)
            goal.status = "done"
            goal.result = {"answer": ans}
            return
        except Exception as e:
            self.runlog.logger.info(f"CDP ChatGPT failed: {e}")

        # Fallback: VLM detect captcha/login and stop safely
        if self._vlm_confirm_mode("captcha", min_conf=0.50):
            goal.status = "failed"
            goal.error = "Captcha detected on ChatGPT. Stopping for safety."
            raise SafetyStop(goal.error)

        if self._vlm_confirm_mode("login", min_conf=0.55):
            goal.status = "failed"
            goal.error = "Login required on ChatGPT UI. Not auto-typing credentials. Stopping."
            raise SafetyStop(goal.error)

        goal.status = "failed"
        goal.error = "ChatGPT goal failed: CDP could not retrieve answer."
        raise RuntimeError(goal.error)

    def _act_goal_notepad(self):
        goal = self.state.goals[self.state.current_goal_index]
        self.runlog.logger.info("Goal: Notepad - type text")

        answer = self._find_chatgpt_answer()
        text = self.notepad_text_prefix + (answer or "(回答取得に失敗)")

        # Preferred: UIA
        if self.uia.launch_notepad_and_type(text):
            goal.status = "done"
            goal.result = {"typed_chars": len(text)}
            return

        # Fallback: PyAutoGUI (only if enabled)
        if self.pag.enabled:
            # Try open notepad via Win+R (safe-ish) then type.
            # Note: This assumes Windows shell is available.
            try:
                self.pag.hotkey("win", "r")
                time.sleep(0.3)
                self.pag.type_text("notepad")
                time.sleep(0.1)
                self.pag.hotkey("enter")
                time.sleep(0.8)
                self.pag.type_text(text)
                goal.status = "done"
                goal.result = {"typed_chars": len(text), "method": "pyautogui"}
                return
            except Exception as e:
                self.runlog.logger.info(f"PyAutoGUI notepad fallback failed: {e}")

        # Last resort: VLM check notepad presence; if present but typing failed, stop
        if self._vlm_confirm_mode("notepad", min_conf=0.50):
            goal.status = "failed"
            goal.error = "Notepad is visible but typing failed. Stopping."
            raise RuntimeError(goal.error)

        goal.status = "failed"
        goal.error = "Notepad goal failed."
        raise RuntimeError(goal.error)

    # =============================================================================
    # Recovery / Safety / Helpers
    # =============================================================================

    def _act_recover_from_error_dialog(self):
        self.runlog.logger.info("Recovery: dismiss error dialog")
        ok = self.uia.dismiss_common_dialog()
        if not ok:
            # Try ESC as a safe generic dismissal
            if self.pag.enabled:
                self.pag.hotkey("esc")
                ok = True

        if not ok:
            raise RuntimeError("Could not dismiss error dialog.")
        return

    def _attempt_global_recovery(self) -> bool:
        """
        Global recovery policy:
        1) Dismiss dialogs
        2) Refresh CDP page if on allowed domain
        3) Take screenshot + VLM classify; stop if danger/captcha
        """
        # 1) dialogs
        try:
            if self.uia.detect_error_dialog():
                self.uia.dismiss_common_dialog()
                return True
        except Exception:
            pass

        # 2) CDP refresh if possible
        try:
            url = self.cdp.current_url()
            if url:
                self.safety.check_url_allowlist(url)
                self.cdp.page.reload(wait_until="domcontentloaded")  # type: ignore
                return True
        except Exception:
            pass

        # 3) VLM classification
        try:
            if self.vlm.enabled:
                png = self.capturer.capture_png_bytes()
                shot_path = self.runlog.save_screenshot_png(png, "recovery")
                self.runlog.event("screenshot", {"path": shot_path, "reason": "recovery"})
                v = self.vlm.infer(png)
                self.runlog.event("vlm_recovery", v)

                # Safety checks
                mode = v.get("mode", "unknown")
                if mode in ("danger", "system_settings", "captcha"):
                    raise SafetyStop(f"Recovery VLM detected '{mode}'.")
                # If unknown, do not keep looping forever
        except SafetyStop:
            raise
        except Exception:
            pass

        return False

    def _vlm_confirm_mode(self, mode: str, min_conf: float) -> bool:
        if not self.vlm.enabled:
            return False
        png = self.capturer.capture_png_bytes()
        shot_path = self.runlog.save_screenshot_png(png, f"vlm_check_{mode}")
        self.runlog.event("screenshot", {"path": shot_path, "reason": f"vlm_check_{mode}"})
        v = self.vlm.infer(png)
        self.runlog.event("vlm_check", v)
        return (v.get("mode") == mode) and (float(v.get("confidence") or 0.0) >= min_conf)

    def _advance_goals_if_done(self):
        # Move current_goal_index forward while goals are done
        while self.state.current_goal_index < len(self.state.goals):
            g = self.state.goals[self.state.current_goal_index]
            if g.status == "done":
                self.state.current_goal_index += 1
            else:
                break

    def _all_done(self) -> bool:
        return all(g.status == "done" for g in self.state.goals)

    def _find_chatgpt_answer(self) -> Optional[str]:
        # Get from goal results
        for g in self.state.goals:
            if g.name == "chatgpt_ask_and_get_answer" and g.status == "done" and isinstance(g.result, dict):
                return g.result.get("answer")
        return None

    def _safe_repr(self, x: Any) -> Any:
        try:
            if isinstance(x, (str, int, float, bool)) or x is None:
                return x
            return str(x)[:2000]
        except Exception:
            return "<unrepr>"


# =============================================================================
# Main
# =============================================================================

def main():
    runlog = RunLogger()
    safety = SafetyGuard(runlog)
    capturer = ScreenCapturer(monitor_index=1)

    # CDP
    cdp_url = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")
    cdp = CDPTools(runlog, safety)

    # UIA / PyAutoGUI
    uia = UIATools(runlog, safety)
    pag = PyAutoGUITools(runlog, safety)

    # VLM (optional)
    gemini_key = os.getenv("GEMINI_API_KEY")
    vlm = GeminiVLM(api_key=gemini_key, model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

    # Handle SIGTERM cleanly
    def _sigterm(_signum, _frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _sigterm)

    # Connect CDP (required for primary use-cases)
    try:
        cdp.connect(cdp_url)
    except Exception as e:
        runlog.logger.error(f"CDP connect failed: {e}")
        runlog.logger.error("Start Chrome with --remote-debugging-port=9222 and set CHROME_CDP_URL.")
        return 2

    agent = AutonomousAgent(runlog, cdp, uia, pag, capturer, vlm, safety)

    try:
        agent.run()
    finally:
        try:
            cdp.close()
        except Exception:
            pass
        runlog.logger.info("Agent finished.")
    return 0


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
    raise SystemExit(
        _run_logged_main("desktop", "autonomous_agent_complete", main, phase_name="AUTONOMOUS_AGENT_RUN")
    )

# Usage notes were removed from executable code block; keep this file importable/executable.

